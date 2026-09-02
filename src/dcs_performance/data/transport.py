"""HTTP transport for the dcs-service V1 protocol.

The History and Event endpoints return a complete CSV response stream.  The
transport therefore owns response lifetime and retry handling: a response is
handed to a consumer only for one attempt, and a consumer result is returned
only after the underlying stream has reached EOF successfully.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import http.client
import io
import json
import random
import socket
import time
from typing import Any, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

import urllib.request

from .errors import (
    DcsArgumentError,
    DcsDataIntegrityError,
    DcsIncompleteStreamError,
    DcsProtocolError,
    DcsRequestTimeoutError,
    DcsServiceError,
    DcsTransportError,
    error_from_code,
)


@dataclass(frozen=True)
class HttpResponse:
    """A fully buffered response used by JSON and lightweight endpoints."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpStreamResponse:
    """One HTTP response attempt whose body must be consumed to EOF.

    ``urllib``/``http.client`` handles HTTP framing, including chunked
    transfer encoding.  This wrapper only converts read failures into the
    data-layer's explicit incomplete-stream error and provides a text stream
    for ``csv.DictReader``.
    """

    def __init__(self, status: int, headers: Mapping[str, str], raw: Any) -> None:
        self.status = status
        self.headers = headers
        self._raw = raw
        self._bytes_read = 0
        self._closed = False
        self._eof = False

    @classmethod
    def from_http_response(cls, response: HttpResponse) -> "HttpStreamResponse":
        body = response.body
        if isinstance(body, str):
            body = body.encode("utf-8")
        if not isinstance(body, bytes):
            raise DcsProtocolError(
                "HTTP response body must be bytes",
                code="invalid_response",
            )
        return cls(response.status, response.headers, io.BytesIO(body))

    def read(self, size: int = -1) -> bytes:
        """Read bytes and fail closed if the response cannot reach EOF."""

        if self._closed:
            raise DcsIncompleteStreamError(
                "HTTP response stream was closed before it was fully read",
                code="incomplete_stream",
                context={"bytes_read": self._bytes_read},
            )
        try:
            value = (
                self._raw.read()
                if size is None or size < 0
                else self._raw.read(size)
            )
        except DcsIncompleteStreamError:
            raise
        except (
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
            ConnectionResetError,
            BrokenPipeError,
            socket.timeout,
            TimeoutError,
            URLError,
            OSError,
        ) as exc:
            raise DcsIncompleteStreamError(
                f"HTTP response stream ended incompletely: {exc}",
                code="incomplete_stream",
                context={"bytes_read": self._bytes_read},
            ) from exc

        if isinstance(value, str):
            value = value.encode("utf-8")
        elif isinstance(value, (bytearray, memoryview)):
            value = bytes(value)
        if not isinstance(value, bytes):
            raise DcsProtocolError(
                "HTTP response body must be bytes",
                code="invalid_response",
            )
        self._bytes_read += len(value)
        if size is None or size < 0 or not value:
            self._eof = True
        return value

    @property
    def reached_eof(self) -> bool:
        """Whether the underlying HTTP reader has returned its EOF marker."""

        return self._eof

    def iter_bytes(self, chunk_size: int = 64 * 1024):
        """Yield response chunks until the HTTP library reports normal EOF."""

        if (
            isinstance(chunk_size, bool)
            or not isinstance(chunk_size, int)
            or chunk_size <= 0
        ):
            raise DcsArgumentError(
                "chunk_size must be a positive integer",
                code="invalid_request",
            )
        while True:
            chunk = self.read(chunk_size)
            if not chunk:
                return
            yield chunk

    def text_stream(self) -> io.TextIOBase:
        """Return a UTF-8 text wrapper that preserves streaming reads."""

        raw = _ResponseRaw(self)
        return io.TextIOWrapper(
            io.BufferedReader(raw),
            encoding="utf-8",
            newline="",
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._raw, "close", None)
        if callable(close):
            close()


class _ResponseRaw(io.RawIOBase):
    """Adapt ``HttpStreamResponse.read`` to ``io.BufferedReader``."""

    def __init__(self, response: HttpStreamResponse) -> None:
        super().__init__()
        self._response = response

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:
        chunk = self._response.read(len(buffer))
        if not chunk:
            return 0
        buffer[: len(chunk)] = chunk
        return len(chunk)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            super().close()


_Result = TypeVar("_Result")


class DcsHttpTransport:
    """Perform GET requests with bounded retries and complete-stream checks.

    ``total_timeout_seconds`` is an optional soft client-operation budget. It
    limits connection setup, retry/backoff work, and post-operation checks,
    but it is not an operating-system-level precise interruption mechanism
    for a blocking socket read. Individual connection and read waits remain
    governed by ``timeout_seconds``.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 70,
        total_timeout_seconds: float | None = None,
        max_retries: int = 4,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        monotonic_fn: Callable[[], float] = time.monotonic,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(base_url, str):
            raise DcsArgumentError("base_url must be text", code="invalid_request")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise DcsArgumentError(
                "base_url must include an http or https scheme and host",
                code="invalid_request",
            )
        if timeout_seconds <= 0:
            raise DcsArgumentError(
                "timeout_seconds must be greater than zero",
                code="invalid_request",
            )
        if total_timeout_seconds is not None and total_timeout_seconds <= 0:
            raise DcsArgumentError(
                "total_timeout_seconds must be greater than zero or None",
                code="invalid_request",
            )
        if max_retries < 0:
            raise DcsArgumentError(
                "max_retries cannot be negative",
                code="invalid_request",
            )

        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.total_timeout_seconds = total_timeout_seconds
        self.max_retries = max_retries
        self.sleep_fn = sleep_fn
        self.random_fn = random_fn
        self.monotonic_fn = monotonic_fn
        self.opener = opener or urllib.request.urlopen

    def get(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> HttpResponse:
        """Execute a GET and return a fully buffered response."""

        url = self._build_url(path, params)
        deadline = self._new_deadline()
        retry_count = 0
        while True:
            try:
                response = self._request(url, deadline=deadline)
                if self._deadline_expired(deadline):
                    raise DcsRequestTimeoutError(
                        "HTTP request exceeded total timeout",
                        code="request_timeout",
                    )
                if response.status != 200:
                    raise _error_from_http_response(response)
                return response
            except HTTPError as exc:
                try:
                    error = _error_from_http_error(exc)
                except DcsIncompleteStreamError as incomplete:
                    error = incomplete
            except DcsIncompleteStreamError as exc:
                error = exc
            except (TimeoutError, socket.timeout) as exc:
                error = DcsRequestTimeoutError(
                    f"request timed out: {exc}",
                    code="request_timeout",
                )
            except URLError as exc:
                error = DcsTransportError(
                    f"HTTP request failed: {exc}",
                    code="network_error",
                )
            except OSError as exc:
                error = DcsTransportError(
                    f"HTTP request failed: {exc}",
                    code="network_error",
                )
            except DcsServiceError as exc:
                error = exc

            if not self._can_retry(error, retry_count, deadline):
                raise error
            self._sleep_before_retry(retry_count, deadline=deadline)
            retry_count += 1

    def get_stream(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
        consumer: Callable[[HttpStreamResponse], _Result] | None = None,
    ) -> _Result:
        """Consume one complete CSV response, retrying the whole request.

        The consumer is called once per attempt.  It must read the response to
        EOF before returning.  If a read is interrupted, its partial result is
        discarded and a fresh request with the same URL is attempted.
        """

        if not callable(consumer):
            raise DcsArgumentError(
                "get_stream requires a response consumer",
                code="invalid_request",
            )
        return self._get_stream_with_retries(path, params, consumer)

    def _request(
        self,
        url: str,
        *,
        deadline: float | None = None,
    ) -> HttpResponse:
        stream = self._open_stream(url, deadline=deadline)
        try:
            body = stream.read()
            return HttpResponse(
                status=stream.status,
                headers=stream.headers,
                body=body,
            )
        finally:
            stream.close()

    def _get_stream_with_retries(
        self,
        path: str,
        params: Mapping[str, object] | None,
        consumer: Callable[[HttpStreamResponse], _Result],
    ) -> _Result:
        url = self._build_url(path, params)
        deadline = self._new_deadline()
        retry_count = 0
        while True:
            stream: HttpStreamResponse | None = None
            try:
                stream = self._open_stream(url, deadline=deadline)
                if stream.status != 200:
                    response = HttpResponse(
                        status=stream.status,
                        headers=stream.headers,
                        body=stream.read(),
                    )
                    raise _error_from_http_response(response)
                result = consumer(stream)
                if not stream.reached_eof:
                    raise DcsIncompleteStreamError(
                        "HTTP stream consumer returned before EOF",
                        code="incomplete_stream",
                        context={"bytes_read": stream._bytes_read},
                    )
                if self._deadline_expired(deadline):
                    raise DcsRequestTimeoutError(
                        "HTTP stream exceeded total timeout",
                        code="request_timeout",
                    )
                return result
            except HTTPError as exc:
                try:
                    error = _error_from_http_error(exc)
                except DcsIncompleteStreamError as incomplete:
                    error = incomplete
            except DcsIncompleteStreamError as exc:
                error = exc
            except (
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
                ConnectionResetError,
                BrokenPipeError,
                socket.timeout,
                TimeoutError,
            ) as exc:
                error = DcsIncompleteStreamError(
                    f"HTTP response stream ended incompletely: {exc}",
                    context={"bytes_read": 0},
                )
            except URLError as exc:
                error = DcsTransportError(
                    f"HTTP request failed: {exc}",
                    code="network_error",
                )
            except OSError as exc:
                error = DcsTransportError(
                    f"HTTP request failed: {exc}",
                    code="network_error",
                )
            except DcsServiceError as exc:
                error = exc
            finally:
                if stream is not None:
                    stream.close()

            if not self._can_retry(error, retry_count, deadline):
                raise error
            self._sleep_before_retry(retry_count, deadline=deadline)
            retry_count += 1

    def _open_stream(
        self,
        url: str,
        *,
        deadline: float | None = None,
    ) -> HttpStreamResponse:
        request = Request(url, method="GET")
        timeout = self.timeout_seconds
        if deadline is not None:
            remaining = deadline - self.monotonic_fn()
            if remaining <= 0:
                raise DcsRequestTimeoutError(
                    "HTTP request exceeded total timeout",
                    code="request_timeout",
                )
            timeout = min(timeout, remaining)
        response = self.opener(request, timeout=timeout)
        if isinstance(response, HttpResponse):
            return HttpStreamResponse.from_http_response(response)

        status_value = getattr(response, "status", None)
        if status_value is None:
            status_value = response.getcode()
        status = int(status_value)
        headers = _headers_to_dict(getattr(response, "headers", None))
        return HttpStreamResponse(status=status, headers=headers, raw=response)

    def _build_url(
        self,
        path: str,
        params: Mapping[str, object] | None,
    ) -> str:
        if not isinstance(path, str) or not path:
            raise DcsArgumentError("path must be non-empty text", code="invalid_request")
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            encoded = urlencode(
                [(key, str(value)) for key, value in params.items() if value is not None]
            )
            if encoded:
                url = f"{url}?{encoded}"
        return url

    def _new_deadline(self) -> float | None:
        if self.total_timeout_seconds is None:
            return None
        return self.monotonic_fn() + self.total_timeout_seconds

    def _can_retry(
        self,
        error: DcsServiceError,
        retry_count: int,
        deadline: float | None,
    ) -> bool:
        return (
            self._should_retry(error, retry_count)
            and not self._deadline_expired(deadline)
        )

    def _should_retry(self, error: DcsServiceError, retry_count: int) -> bool:
        if retry_count >= self.max_retries:
            return False
        if isinstance(error, DcsDataIntegrityError):
            return False
        if isinstance(error, DcsTransportError):
            return True
        if error.code == "request_timeout":
            return True
        return error.code == "service_busy" and error.status_code in {429, 503}

    def _deadline_expired(self, deadline: float | None) -> bool:
        return deadline is not None and self.monotonic_fn() >= deadline

    def _sleep_before_retry(
        self,
        retry_count: int,
        *,
        deadline: float | None = None,
    ) -> None:
        delay = min(2**retry_count, 8)
        jitter = max(0.0, self.random_fn()) * 0.25
        total_delay = delay + jitter
        if deadline is not None and deadline - self.monotonic_fn() <= total_delay:
            raise DcsRequestTimeoutError(
                "HTTP retry backoff exceeded total timeout",
                code="request_timeout",
            )
        self.sleep_fn(total_delay)


def _headers_to_dict(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    if hasattr(headers, "items"):
        return {str(key): str(value) for key, value in headers.items()}
    return {}


def _error_from_http_error(error: HTTPError) -> DcsServiceError:
    try:
        body = error.read()
    except (
        http.client.IncompleteRead,
        http.client.RemoteDisconnected,
        ConnectionResetError,
        BrokenPipeError,
        socket.timeout,
        TimeoutError,
        URLError,
        OSError,
    ) as exc:
        raise DcsIncompleteStreamError(
            "HTTP error response body ended incompletely",
            status_code=error.code,
            code="incomplete_stream",
            context={"http_status": error.code},
        ) from exc
    headers = _headers_to_dict(error.headers)
    return _error_from_http_parts(error.code, headers, body)


def _error_from_http_response(response: HttpResponse) -> DcsServiceError:
    return _error_from_http_parts(response.status, response.headers, response.body)


def _error_from_http_parts(
    status: int,
    headers: Mapping[str, str],
    body: bytes,
) -> DcsServiceError:
    del headers  # The protocol error code is carried by the JSON body.
    summary = _body_summary(body)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return DcsProtocolError(
            f"HTTP {status} error response is not valid JSON",
            status_code=status,
            code="invalid_error_response",
            response_body_summary=summary,
        )

    error_payload = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error_payload, dict):
        return DcsProtocolError(
            f"HTTP {status} error response has no error object",
            status_code=status,
            code="invalid_error_response",
            response_body_summary=summary,
        )

    code = error_payload.get("code")
    message = error_payload.get("message")
    if not isinstance(code, str) or not code:
        return DcsProtocolError(
            f"HTTP {status} error response has no valid error code",
            status_code=status,
            code="invalid_error_response",
            response_body_summary=summary,
        )
    if not isinstance(message, str):
        message = f"dcs-service returned {code}"
    return error_from_code(
        status_code=status,
        code=code,
        message=message,
        response_body_summary=summary,
    )


def _body_summary(body: bytes, *, max_length: int = 512) -> str:
    text = body.decode("utf-8", errors="replace")
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."

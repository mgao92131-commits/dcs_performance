"""HTTP GET transport for the dcs-service V1 protocol."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import random
import socket
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

import urllib.request

from .errors import (
    DcsArgumentError,
    DcsDataIntegrityError,
    DcsProtocolError,
    DcsRequestTimeoutError,
    DcsServiceError,
    DcsTransportError,
    error_from_code,
)


@dataclass(frozen=True)
class HttpResponse:
    """The bounded HTTP response returned by the transport layer."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class DcsHttpTransport:
    """Perform GET requests, URL encoding, and finite retry handling."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 70,
        total_timeout_seconds: float | None = 120,
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
        """Execute one GET request and return only a successful HTTP 200."""

        url = self._build_url(path, params)
        deadline = (
            None
            if self.total_timeout_seconds is None
            else self.monotonic_fn() + self.total_timeout_seconds
        )
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
                error = _error_from_http_error(exc)
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

            if (
                not self._should_retry(error, retry_count)
                or self._deadline_expired(deadline)
            ):
                raise error
            self._sleep_before_retry(retry_count, deadline=deadline)
            retry_count += 1

    def _request(
        self,
        url: str,
        *,
        deadline: float | None = None,
    ) -> HttpResponse:
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
            return response

        try:
            status_value = getattr(response, "status", None)
            if status_value is None:
                status_value = response.getcode()
            status = int(status_value)
            headers = _headers_to_dict(getattr(response, "headers", None))
            body = response.read()
            if isinstance(body, str):
                body = body.encode("utf-8")
            if not isinstance(body, bytes):
                raise DcsProtocolError(
                    "HTTP response body must be bytes",
                    code="invalid_response",
                )
            return HttpResponse(status=status, headers=headers, body=body)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

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

    def _should_retry(self, error: DcsServiceError, retry_count: int) -> bool:
        if retry_count >= self.max_retries:
            return False
        if isinstance(error, DcsDataIntegrityError):
            return False
        if isinstance(error, DcsTransportError):
            return True
        if error.code == "request_timeout":
            return True
        return (
            error.code == "service_busy"
            and error.status_code in {429, 503}
        )

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
    except OSError:
        body = b""
    headers = _headers_to_dict(error.headers)
    return _error_from_http_parts(error.code, headers, body)


def _error_from_http_response(response: HttpResponse) -> DcsServiceError:
    return _error_from_http_parts(response.status, response.headers, response.body)


def _error_from_http_parts(
    status: int,
    headers: Mapping[str, str],
    body: bytes,
) -> DcsServiceError:
    summary = _body_summary(body)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
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

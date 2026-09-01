"""Public dcs-service client used by assessment rules."""

from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import io
import json
import random
from threading import Condition, RLock
import time
from typing import Any, Iterator

from .client import DcsDataClient
from .errors import (
    DcsArgumentError,
    DcsProtocolError,
    DcsRequestTimeoutError,
    DcsServiceError,
)
from .models import DcsEvent, HistorySample, ServiceInfo, TagInfo
from .parsers import (
    ensure_naive_datetime,
    format_timestamp,
    parse_event_csv_stream,
    parse_history_csv_stream,
    parse_timestamp,
)
from .settings import DEFAULT_DCS_SERVICE_BASE_URL
from .transport import DcsHttpTransport, HttpResponse, HttpStreamResponse


class DcsServiceClient:
    """Read complete History and Event ranges through dcs-service V1.

    ``total_timeout_seconds`` is an optional soft client-operation budget,
    disabled by default.  It covers queueing, request setup, retries,
    backoff, and completion checks, but does not guarantee precise
    interruption of a blocking low-level stream read.  Individual network
    connection and read waits are controlled by ``timeout_seconds``.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_DCS_SERVICE_BASE_URL,
        *,
        timeout_seconds: float = 70,
        total_timeout_seconds: float | None = None,
        max_retries: int = 4,
        transport: DcsHttpTransport | Any | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if total_timeout_seconds is not None and total_timeout_seconds <= 0:
            raise DcsArgumentError(
                "total_timeout_seconds must be greater than zero or None",
                code="invalid_request",
            )
        self.total_timeout_seconds = total_timeout_seconds
        self._monotonic_fn = monotonic_fn
        self._info_lock = RLock()
        self._history_gate = _ConcurrencyGate("History")
        self._event_gate = _ConcurrencyGate("Event")
        self.transport = transport or DcsHttpTransport(
            base_url,
            timeout_seconds=timeout_seconds,
            total_timeout_seconds=total_timeout_seconds,
            max_retries=max_retries,
            sleep_fn=sleep_fn,
            random_fn=random_fn,
            monotonic_fn=monotonic_fn,
        )
        self._service_info: ServiceInfo | None = None

    def health(self) -> bool:
        """Return whether the HTTP process answered ``{"status":"ok"}``."""

        payload = self._get_json("/health")
        return payload.get("status") == "ok"

    def get_info(self, *, refresh: bool = False) -> ServiceInfo:
        """Read and cache service capabilities, optionally refreshing them."""

        self._ensure_runtime_state()
        with self._info_lock:
            cached_info = getattr(self, "_service_info", None)
            if cached_info is not None and not refresh:
                self._set_concurrency_limits(cached_info)
                return cached_info

            payload = self._get_json("/api/v1/info")
            info = ServiceInfo(
                service=_required_text(payload, "service"),
                version=_required_text(payload, "version"),
                historian_server=_required_text(payload, "historianServer"),
                source_timezone=_required_text(payload, "sourceTimeZone"),
                history_max_concurrent=_positive_int(
                    payload,
                    "historyMaxConcurrent",
                ),
                event_max_concurrent=_positive_int(
                    payload,
                    "eventMaxConcurrent",
                ),
                history_stream_window_minutes=_positive_int(
                    payload,
                    "historyStreamWindowMinutes",
                ),
                event_stream_window_minutes=_positive_int(
                    payload,
                    "eventStreamWindowMinutes",
                ),
                read_only=_required_bool(payload, "readOnly"),
            )
            self._service_info = info
            self._set_concurrency_limits(info)
            return info

    def refresh_info(self) -> ServiceInfo:
        """Explicitly refresh the cached service information."""

        return self.get_info(refresh=True)

    def check_tag(self, tag: str) -> TagInfo:
        """Return the service's semantic result for one Historian TAG."""

        tag = _validate_tag(tag)
        self._ensure_info()
        payload = self._get_json("/api/v1/tag", {"tag": tag})
        returned_tag = _required_text(payload, "tag")
        if returned_tag != tag:
            raise DcsProtocolError(
                "tag response does not match the requested TAG",
                code="tag_mismatch",
            )
        status = _required_text(payload, "status")
        data_type = payload.get("dataType")
        if data_type is not None and not isinstance(data_type, str):
            raise DcsProtocolError(
                "tag dataType must be text or null",
                code="invalid_response",
            )
        return TagInfo(tag=returned_tag, status=status, data_type=data_type)

    def get_history(
        self,
        tag: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[HistorySample]:
        """Return one complete Historian TAG range."""

        tag = _validate_tag(tag)
        start_time, end_time = _validate_range(start_time, end_time)
        return self._get_history_validated(
            tag,
            start_time,
            end_time,
            deadline=self._new_deadline(),
        )

    def _get_history_validated(
        self,
        tag: str,
        start_time: datetime,
        end_time: datetime,
        *,
        deadline: float | None,
    ) -> list[HistorySample]:
        info = self._ensure_info()
        self._check_deadline(deadline, "History query")
        with self._history_gate.lease(
            deadline,
            getattr(self, "_monotonic_fn", time.monotonic),
        ):
            self._check_deadline(deadline, "History query")
            params = {
                "tag": tag,
                "from": format_timestamp(start_time),
                "to": format_timestamp(end_time),
            }

            def consume(response: HttpStreamResponse | HttpResponse):
                self._check_deadline(deadline, "History query")
                self._validate_csv_response(response, kind="History")
                self._validate_source_timezone(response.headers, info)
                returned_tag = _header(response.headers, "X-DCS-Tag")
                if returned_tag is not None and returned_tag != tag:
                    raise DcsProtocolError(
                        "History response X-DCS-Tag does not match the requested TAG",
                        code="tag_mismatch",
                        context={
                            "requested_tag": tag,
                            "returned_tag": returned_tag,
                        },
                    )
                return self._parse_csv_stream(
                    response,
                    parse_history_csv_stream,
                    kind="History",
                    deadline=deadline,
                )

            return self.transport.get_stream(
                "/api/v1/history",
                params,
                consume,
            )

    def get_histories(
        self,
        tags: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, list[HistorySample]]:
        """Read unique TAGs with at most the server's History concurrency."""

        if not isinstance(tags, list):
            raise DcsArgumentError(
                "tags must be a list of strings",
                code="invalid_request",
            )
        start_time, end_time = _validate_range(start_time, end_time)
        unique_tags: list[str] = []
        for tag in tags:
            tag = _validate_tag(tag)
            if tag not in unique_tags:
                unique_tags.append(tag)
        if not unique_tags:
            return {}

        deadline = self._new_deadline()
        info = self._ensure_info()
        self._check_deadline(deadline, "Multiple History query")
        max_workers = min(info.history_max_concurrent, len(unique_tags))
        results: dict[str, list[HistorySample]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._get_history_validated,
                    tag,
                    start_time,
                    end_time,
                    deadline=deadline,
                ): tag
                for tag in unique_tags
            }
            for future in as_completed(futures):
                tag = futures[future]
                try:
                    results[tag] = future.result()
                except DcsServiceError as exc:
                    exc.add_context(tag=tag)
                    raise
                except Exception as exc:
                    raise DcsServiceError(
                        f"History request failed for TAG {tag!r}",
                        code="multi_history_error",
                        context={"tag": tag},
                    ) from exc
        return {tag: results[tag] for tag in unique_tags}

    def get_events(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[DcsEvent]:
        """Return all events in one complete fixed range ``[start, end)``."""

        start_time, end_time = _validate_range(start_time, end_time)
        deadline = self._new_deadline()
        info = self._ensure_info()
        self._check_deadline(deadline, "Event query")
        with self._event_gate.lease(deadline, self._monotonic_fn):
            self._check_deadline(deadline, "Event query")
            params = {
                "from": format_timestamp(start_time),
                "to": format_timestamp(end_time),
            }

            def consume(response: HttpStreamResponse | HttpResponse):
                self._check_deadline(deadline, "Event query")
                self._validate_csv_response(response, kind="Event")
                self._validate_source_timezone(response.headers, info)
                events = self._parse_csv_stream(
                    response,
                    parse_event_csv_stream,
                    kind="Event",
                    deadline=deadline,
                )
                _validate_event_order(events)
                _validate_event_range(events, start_time, end_time)
                return events

            return self.transport.get_stream(
                "/api/v1/events",
                params,
                consume,
            )

    def _ensure_info(self) -> ServiceInfo:
        return self.get_info()

    def _ensure_runtime_state(self) -> None:
        """Initialize state lazily for lightweight test doubles/subclasses."""

        if not hasattr(self, "_info_lock"):
            self._info_lock = RLock()
        if not hasattr(self, "_history_gate"):
            self._history_gate = _ConcurrencyGate("History")
        if not hasattr(self, "_event_gate"):
            self._event_gate = _ConcurrencyGate("Event")

    def _set_concurrency_limits(self, info: ServiceInfo) -> None:
        self._history_gate.set_limit(info.history_max_concurrent)
        self._event_gate.set_limit(info.event_max_concurrent)

    def _new_deadline(self) -> float | None:
        total_timeout_seconds = getattr(self, "total_timeout_seconds", None)
        if total_timeout_seconds is None:
            return None
        monotonic_fn = getattr(self, "_monotonic_fn", time.monotonic)
        return monotonic_fn() + total_timeout_seconds

    def _check_deadline(self, deadline: float | None, operation: str) -> None:
        monotonic_fn = getattr(self, "_monotonic_fn", time.monotonic)
        if deadline is not None and monotonic_fn() >= deadline:
            raise DcsRequestTimeoutError(
                f"{operation} exceeded total timeout",
                code="request_timeout",
                context={"operation": operation},
            )

    def _get_json(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        response = self.transport.get(path, params)
        if response.status != 200:
            raise DcsProtocolError(
                f"{path} returned unexpected HTTP status {response.status}",
                status_code=response.status,
                code="unexpected_status",
            )
        content_type = _header(response.headers, "Content-Type")
        if content_type is not None:
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                raise DcsProtocolError(
                    f"{path} response is not application/json",
                    code="invalid_content_type",
                )
        body = response.body
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, ValueError) as exc:
            raise DcsProtocolError(
                f"{path} response is not valid JSON",
                code="invalid_json",
            ) from exc
        if not isinstance(payload, dict):
            raise DcsProtocolError(
                f"{path} response must be a JSON object",
                code="invalid_json",
            )
        return payload

    @staticmethod
    def _validate_csv_response(response: Any, *, kind: str) -> None:
        if response.status != 200:
            raise DcsProtocolError(
                f"{kind} returned unexpected HTTP status {response.status}",
                status_code=response.status,
                code="unexpected_status",
            )
        content_type = _header(response.headers, "Content-Type")
        media_type = (
            content_type.split(";", 1)[0].strip().lower()
            if content_type is not None
            else ""
        )
        if media_type != "text/csv":
            raise DcsProtocolError(
                f"{kind} response is not text/csv",
                code="invalid_content_type",
            )

    @staticmethod
    def _validate_source_timezone(
        headers: Mapping[str, str],
        info: ServiceInfo,
    ) -> None:
        actual = _header(headers, "X-DCS-Source-TimeZone")
        if actual is not None and actual != info.source_timezone:
            raise DcsProtocolError(
                "DCS source timezone changed or does not match /info",
                code="source_timezone_mismatch",
                context={
                    "expected_timezone": info.source_timezone,
                    "actual_timezone": actual,
                },
            )

    def _parse_csv_stream(
        self,
        response: HttpStreamResponse | HttpResponse,
        parser: Callable[[Iterable[str]], list[Any]],
        *,
        kind: str,
        deadline: float | None,
    ) -> list[Any]:
        text_stream = _text_stream_for_response(response, kind)
        try:
            values = parser(text_stream)
        finally:
            close = getattr(text_stream, "close", None)
            if callable(close):
                close()
        self._check_deadline(deadline, f"{kind} query")
        return values


def _validate_tag(tag: str) -> str:
    if not isinstance(tag, str) or not tag:
        raise DcsArgumentError("tag must be non-empty text", code="invalid_request")
    return tag


def _validate_range(
    start_time: datetime,
    end_time: datetime,
) -> tuple[datetime, datetime]:
    start_time = ensure_naive_datetime(start_time, field_name="start_time")
    end_time = ensure_naive_datetime(end_time, field_name="end_time")
    if end_time <= start_time:
        raise DcsArgumentError(
            "end_time must be after start_time",
            code="invalid_request",
        )
    return start_time, end_time


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _required_text(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise DcsProtocolError(
            f"JSON field {name} must be non-empty text",
            code="invalid_response",
        )
    return value


def _positive_int(payload: Mapping[str, Any], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DcsProtocolError(
            f"JSON field {name} must be a positive integer",
            code="invalid_response",
        )
    return value


def _required_bool(payload: Mapping[str, Any], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise DcsProtocolError(
            f"JSON field {name} must be boolean",
            code="invalid_response",
        )
    return value


def _text_stream_for_response(
    response: HttpStreamResponse | HttpResponse,
    kind: str,
) -> io.TextIOBase:
    text_stream = getattr(response, "text_stream", None)
    if callable(text_stream):
        return text_stream()
    body = getattr(response, "body", None)
    if isinstance(body, bytes):
        try:
            return io.StringIO(body.decode("utf-8"), newline="")
        except UnicodeDecodeError as exc:
            raise DcsProtocolError(
                f"{kind} CSV is not valid UTF-8",
                code="csv_parse_error",
            ) from exc
    raise DcsProtocolError(
        f"{kind} response does not provide a text stream",
        code="invalid_response",
    )


def _event_time_key(event: DcsEvent) -> tuple[datetime, int]:
    return _timestamp_order_key(event.timestamp, event.timestamp_raw)


def _datetime_time_key(value: datetime) -> tuple[datetime, int]:
    value = ensure_naive_datetime(value, field_name="timestamp")
    return value.replace(microsecond=0), value.microsecond * 10


def _timestamp_order_key(
    value: datetime,
    raw: str | None,
) -> tuple[datetime, int]:
    """Preserve seven-digit protocol timestamp ordering where available."""

    if raw is not None:
        parsed = parse_timestamp(raw)
        _, separator, fraction = raw.partition(".")
        fraction_value = int(fraction.ljust(7, "0")) if separator else 0
        return parsed.replace(microsecond=0), fraction_value
    value = ensure_naive_datetime(value, field_name="timestamp")
    return value.replace(microsecond=0), value.microsecond * 10


def _event_cursor_key(event: DcsEvent) -> tuple[datetime, int, int, int]:
    timestamp, fraction = _event_time_key(event)
    return timestamp, fraction, event.frac_sec, event.ord


def _validate_event_order(events: list[DcsEvent]) -> None:
    previous_key: tuple[datetime, int, int, int] | None = None
    for row_index, event in enumerate(events, start=1):
        current_key = _event_cursor_key(event)
        if previous_key is not None and current_key <= previous_key:
            raise DcsProtocolError(
                "Event records must be strictly increasing by (DateTime, FracSec, Ord)",
                code="event_order",
                context={"row": row_index},
            )
        previous_key = current_key


def _validate_event_range(
    events: list[DcsEvent],
    start_time: datetime,
    end_time: datetime,
) -> None:
    start_key = _datetime_time_key(start_time)
    end_key = _datetime_time_key(end_time)
    for row_index, event in enumerate(events, start=1):
        event_key = _event_time_key(event)
        if event_key < start_key or event_key >= end_key:
            raise DcsProtocolError(
                "Event response contains an event outside [from, to)",
                code="event_range_out_of_bounds",
                context={
                    "row": row_index,
                    "event_timestamp": event.timestamp,
                    "event_timestamp_raw": event.timestamp_raw,
                },
            )


class _ConcurrencyGate:
    """A resizable process-local limit shared by all client operations."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._condition = Condition()
        self._limit = 0
        self._active = 0

    def set_limit(self, limit: int) -> None:
        with self._condition:
            self._limit = limit
            self._condition.notify_all()

    def acquire(
        self,
        deadline: float | None,
        monotonic_fn: Callable[[], float],
    ) -> None:
        with self._condition:
            while self._limit <= 0 or self._active >= self._limit:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - monotonic_fn()
                if remaining <= 0:
                    raise DcsRequestTimeoutError(
                        f"{self.name} concurrency slot wait exceeded total timeout",
                        code="request_timeout",
                        context={"operation": f"{self.name} concurrency slot"},
                    )
                self._condition.wait(timeout=remaining)
            self._active += 1

    def release(self) -> None:
        with self._condition:
            if self._active <= 0:
                raise RuntimeError(f"{self.name} concurrency gate released empty")
            self._active -= 1
            self._condition.notify()

    @contextmanager
    def lease(
        self,
        deadline: float | None,
        monotonic_fn: Callable[[], float],
    ) -> Iterator[None]:
        self.acquire(deadline, monotonic_fn)
        try:
            yield
        finally:
            self.release()

"""Public dcs-service V1 client used by assessment rules."""

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import random
import time
from typing import Any

from .client import DcsDataClient
from .errors import (
    DcsArgumentError,
    DcsDataIntegrityError,
    DcsHistoryQueryTooLargeError,
    DcsProtocolError,
    DcsServiceError,
)
from .models import (
    DcsEvent,
    EventCursor,
    EventPage,
    HistorySample,
    ServiceInfo,
    TagInfo,
)
from .parsers import (
    ensure_naive_datetime,
    format_timestamp,
    parse_bool,
    parse_event_csv,
    parse_history_csv,
    parse_timestamp,
)
from .transport import DcsHttpTransport, HttpResponse


class DcsServiceClient:
    """Read History and Event data through the documented V1 HTTP API."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 70,
        max_retries: int = 4,
        event_page_limit: int = 1000,
        transport: DcsHttpTransport | Any | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
    ) -> None:
        if event_page_limit <= 0:
            raise DcsArgumentError(
                "event_page_limit must be greater than zero",
                code="invalid_request",
            )
        self.transport = transport or DcsHttpTransport(
            base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            sleep_fn=sleep_fn,
            random_fn=random_fn,
        )
        self.event_page_limit = event_page_limit
        self._service_info: ServiceInfo | None = None

    def health(self) -> bool:
        """Return whether the HTTP process answered ``{"status":"ok"}``."""

        payload = self._get_json("/health")
        return payload.get("status") == "ok"

    def get_info(self, *, refresh: bool = False) -> ServiceInfo:
        """Read and cache service capabilities, optionally refreshing them."""

        if self._service_info is not None and not refresh:
            return self._service_info

        payload = self._get_json("/api/v1/info")
        info = ServiceInfo(
            service=_required_text(payload, "service"),
            version=_required_text(payload, "version"),
            historian_server=_required_text(payload, "historianServer"),
            source_timezone=_required_text(payload, "sourceTimezone"),
            history_max_concurrent=_positive_int(payload, "historyMaxConcurrent"),
            event_max_concurrent=_positive_int(payload, "eventMaxConcurrent"),
            read_only=_required_bool(payload, "readOnly"),
        )
        self._service_info = info
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
        info = self._ensure_info()
        try:
            response = self.transport.get(
                "/api/v1/history",
                {
                    "tag": tag,
                    "from": format_timestamp(start_time),
                    "to": format_timestamp(end_time),
                },
            )
        except DcsHistoryQueryTooLargeError as exc:
            exc.tag = tag
            exc.start_time = start_time
            exc.end_time = end_time
            exc.add_context(
                tag=tag,
                start_time=start_time,
                end_time=end_time,
            )
            raise

        self._validate_csv_response(response, kind="History")
        self._validate_source_timezone(response.headers, info)
        returned_tag = _required_header_text(response.headers, "X-DCS-Tag")
        if returned_tag != tag:
            raise DcsProtocolError(
                "History response X-DCS-Tag does not match the requested TAG",
                code="tag_mismatch",
                context={"requested_tag": tag, "returned_tag": returned_tag},
            )
        row_count = _required_header_int(response.headers, "X-DCS-Row-Count")
        samples = parse_history_csv(response.body)
        _validate_row_count(row_count, len(samples), "History")
        return samples

    def get_histories(
        self,
        tags: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, list[HistorySample]]:
        """Read unique TAGs with at most the server's History concurrency."""

        if not isinstance(tags, list):
            raise DcsArgumentError("tags must be a list of strings", code="invalid_request")
        start_time, end_time = _validate_range(start_time, end_time)
        unique_tags: list[str] = []
        for tag in tags:
            tag = _validate_tag(tag)
            if tag not in unique_tags:
                unique_tags.append(tag)
        if not unique_tags:
            return {}

        info = self._ensure_info()
        max_workers = min(info.history_max_concurrent, len(unique_tags))
        results: dict[str, list[HistorySample]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.get_history, tag, start_time, end_time): tag
                for tag in unique_tags
            }
            for future in as_completed(futures):
                tag = futures[future]
                try:
                    results[tag] = future.result()
                except DcsServiceError as exc:
                    exc.add_context(tag=tag)
                    if isinstance(exc, DcsHistoryQueryTooLargeError):
                        exc.tag = tag
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
        """Return all events in the fixed half-open range ``[start, end)``."""

        start_time, end_time = _validate_range(start_time, end_time)
        self._ensure_info()
        page = self._get_event_range_page(
            start_time,
            end_time,
            self.event_page_limit,
        )
        source_generation = page.source_generation
        collected: list[DcsEvent] = []
        seen_cursors: set[tuple[str, int, int]] = set()

        while True:
            if page.source_generation != source_generation:
                raise DcsDataIntegrityError(
                    "Event source generation changed during a fixed-range query",
                    status_code=200,
                    code="source_changed",
                    context={
                        "expected_generation": source_generation,
                        "actual_generation": page.source_generation,
                    },
                )

            crossed_end = False
            for event in page.events:
                if event.timestamp >= end_time:
                    crossed_end = True
                if start_time <= event.timestamp < end_time:
                    collected.append(event)

            if crossed_end or not page.has_more:
                return collected

            cursor = _cursor_from_page(page)
            cursor_key = (
                cursor.datetime_raw or format_timestamp(cursor.datetime),
                cursor.frac_sec,
                cursor.ord,
            )
            if cursor_key in seen_cursors:
                raise DcsDataIntegrityError(
                    "Event cursor repeated during a fixed-range query",
                    status_code=200,
                    code="cursor_window_empty",
                )
            seen_cursors.add(cursor_key)
            page = self._get_event_cursor_page(
                cursor,
                source_generation,
                self.event_page_limit,
            )

    def _get_event_range_page(
        self,
        start_time: datetime,
        end_time: datetime,
        limit: int,
    ) -> EventPage:
        start_time, end_time = _validate_range(start_time, end_time)
        limit = _validate_limit(limit)
        info = self._ensure_info()
        response = self.transport.get(
            "/api/v1/events",
            {
                "from": format_timestamp(start_time),
                "to": format_timestamp(end_time),
                "limit": limit,
            },
        )
        return self._parse_event_page(response, info)

    def _get_event_cursor_page(
        self,
        cursor: EventCursor,
        source_generation: str,
        limit: int,
    ) -> EventPage:
        if not isinstance(cursor, EventCursor):
            raise DcsArgumentError("cursor must be an EventCursor", code="invalid_request")
        if not isinstance(source_generation, str) or not source_generation:
            raise DcsArgumentError(
                "source_generation must be non-empty text",
                code="invalid_request",
            )
        ensure_naive_datetime(cursor.datetime, field_name="cursor.datetime")
        limit = _validate_limit(limit)
        info = self._ensure_info()
        after_time = cursor.datetime_raw or format_timestamp(cursor.datetime)
        response = self.transport.get(
            "/api/v1/events",
            {
                "afterTime": after_time,
                "afterFracSec": cursor.frac_sec,
                "afterOrd": cursor.ord,
                "sourceGeneration": source_generation,
                "limit": limit,
            },
        )
        return self._parse_event_page(response, info)

    def _parse_event_page(
        self,
        response: HttpResponse,
        info: ServiceInfo,
    ) -> EventPage:
        self._validate_csv_response(response, kind="Event")
        self._validate_source_timezone(response.headers, info)
        row_count = _required_header_int(response.headers, "X-DCS-Row-Count")
        source_generation = _required_header_text(
            response.headers,
            "X-DCS-Source-Generation",
        )
        has_more = _required_header_bool(response.headers, "X-DCS-Has-More")
        events = parse_event_csv(response.body)
        _validate_row_count(row_count, len(events), "Event")

        next_datetime_raw = _header(response.headers, "X-DCS-Next-DateTime")
        next_frac_raw = _header(response.headers, "X-DCS-Next-FracSec")
        next_ord_raw = _header(response.headers, "X-DCS-Next-Ord")
        next_values = (next_datetime_raw, next_frac_raw, next_ord_raw)
        present = tuple(value is not None for value in next_values)
        if any(present) and not all(present):
            raise DcsProtocolError(
                "Event next cursor headers must be provided together",
                code="invalid_cursor_headers",
            )
        if has_more and not all(present):
            raise DcsProtocolError(
                "Event page with HasMore=true must include a next cursor",
                code="invalid_cursor_headers",
            )

        next_frac_sec: int | None = None
        next_ord: int | None = None
        if all(present):
            assert next_datetime_raw is not None
            assert next_frac_raw is not None
            assert next_ord_raw is not None
            parse_timestamp(next_datetime_raw)
            next_frac_sec = _parse_header_int(next_frac_raw, "X-DCS-Next-FracSec")
            next_ord = _parse_header_int(next_ord_raw, "X-DCS-Next-Ord")

        return EventPage(
            events=events,
            source_generation=source_generation,
            has_more=has_more,
            next_datetime_raw=next_datetime_raw,
            next_frac_sec=next_frac_sec,
            next_ord=next_ord,
        )

    def _ensure_info(self) -> ServiceInfo:
        return self.get_info()

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
        try:
            import json

            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
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
    def _validate_csv_response(response: HttpResponse, *, kind: str) -> None:
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
        if actual is None:
            raise DcsProtocolError(
                "response is missing X-DCS-Source-TimeZone",
                code="missing_header",
            )
        if actual != info.source_timezone:
            raise DcsProtocolError(
                "DCS source timezone changed or does not match /info",
                code="source_timezone_mismatch",
                context={
                    "expected_timezone": info.source_timezone,
                    "actual_timezone": actual,
                },
            )


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


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise DcsArgumentError("limit must be a positive integer", code="invalid_request")
    return limit


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _required_header_text(headers: Mapping[str, str], name: str) -> str:
    value = _header(headers, name)
    if value is None or not value:
        raise DcsProtocolError(
            f"response is missing {name}",
            code="missing_header",
        )
    return value


def _required_header_int(headers: Mapping[str, str], name: str) -> int:
    return _parse_header_int(_required_header_text(headers, name), name)


def _parse_header_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise DcsProtocolError(
            f"{name} must be an integer",
            code="invalid_header",
        ) from exc
    if parsed < 0:
        raise DcsProtocolError(
            f"{name} cannot be negative",
            code="invalid_header",
        )
    return parsed


def _required_header_bool(headers: Mapping[str, str], name: str) -> bool:
    return parse_bool(_required_header_text(headers, name), field_name=name)


def _validate_row_count(expected: int, actual: int, kind: str) -> None:
    if expected != actual:
        raise DcsProtocolError(
            f"{kind} CSV row count does not match X-DCS-Row-Count",
            code="row_count_mismatch",
            context={"expected_rows": expected, "actual_rows": actual},
        )


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


def _cursor_from_page(page: EventPage) -> EventCursor:
    if (
        page.next_datetime_raw is None
        or page.next_frac_sec is None
        or page.next_ord is None
    ):
        raise DcsProtocolError(
            "Event page has no complete next cursor",
            code="invalid_cursor_headers",
        )
    return EventCursor(
        datetime=parse_timestamp(page.next_datetime_raw),
        frac_sec=page.next_frac_sec,
        ord=page.next_ord,
        datetime_raw=page.next_datetime_raw,
    )

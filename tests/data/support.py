"""Reusable in-memory HTTP/CSV fixtures for data-layer tests."""

import csv
import io
import json
from collections.abc import Iterable

from dcs_performance.data.transport import HttpResponse


class FakeTransport:
    """Return queued HttpResponses or raise queued exceptions."""

    def __init__(self, responses: Iterable[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if not self.responses:
            raise AssertionError("FakeTransport received an unexpected request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeUrlopenResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.status = status
        self.headers = headers
        self.body = body
        self.closed = False

    def read(self) -> bytes:
        return self.body

    def close(self) -> None:
        self.closed = True


def json_response(payload: object) -> HttpResponse:
    return HttpResponse(
        status=200,
        headers={"Content-Type": "application/json; charset=utf-8"},
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


def history_response(
    csv_body: bytes,
    *,
    tag: str = "TAG1",
    rows: int = 1,
    timezone: str = "China Standard Time",
) -> HttpResponse:
    return HttpResponse(
        status=200,
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "X-DCS-Tag": tag,
            "X-DCS-Row-Count": str(rows),
            "X-DCS-Source-TimeZone": timezone,
        },
        body=csv_body,
    )


def event_response(
    csv_body: bytes,
    *,
    rows: int = 1,
    timezone: str = "China Standard Time",
    generation: str = "APP|2026-08-30T00:00:00.000",
    has_more: bool = False,
    next_datetime: str | None = None,
    next_frac_sec: int | None = None,
    next_ord: int | None = None,
) -> HttpResponse:
    headers = {
        "Content-Type": "text/csv; charset=utf-8",
        "X-DCS-Row-Count": str(rows),
        "X-DCS-Source-TimeZone": timezone,
        "X-DCS-Source-Generation": generation,
        "X-DCS-Has-More": "true" if has_more else "false",
    }
    if next_datetime is not None:
        headers["X-DCS-Next-DateTime"] = next_datetime
    if next_frac_sec is not None:
        headers["X-DCS-Next-FracSec"] = str(next_frac_sec)
    if next_ord is not None:
        headers["X-DCS-Next-Ord"] = str(next_ord)
    return HttpResponse(status=200, headers=headers, body=csv_body)


def make_csv(columns: Iterable[str], rows: Iterable[Iterable[object]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(list(columns))
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


"""Reusable in-memory HTTP/CSV fixtures for data-layer tests."""

import csv
import io
import json
from collections.abc import Iterable

from dcs_performance.data.transport import HttpResponse, HttpStreamResponse


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

    def get_stream(self, path, params=None, consumer=None):
        self.calls.append((path, dict(params or {})))
        if not self.responses:
            raise AssertionError("FakeTransport received an unexpected request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, HttpResponse):
            response = HttpStreamResponse.from_http_response(response)
        if not callable(consumer):
            raise AssertionError("FakeTransport stream call needs a consumer")
        try:
            return consumer(response)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()


class FakeUrlopenResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self._stream = io.BytesIO(body)
        self.status = status
        self.headers = headers
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def close(self) -> None:
        self.closed = True


class InterruptingUrlopenResponse:
    """Return a prefix and then emulate an incomplete HTTP body."""

    def __init__(self, body: bytes, headers: dict[str, str], split_at: int) -> None:
        self.status = 200
        self.headers = headers
        self._prefix = body[:split_at]
        self._sent_prefix = False
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if not self._sent_prefix:
            self._sent_prefix = True
            return self._prefix
        from http.client import IncompleteRead

        raise IncompleteRead(self._prefix, None)

    def close(self) -> None:
        self.closed = True


def raw_response(response: HttpResponse) -> FakeUrlopenResponse:
    return FakeUrlopenResponse(response.status, response.body, dict(response.headers))


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
    timezone: str = "China Standard Time",
) -> HttpResponse:
    return HttpResponse(
        status=200,
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "X-DCS-Tag": tag,
            "X-DCS-Source-TimeZone": timezone,
        },
        body=csv_body,
    )


def event_response(
    csv_body: bytes,
    *,
    timezone: str = "China Standard Time",
) -> HttpResponse:
    headers = {
        "Content-Type": "text/csv; charset=utf-8",
        "X-DCS-Source-TimeZone": timezone,
    }
    return HttpResponse(status=200, headers=headers, body=csv_body)


def make_csv(columns: Iterable[str], rows: Iterable[Iterable[object]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(list(columns))
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")

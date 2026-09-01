import io
from http.client import IncompleteRead
from urllib.error import HTTPError

import pytest

from dcs_performance.data.errors import (
    DcsDataIntegrityError,
    DcsIncompleteStreamError,
    DcsRequestTimeoutError,
    DcsServiceBusyError,
    DcsServiceError,
)
from dcs_performance.data.transport import DcsHttpTransport

from .support import FakeUrlopenResponse
from .support import InterruptingUrlopenResponse


class _IncompleteErrorBody:
    def read(self, *args, **kwargs):
        del args, kwargs
        raise IncompleteRead(b'{"ok":false,"error":', 100)

    def close(self):
        pass


def test_transport_url_encodes_query_and_uses_timeout():
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["timeout"] = timeout
        return FakeUrlopenResponse(200, b"ok", {})

    transport = DcsHttpTransport("http://127.0.0.1:18080", timeout_seconds=70, opener=opener)
    response = transport.get(
        "/api/v1/history",
        {"tag": "012-P01HZX/PID1/PV.CV", "from": "2026-08-30T08:00:00"},
    )

    assert response.status == 200
    assert seen == {
        "url": "http://127.0.0.1:18080/api/v1/history?tag=012-P01HZX%2FPID1%2FPV.CV&from=2026-08-30T08%3A00%3A00",
        "method": "GET",
        "timeout": 70,
    }


def test_transport_has_no_default_total_download_deadline():
    transport = DcsHttpTransport("http://service", opener=lambda request, timeout: None)

    assert transport.total_timeout_seconds is None


def test_transport_retries_service_busy_with_injected_backoff():
    responses = [
        FakeUrlopenResponse(
            429,
            b'{"ok":false,"error":{"code":"service_busy","message":"busy"}}',
            {},
        ),
        FakeUrlopenResponse(
            429,
            b'{"ok":false,"error":{"code":"service_busy","message":"busy"}}',
            {},
        ),
        FakeUrlopenResponse(200, b"ok", {}),
    ]
    sleeps = []

    def opener(request, timeout):
        return responses.pop(0)

    transport = DcsHttpTransport(
        "http://service",
        max_retries=2,
        sleep_fn=sleeps.append,
        random_fn=lambda: 0.0,
        opener=opener,
    )

    assert transport.get("/health").status == 200
    assert sleeps == [1.0, 2.0]


def test_transport_does_not_retry_invalid_request():
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        raise HTTPError(
            request.full_url,
            400,
            "bad",
            {},
            io.BytesIO(b'{"ok":false,"error":{"code":"invalid_request","message":"bad"}}'),
        )

    transport = DcsHttpTransport("http://service", max_retries=4, opener=opener)
    with pytest.raises(DcsServiceError) as caught:
        transport.get("/api/v1/history")

    assert caught.value.status_code == 400
    assert caught.value.code == "invalid_request"
    assert len(calls) == 1


def test_transport_does_not_retry_event_overflow():
    calls = []

    def opener(request, timeout):
        calls.append(1)
        return FakeUrlopenResponse(
            503,
            b'{"ok":false,"error":{"code":"event_overflow","message":"overflow"}}',
            {},
        )

    transport = DcsHttpTransport("http://service", max_retries=4, opener=opener)
    with pytest.raises(DcsDataIntegrityError) as caught:
        transport.get("/api/v1/events")

    assert caught.value.code == "event_overflow"
    assert len(calls) == 1


def test_transport_invalid_error_body_keeps_bounded_summary():
    def opener(request, timeout):
        return FakeUrlopenResponse(500, b"x" * 1000, {})

    transport = DcsHttpTransport("http://service", opener=opener)
    with pytest.raises(DcsServiceError) as caught:
        transport.get("/health")

    assert caught.value.code == "invalid_error_response"
    assert caught.value.status_code == 500
    assert len(caught.value.response_body_summary) <= 515


def test_http_error_body_incomplete_is_transport_error():
    def opener(request, timeout):
        del timeout
        raise HTTPError(
            request.full_url,
            503,
            "service unavailable",
            {},
            _IncompleteErrorBody(),
        )

    transport = DcsHttpTransport(
        "http://service",
        max_retries=0,
        opener=opener,
    )

    with pytest.raises(DcsIncompleteStreamError) as caught:
        transport.get("/health")

    assert caught.value.code == "incomplete_stream"
    assert caught.value.code != "invalid_error_response"
    assert caught.value.status_code is None


def test_incomplete_http_error_body_retries_whole_request():
    calls = []
    sleeps = []

    def opener(request, timeout):
        del timeout
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(
                request.full_url,
                503,
                "service unavailable",
                {},
                _IncompleteErrorBody(),
            )
        if len(calls) == 2:
            raise HTTPError(
                request.full_url,
                503,
                "service unavailable",
                {},
                io.BytesIO(
                    b'{"ok":false,"error":{"code":"service_busy","message":"busy"}}'
                ),
            )
        return FakeUrlopenResponse(200, b"ok", {})

    transport = DcsHttpTransport(
        "http://service",
        max_retries=2,
        sleep_fn=sleeps.append,
        random_fn=lambda: 0.0,
        opener=opener,
    )

    assert transport.get("/health").status == 200
    assert calls == [
        "http://service/health",
        "http://service/health",
        "http://service/health",
    ]
    assert sleeps == [1.0, 2.0]


def test_transport_total_timeout_stops_retry_backoff_chain():
    clock = [0.0]
    calls = []

    def opener(request, timeout):
        calls.append(timeout)
        raise HTTPError(
            request.full_url,
            429,
            "busy",
            {},
            io.BytesIO(
                b'{"ok":false,"error":{"code":"service_busy","message":"busy"}}'
            ),
        )

    def sleep(seconds):
        clock[0] += seconds

    transport = DcsHttpTransport(
        "http://service",
        total_timeout_seconds=2.5,
        max_retries=4,
        sleep_fn=sleep,
        random_fn=lambda: 0.0,
        monotonic_fn=lambda: clock[0],
        opener=opener,
    )

    with pytest.raises(DcsRequestTimeoutError) as caught:
        transport.get("/health")

    assert caught.value.code == "request_timeout"
    assert len(calls) == 2
    assert clock[0] == 1.0


def test_transport_retries_an_incomplete_stream_without_returning_partial_text():
    body = b"header\nfirst\nsecond\n"
    responses = [
        InterruptingUrlopenResponse(body, {"Content-Type": "text/csv"}, split_at=10),
        FakeUrlopenResponse(200, body, {"Content-Type": "text/csv"}),
    ]
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        return responses.pop(0)

    transport = DcsHttpTransport(
        "http://service",
        max_retries=1,
        sleep_fn=lambda _: None,
        random_fn=lambda: 0.0,
        opener=opener,
    )

    def consume(response):
        text = response.text_stream()
        try:
            return text.read()
        finally:
            text.close()

    assert transport.get_stream("/api/v1/history", {"tag": "TAG"}, consume) == body.decode()
    assert calls == [
        "http://service/api/v1/history?tag=TAG",
        "http://service/api/v1/history?tag=TAG",
    ]


def test_transport_reports_incomplete_stream_when_no_retry_remains():
    body = b"header\npartial"

    def opener(request, timeout):
        return InterruptingUrlopenResponse(
            body,
            {"Content-Type": "text/csv"},
            split_at=8,
        )

    transport = DcsHttpTransport("http://service", max_retries=0, opener=opener)

    def consume(response):
        text = response.text_stream()
        try:
            return text.read()
        finally:
            text.close()

    with pytest.raises(DcsIncompleteStreamError):
        transport.get_stream("/api/v1/events", consumer=consume)

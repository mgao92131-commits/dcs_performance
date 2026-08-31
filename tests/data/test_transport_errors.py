import io
from urllib.error import HTTPError

import pytest

from dcs_performance.data.errors import DcsDataIntegrityError, DcsServiceBusyError, DcsServiceError
from dcs_performance.data.transport import DcsHttpTransport

from .support import FakeUrlopenResponse


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


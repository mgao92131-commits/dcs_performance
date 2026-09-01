from datetime import datetime, timezone

import pytest

from dcs_performance.data.dcs_service import DcsServiceClient
from dcs_performance.data.errors import DcsArgumentError, DcsProtocolError
from dcs_performance.data.parsers import HISTORY_COLUMNS
from dcs_performance.data.transport import DcsHttpTransport, HttpResponse

from .support import (
    FakeTransport,
    InterruptingUrlopenResponse,
    history_response,
    json_response,
    make_csv,
    raw_response,
)


SERVICE_INFO = {
    "service": "DcsDataService",
    "version": "1.1.0",
    "historianServer": "APP",
    "sourceTimeZone": "China Standard Time",
    "historyMaxConcurrent": 2,
    "eventMaxConcurrent": 4,
    "historyStreamWindowMinutes": 60,
    "eventStreamWindowMinutes": 60,
    "readOnly": True,
}


def _history_body(value="12.5"):
    return make_csv(
        HISTORY_COLUMNS,
        [[
            "2026-08-30T08:00:00.0000000",
            value,
            "Float",
            "Good",
            "HistoryDataIsValid",
            "1",
            "false",
            "false",
            "false",
            "false",
        ]],
    )


def test_health_parses_json_without_claiming_data_source_availability():
    client = DcsServiceClient(
        "http://service",
        transport=FakeTransport([json_response({"status": "ok"})]),
    )

    assert client.health() is True


def test_get_info_parses_stream_window_capabilities():
    client = DcsServiceClient(
        "http://service",
        transport=FakeTransport([json_response(SERVICE_INFO)]),
    )

    info = client.get_info()

    assert info.source_timezone == "China Standard Time"
    assert info.history_stream_window_minutes == 60
    assert info.event_stream_window_minutes == 60


def test_get_history_uses_one_complete_range_request_without_row_count_header():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            history_response(_history_body(), tag="TAG/1"),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport)

    samples = client.get_history(
        "TAG/1",
        datetime(2026, 8, 30, 8, 0),
        datetime(2026, 8, 30, 9, 0),
    )

    assert samples[0].value == "12.5"
    assert transport.calls == [
        ("/api/v1/info", {}),
        (
            "/api/v1/history",
            {
                "tag": "TAG/1",
                "from": "2026-08-30T08:00:00",
                "to": "2026-08-30T09:00:00",
            },
        ),
    ]


def test_get_history_does_not_require_optional_response_headers():
    response = HttpResponse(
        status=200,
        headers={"Content-Type": "text/csv; charset=utf-8"},
        body=_history_body(),
    )
    client = DcsServiceClient(
        "http://service",
        transport=FakeTransport([json_response(SERVICE_INFO), response]),
    )

    assert len(
        client.get_history(
            "TAG1",
            datetime(2026, 8, 30, 8),
            datetime(2026, 8, 30, 9),
        )
    ) == 1


def test_get_info_is_cached_and_can_be_refreshed():
    refreshed = {**SERVICE_INFO, "version": "1.1.1"}
    transport = FakeTransport([json_response(SERVICE_INFO), json_response(refreshed)])
    client = DcsServiceClient("http://service", transport=transport)

    assert client.get_info().version == "1.1.0"
    assert client.get_info().version == "1.1.0"
    assert client.refresh_info().version == "1.1.1"
    assert [call[0] for call in transport.calls] == [
        "/api/v1/info",
        "/api/v1/info",
    ]


def test_check_tag_preserves_unknown_semantics_and_nullable_data_type():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            json_response({"tag": "UNKNOWN", "status": "HistoryTagUnknown"}),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport)

    tag_info = client.check_tag("UNKNOWN")

    assert tag_info.tag == "UNKNOWN"
    assert tag_info.status == "HistoryTagUnknown"
    assert tag_info.data_type is None


def test_history_rejects_timezone_mismatch():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            history_response(_history_body(), tag="TAG1", timezone="UTC"),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport)

    with pytest.raises(DcsProtocolError, match="timezone"):
        client.get_history("TAG1", datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))


def test_history_rejects_response_tag_mismatch():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            history_response(_history_body(), tag="OTHER"),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport)

    with pytest.raises(DcsProtocolError, match="TAG"):
        client.get_history("TAG1", datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))


def test_history_long_range_is_not_split_by_client():
    body = make_csv(HISTORY_COLUMNS, [])
    transport = FakeTransport(
        [json_response(SERVICE_INFO), history_response(body, tag="TAG1")]
    )
    client = DcsServiceClient("http://service", transport=transport)
    start = datetime(2026, 8, 30)
    end = datetime(2026, 8, 30, 12)

    assert client.get_history("TAG1", start, end) == []
    assert transport.calls[-1] == (
        "/api/v1/history",
        {"tag": "TAG1", "from": "2026-08-30T00:00:00", "to": "2026-08-30T12:00:00"},
    )
    assert len([call for call in transport.calls if call[0] == "/api/v1/history"]) == 1


def test_get_history_rejects_aware_datetime_before_network_access():
    client = DcsServiceClient("http://service", transport=FakeTransport([]))

    with pytest.raises(DcsArgumentError):
        client.get_history(
            "TAG1",
            datetime(2026, 8, 30, 8, tzinfo=timezone.utc),
            datetime(2026, 8, 30, 9),
        )


def test_history_stream_interruption_discards_partial_samples_and_retries_whole_range():
    body = _history_body()
    response = history_response(body, tag="TAG1")
    responses = [
        raw_response(json_response(SERVICE_INFO)),
        InterruptingUrlopenResponse(
            body,
            dict(response.headers),
            split_at=len(body) // 2,
        ),
        raw_response(response),
    ]
    opened_urls = []

    def opener(request, timeout):
        opened_urls.append(request.full_url)
        return responses.pop(0)

    transport = DcsHttpTransport(
        "http://service",
        max_retries=1,
        sleep_fn=lambda _: None,
        random_fn=lambda: 0.0,
        opener=opener,
    )
    client = DcsServiceClient("http://service", transport=transport)

    samples = client.get_history(
        "TAG1",
        datetime(2026, 8, 30, 8),
        datetime(2026, 8, 30, 9),
    )

    assert [sample.value for sample in samples] == ["12.5"]
    assert opened_urls[1] == opened_urls[2]

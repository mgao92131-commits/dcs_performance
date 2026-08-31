from datetime import datetime

import pytest

from dcs_performance.data.dcs_service import DcsServiceClient
from dcs_performance.data.errors import (
    DcsHistoryQueryTooLargeError,
    DcsProtocolError,
)
from dcs_performance.data.parsers import HISTORY_COLUMNS

from .support import FakeTransport, history_response, json_response, make_csv


SERVICE_INFO = {
    "service": "DcsDataService",
    "version": "1.1.0",
    "historianServer": "APP",
    "sourceTimezone": "China Standard Time",
    "historyMaxConcurrent": 2,
    "eventMaxConcurrent": 4,
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


def test_get_history_calls_info_first_and_validates_request_headers():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            history_response(_history_body(), tag="TAG/1", rows=1),
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


def test_get_info_is_cached_and_can_be_refreshed():
    refreshed = {**SERVICE_INFO, "version": "1.1.1"}
    transport = FakeTransport([json_response(SERVICE_INFO), json_response(refreshed)])
    client = DcsServiceClient("http://service", transport=transport)

    assert client.get_info().version == "1.1.0"
    assert client.get_info().version == "1.1.0"
    assert client.refresh_info().version == "1.1.1"
    assert [call[0] for call in transport.calls] == ["/api/v1/info", "/api/v1/info"]


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


def test_history_rejects_timezone_mismatch_and_row_count_mismatch():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            history_response(_history_body(), tag="TAG1", rows=2),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport)
    with pytest.raises(DcsProtocolError, match="row count"):
        client.get_history("TAG1", datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))

    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            history_response(_history_body(), tag="TAG1", rows=1, timezone="UTC"),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport)
    with pytest.raises(DcsProtocolError, match="timezone"):
        client.get_history("TAG1", datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))


def test_history_rejects_response_tag_mismatch():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            history_response(_history_body(), tag="OTHER", rows=1),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport)

    with pytest.raises(DcsProtocolError, match="TAG"):
        client.get_history("TAG1", datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))


def test_history_too_large_is_not_retried_and_keeps_query_context():
    error = DcsHistoryQueryTooLargeError(
        "query too large",
        status_code=413,
        code="history_query_too_large",
    )
    transport = FakeTransport([json_response(SERVICE_INFO), error])
    client = DcsServiceClient("http://service", transport=transport)
    start = datetime(2026, 8, 30, 8)
    end = datetime(2026, 8, 30, 9)

    with pytest.raises(DcsHistoryQueryTooLargeError) as caught:
        client.get_history("TAG1", start, end)

    assert caught.value.tag == "TAG1"
    assert caught.value.start_time == start
    assert caught.value.end_time == end
    assert caught.value.context["tag"] == "TAG1"
    assert len(transport.calls) == 2

from datetime import datetime

import pytest

from dcs_performance.data.dcs_service import DcsServiceClient
from dcs_performance.data.errors import DcsIncompleteStreamError, DcsProtocolError
from dcs_performance.data.parsers import EVENT_COLUMNS
from dcs_performance.data.transport import DcsHttpTransport

from .support import (
    FakeTransport,
    event_response,
    json_response,
    make_csv,
    raw_response,
    InterruptingUrlopenResponse,
)
from .test_history_client import SERVICE_INFO


def _rows(*timestamps):
    return make_csv(
        EVENT_COLUMNS,
        [
            [
                timestamp,
                frac_sec,
                ordinal,
                "Alarm",
                "High",
                "Process",
                "Area",
                "Node",
                "Unit",
                "Module",
                "Description",
                "Attribute",
                "Active",
                "HI",
                "Desc1",
                "Desc2",
                "false",
            ]
            for timestamp, frac_sec, ordinal in timestamps
        ],
    )


def test_event_range_is_one_request_and_returns_all_rows():
    body = _rows(
        ("2026-08-30T08:01:00.000", 1, 1),
        ("2026-08-30T08:02:00.000", 2, 2),
    )
    transport = FakeTransport([json_response(SERVICE_INFO), event_response(body)])
    client = DcsServiceClient("http://service", transport=transport)

    events = client.get_events(datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))

    assert [(event.frac_sec, event.ord) for event in events] == [(1, 1), (2, 2)]
    assert len([call for call in transport.calls if call[0] == "/api/v1/events"]) == 1
    assert "limit" not in transport.calls[-1][1]


@pytest.mark.parametrize(
    "timestamp",
    ["2026-08-30T07:59:00.000", "2026-08-30T09:00:00.000"],
)
def test_event_range_rejects_events_outside_half_open_range(timestamp):
    transport = FakeTransport(
        [json_response(SERVICE_INFO), event_response(_rows((timestamp, 1, 1)))]
    )
    client = DcsServiceClient("http://service", transport=transport)

    with pytest.raises(DcsProtocolError) as caught:
        client.get_events(datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))

    assert caught.value.code == "event_range_out_of_bounds"


def test_event_range_rejects_non_monotonic_date_time_frac_sec_ord():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            event_response(
                _rows(
                    ("2026-08-30T08:02:00.000", 2, 2),
                    ("2026-08-30T08:01:00.000", 1, 1),
                )
            ),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport)

    with pytest.raises(DcsProtocolError) as caught:
        client.get_events(datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))

    assert caught.value.code == "event_order"


def test_event_stream_interruption_discards_partial_rows_and_retries_whole_range():
    body = _rows(
        ("2026-08-30T08:01:00.000", 1, 1),
        ("2026-08-30T08:02:00.000", 2, 2),
    )
    info_response = raw_response(
        json_response(SERVICE_INFO)
    )
    csv_response = event_response(body)
    interrupted = InterruptingUrlopenResponse(
        body,
        dict(csv_response.headers),
        split_at=len(body) // 2,
    )
    successful = raw_response(csv_response)
    opened_urls = []
    responses = [info_response, interrupted, successful]

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

    events = client.get_events(datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))

    assert len(events) == 2
    assert len(opened_urls) == 3
    assert opened_urls[1] == opened_urls[2]
    assert "limit" not in opened_urls[1]


def test_event_stream_incompleteness_is_exposed_when_retries_are_exhausted():
    body = _rows(("2026-08-30T08:01:00.000", 1, 1))
    info_response = raw_response(json_response(SERVICE_INFO))
    csv_response = event_response(body)
    responses = [
        info_response,
        InterruptingUrlopenResponse(body, dict(csv_response.headers), split_at=len(body) // 2),
    ]

    def opener(request, timeout):
        return responses.pop(0)

    transport = DcsHttpTransport(
        "http://service",
        max_retries=0,
        opener=opener,
    )
    client = DcsServiceClient("http://service", transport=transport)

    with pytest.raises(DcsIncompleteStreamError) as caught:
        client.get_events(datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))

    assert caught.value.code == "incomplete_stream"

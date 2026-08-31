from datetime import datetime

import pytest

from dcs_performance.data.dcs_service import DcsServiceClient
from dcs_performance.data.errors import DcsDataIntegrityError
from dcs_performance.data.parsers import EVENT_COLUMNS

from .support import FakeTransport, event_response, json_response, make_csv
from .test_history_client import SERVICE_INFO


GENERATION = "APP|2026-08-30T00:00:00.000"


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


def test_event_pagination_uses_complete_cursor_and_stops_at_fixed_end():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            event_response(
                _rows(("2026-08-30T19:59:00.000", 10, 20)),
                rows=1,
                generation=GENERATION,
                has_more=True,
                next_datetime="2026-08-30T19:59:00.000",
                next_frac_sec=10,
                next_ord=20,
            ),
            event_response(
                _rows(
                    ("2026-08-30T20:00:00.000", 11, 21),
                    ("2026-08-30T20:01:00.000", 12, 22),
                ),
                rows=2,
                generation=GENERATION,
                has_more=False,
            ),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport)

    events = client.get_events(
        datetime(2026, 8, 30, 8),
        datetime(2026, 8, 30, 20),
    )

    assert [event.timestamp.strftime("%H:%M") for event in events] == ["19:59"]
    assert transport.calls[2] == (
        "/api/v1/events",
        {
            "afterTime": "2026-08-30T19:59:00.000",
            "afterFracSec": 10,
            "afterOrd": 20,
            "sourceGeneration": GENERATION,
            "limit": 1000,
        },
    )
    assert "from" not in transport.calls[2][1]
    assert "to" not in transport.calls[2][1]


def test_event_pagination_merges_all_in_range_pages_until_has_more_false():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            event_response(
                _rows(("2026-08-30T08:01:00.000", 1, 1)),
                generation=GENERATION,
                has_more=True,
                next_datetime="2026-08-30T08:01:00.000",
                next_frac_sec=1,
                next_ord=1,
            ),
            event_response(
                _rows(("2026-08-30T08:02:00.000", 2, 2)),
                generation=GENERATION,
                has_more=True,
                next_datetime="2026-08-30T08:02:00.000",
                next_frac_sec=2,
                next_ord=2,
            ),
            event_response(
                _rows(("2026-08-30T08:03:00.000", 3, 3)),
                generation=GENERATION,
                has_more=False,
            ),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport)

    events = client.get_events(
        datetime(2026, 8, 30, 8),
        datetime(2026, 8, 30, 9),
    )

    assert [(event.frac_sec, event.ord) for event in events] == [(1, 1), (2, 2), (3, 3)]
    assert len(transport.calls) == 4


def test_event_generation_change_fails_closed_without_returning_partial_events():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            event_response(
                _rows(("2026-08-30T08:01:00.000", 1, 1)),
                generation="A",
                has_more=True,
                next_datetime="2026-08-30T08:01:00.000",
                next_frac_sec=1,
                next_ord=1,
            ),
            event_response(
                _rows(("2026-08-30T08:02:00.000", 2, 2)),
                generation="B",
                has_more=False,
            ),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport)

    with pytest.raises(DcsDataIntegrityError) as caught:
        client.get_events(datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))

    assert caught.value.code == "source_changed"
    assert caught.value.context == {"expected_generation": "A", "actual_generation": "B"}


def test_event_page_requires_all_next_cursor_headers_when_has_more():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            event_response(
                _rows(("2026-08-30T08:01:00.000", 1, 1)),
                generation=GENERATION,
                has_more=True,
                next_datetime="2026-08-30T08:01:00.000",
            ),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport)
    with pytest.raises(Exception, match="cursor"):
        client.get_events(datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))


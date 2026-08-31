from datetime import datetime, timezone
import threading
import time

import pytest

from dcs_performance.data.dcs_service import DcsServiceClient
from dcs_performance.data.errors import DcsArgumentError, DcsDataIntegrityError
from dcs_performance.data.parsers import EVENT_COLUMNS

from .support import FakeTransport, event_response, json_response, make_csv
from .test_history_client import SERVICE_INFO


def _event_body(timestamp="2026-08-30T08:30:00.123", frac_sec=123, ordinal=1):
    return make_csv(
        EVENT_COLUMNS,
        [[
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
            "Module description",
            "Attribute",
            "Active",
            "HI",
            "Description 1",
            "Description 2",
            "false",
        ]],
    )


def test_get_events_returns_raw_events_for_fixed_half_open_range():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            event_response(_event_body(), rows=1, has_more=False),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport, event_page_limit=50)

    events = client.get_events(
        datetime(2026, 8, 30, 8),
        datetime(2026, 8, 30, 9),
    )

    assert len(events) == 1
    assert events[0].event_type == "Alarm"
    assert transport.calls[1] == (
        "/api/v1/events",
        {
            "from": "2026-08-30T08:00:00",
            "to": "2026-08-30T09:00:00",
            "limit": 50,
        },
    )


def test_get_events_rejects_aware_datetime_before_network_access():
    client = DcsServiceClient("http://service", transport=FakeTransport([]))
    with pytest.raises(DcsArgumentError):
        client.get_events(
            datetime(2026, 8, 30, 8, tzinfo=timezone.utc),
            datetime(2026, 8, 30, 9),
        )


def test_event_integrity_error_is_not_converted_to_empty_result():
    transport = FakeTransport(
        [
            json_response(SERVICE_INFO),
            DcsDataIntegrityError(
                "retention gap",
                status_code=409,
                code="retention_gap",
            ),
        ]
    )
    client = DcsServiceClient("http://service", transport=transport)

    with pytest.raises(DcsDataIntegrityError) as caught:
        client.get_events(datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))

    assert caught.value.code == "retention_gap"


class ConcurrentEventTransport:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def get(self, path, params=None):
        if path == "/api/v1/info":
            return json_response({**SERVICE_INFO, "eventMaxConcurrent": 1})
        assert path == "/api/v1/events"
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.01)
            return event_response(_event_body(), rows=1, has_more=False)
        finally:
            with self.lock:
                self.active -= 1


def test_event_queries_use_one_shared_client_level_limit():
    transport = ConcurrentEventTransport()
    client = DcsServiceClient("http://service", transport=transport)
    barrier = threading.Barrier(2)
    failures = []

    def run():
        try:
            barrier.wait()
            client.get_events(datetime(2026, 8, 30, 8), datetime(2026, 8, 30, 9))
        except BaseException as exc:  # pragma: no cover - assertion aid
            failures.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert transport.max_active <= 1

from datetime import datetime
import threading
import time

import pytest

from dcs_performance.data.dcs_service import DcsServiceClient
from dcs_performance.data.errors import DcsServiceError
from dcs_performance.data.models import HistorySample, ServiceInfo


class ConcurrentFakeClient(DcsServiceClient):
    def __init__(self, *, failure_tag=None):
        self._service_info = ServiceInfo(
            service="DcsDataService",
            version="1.1.0",
            historian_server="APP",
            source_timezone="China Standard Time",
            history_max_concurrent=2,
            event_max_concurrent=4,
            read_only=True,
        )
        self.failure_tag = failure_tag
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def get_history(self, tag, start_time, end_time):
        if tag == self.failure_tag:
            raise DcsServiceError(
                "history failed",
                status_code=503,
                code="historian_unavailable",
            )
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.01)
        with self.lock:
            self.active -= 1
        return []


def test_get_histories_deduplicates_tags_and_respects_info_concurrency():
    client = ConcurrentFakeClient()

    result = client.get_histories(
        ["TAG1", "TAG2", "TAG1", "TAG3", "TAG4"],
        datetime(2026, 8, 30, 8),
        datetime(2026, 8, 30, 9),
    )

    assert list(result) == ["TAG1", "TAG2", "TAG3", "TAG4"]
    assert all(isinstance(values, list) for values in result.values())
    assert client.max_active <= 2


def test_get_histories_does_not_hide_a_failed_tag():
    client = ConcurrentFakeClient(failure_tag="TAG2")

    with pytest.raises(DcsServiceError) as caught:
        client.get_histories(
            ["TAG1", "TAG2"],
            datetime(2026, 8, 30, 8),
            datetime(2026, 8, 30, 9),
        )

    assert caught.value.context["tag"] == "TAG2"


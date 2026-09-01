from datetime import datetime
import threading
import time

import pytest

from dcs_performance.data.dcs_service import DcsServiceClient
from dcs_performance.data.errors import DcsServiceError
from dcs_performance.data.parsers import HISTORY_COLUMNS
from dcs_performance.data.transport import HttpStreamResponse

from .support import history_response, json_response, make_csv


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


def _history_body(tag: str) -> bytes:
    return make_csv(
        HISTORY_COLUMNS,
        [[
            "2026-08-30T08:00:00.000",
            tag,
            "String",
            "Good",
            "HistoryDataIsValid",
            "1",
            "false",
            "false",
            "false",
            "false",
        ]],
    )


class ConcurrentHistoryTransport:
    def __init__(self, *, failure_tag=None):
        self.failure_tag = failure_tag
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def get(self, path, params=None):
        params = dict(params or {})
        if path == "/api/v1/info":
            return json_response(SERVICE_INFO)
        assert path == "/api/v1/history"
        tag = params["tag"]
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if tag == self.failure_tag:
                raise DcsServiceError(
                    "history failed",
                    status_code=503,
                    code="historian_unavailable",
                )
            time.sleep(0.01)
            return history_response(_history_body(tag), tag=tag)
        finally:
            with self.lock:
                self.active -= 1

    def get_stream(self, path, params=None, consumer=None):
        assert path == "/api/v1/history"
        params = dict(params or {})
        tag = params["tag"]
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if tag == self.failure_tag:
                raise DcsServiceError(
                    "history failed",
                    status_code=503,
                    code="historian_unavailable",
                )
            time.sleep(0.01)
            response = HttpStreamResponse.from_http_response(
                history_response(_history_body(tag), tag=tag)
            )
            try:
                return consumer(response)
            finally:
                response.close()
        finally:
            with self.lock:
                self.active -= 1


def test_get_histories_deduplicates_tags_and_respects_info_concurrency():
    transport = ConcurrentHistoryTransport()
    client = DcsServiceClient("http://service", transport=transport)

    result = client.get_histories(
        ["TAG1", "TAG2", "TAG1", "TAG3", "TAG4"],
        datetime(2026, 8, 30, 8),
        datetime(2026, 8, 30, 9),
    )

    assert list(result) == ["TAG1", "TAG2", "TAG3", "TAG4"]
    assert all(isinstance(values, list) for values in result.values())
    assert transport.max_active <= 2


def test_get_histories_uses_one_shared_limit_across_concurrent_calls():
    transport = ConcurrentHistoryTransport()
    client = DcsServiceClient("http://service", transport=transport)
    barrier = threading.Barrier(2)
    failures = []

    def run(tags):
        try:
            barrier.wait()
            client.get_histories(
                tags,
                datetime(2026, 8, 30, 8),
                datetime(2026, 8, 30, 9),
            )
        except BaseException as exc:  # pragma: no cover - assertion aid
            failures.append(exc)

    threads = [
        threading.Thread(target=run, args=(["TAG1", "TAG2"],)),
        threading.Thread(target=run, args=(["TAG3", "TAG4"],)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert transport.max_active <= 2


def test_get_histories_does_not_hide_a_failed_tag():
    client = DcsServiceClient(
        "http://service",
        transport=ConcurrentHistoryTransport(failure_tag="TAG2"),
    )

    with pytest.raises(DcsServiceError) as caught:
        client.get_histories(
            ["TAG1", "TAG2"],
            datetime(2026, 8, 30, 8),
            datetime(2026, 8, 30, 9),
        )

    assert caught.value.context["tag"] == "TAG2"

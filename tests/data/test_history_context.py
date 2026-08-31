from datetime import datetime, timedelta

import pytest

from dcs_performance.data.history_context import (
    DEFAULT_LOOKBACK_STEPS,
    get_history_with_previous_sample,
)
from dcs_performance.data.models import HistorySample


def sample(timestamp: datetime, value: str, sequence_no: int = 1) -> HistorySample:
    return HistorySample(
        timestamp=timestamp,
        value=value,
        data_type="Digital",
        delta_v_status="Good",
        archive_status="HistoryDataIsValid",
        sequence_no=sequence_no,
        is_history_hole=False,
        is_cr_hole=False,
        is_manually_deleted=False,
        is_manually_inserted=False,
    )


class FakeDataClient:
    def __init__(self, responses: list[list[HistorySample]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, datetime, datetime]] = []

    def get_history(self, tag, start_time, end_time):
        self.calls.append((tag, start_time, end_time))
        if not self.responses:
            raise AssertionError("unexpected get_history call")
        return self.responses.pop(0)


START = datetime(2026, 8, 31, 8, 0)
END = datetime(2026, 8, 31, 9, 0)


def test_context_keeps_previous_sample_found_in_initial_query():
    previous = sample(START - timedelta(minutes=1), "0")
    current = sample(START + timedelta(minutes=10), "1")
    client = FakeDataClient([[current, previous]])

    result = get_history_with_previous_sample(client, "TAG1", START, END)

    assert result == [previous, current]
    assert len(client.calls) == 1


def test_context_finds_previous_sample_in_30_minute_lookback():
    previous = sample(START - timedelta(minutes=20), "1")
    current = sample(START + timedelta(minutes=10), "0")
    client = FakeDataClient([[current], [previous]])

    result = get_history_with_previous_sample(client, "TAG1", START, END)

    assert result == [previous, current]
    # The second response is the first lookback response.  No unnecessary
    # broader query is made after a previous state is found.
    assert [call[1:] for call in client.calls] == [
        (START, END),
        (START - timedelta(minutes=30), START),
    ]


def test_context_uses_two_hour_lookback_when_30_minutes_has_no_state():
    previous = sample(START - timedelta(hours=1), "0")
    client = FakeDataClient([[], [], [previous]])

    result = get_history_with_previous_sample(client, "TAG1", START, END)

    assert result == [previous]
    assert client.calls[-1][1:] == (START - timedelta(hours=2), START)


def test_context_checks_all_default_lookbacks_and_returns_no_fake_zero():
    client = FakeDataClient([[], [], [], [], []])

    result = get_history_with_previous_sample(client, "TAG1", START, END)

    assert result == []
    assert len(client.calls) == 1 + len(DEFAULT_LOOKBACK_STEPS)


def test_context_deduplicates_overlapping_samples_by_timestamp_and_sequence():
    previous = sample(START - timedelta(minutes=5), "0", sequence_no=3)
    duplicate = sample(START + timedelta(minutes=5), "1", sequence_no=4)
    same_timestamp_different_sequence = sample(
        START + timedelta(minutes=5), "1", sequence_no=5
    )
    client = FakeDataClient([
        [duplicate],
        [previous, duplicate, same_timestamp_different_sequence],
    ])

    result = get_history_with_previous_sample(client, "TAG1", START, END)

    assert result == [previous, duplicate, same_timestamp_different_sequence]


def test_context_sorts_result_and_selects_latest_previous_sample():
    old_previous = sample(START - timedelta(hours=1), "0", sequence_no=1)
    latest_previous = sample(START - timedelta(minutes=1), "1", sequence_no=2)
    current_late = sample(START + timedelta(minutes=20), "0", sequence_no=2)
    current_early = sample(START + timedelta(minutes=2), "1", sequence_no=1)
    client = FakeDataClient([[current_late, old_previous, current_early, latest_previous]])

    result = get_history_with_previous_sample(client, "TAG1", START, END)

    assert result == [latest_previous, current_early, current_late]


def test_context_rejects_invalid_ranges_and_missing_client():
    with pytest.raises(ValueError, match="must not be None"):
        get_history_with_previous_sample(None, "TAG1", START, END)

    with pytest.raises(ValueError, match="end_time must be after"):
        get_history_with_previous_sample(FakeDataClient([[]]), "TAG1", END, START)

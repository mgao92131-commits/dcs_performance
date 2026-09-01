from datetime import datetime, timedelta

import pytest

from dcs_performance.data.history_context import (
    DEFAULT_FORWARD_SEARCH_STEPS,
    DEFAULT_LOOKBACK_STEPS,
    find_next_sample,
    get_histories_with_previous_samples,
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
    assert client.calls[-1][1:] == (
        START - timedelta(hours=2),
        START - timedelta(minutes=30),
    )


def test_context_checks_all_default_lookbacks_and_returns_no_fake_zero():
    client = FakeDataClient([[], [], [], [], [], []])

    result = get_history_with_previous_sample(client, "TAG1", START, END)

    assert result == []
    assert len(client.calls) == 1 + len(DEFAULT_LOOKBACK_STEPS)


def test_context_does_not_split_reverse_lookback_ranges_at_a_24_hour_limit():
    client = FakeDataClient([[] for _ in range(6)])

    assert get_history_with_previous_sample(client, "TAG1", START, END) == []
    expected_ranges = [
        (START, END),
        (START - timedelta(minutes=30), START),
        (START - timedelta(hours=2), START - timedelta(minutes=30)),
        (START - timedelta(hours=12), START - timedelta(hours=2)),
        (START - timedelta(hours=48), START - timedelta(hours=12)),
    ]
    assert [call[1:] for call in client.calls] == expected_ranges


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


def test_find_next_sample_finds_target_in_first_chunk():
    target = sample(START + timedelta(minutes=20), "0")
    client = FakeDataClient([[target]])

    result = find_next_sample(
        client,
        "TAG1",
        START,
        lambda item: item.value == "0",
    )

    assert result == target
    assert client.calls == [
        ("TAG1", START, START + timedelta(minutes=30)),
    ]


def test_find_next_sample_uses_non_overlapping_cumulative_chunks():
    target = sample(START + timedelta(hours=1), "0")
    client = FakeDataClient([[], [target]])
    steps = (timedelta(minutes=30), timedelta(hours=2))

    result = find_next_sample(
        client,
        "TAG1",
        START,
        lambda item: item.value == "0",
        search_steps=steps,
    )

    assert result == target
    assert [call[1:] for call in client.calls] == [
        (START, START + timedelta(minutes=30)),
        (START + timedelta(minutes=30), START + timedelta(hours=2)),
    ]


def test_find_next_sample_can_continue_after_an_already_read_cursor():
    target = sample(START + timedelta(hours=1), "0")
    cursor = START + timedelta(minutes=30)
    client = FakeDataClient([[target]])

    result = find_next_sample(
        client,
        "TAG1",
        START,
        lambda item: item.value == "0",
        cursor_time=cursor,
    )

    assert result == target
    assert client.calls == [
        ("TAG1", cursor, START + timedelta(hours=2)),
    ]


def test_find_next_sample_sorts_and_deduplicates_chunk_samples():
    target = sample(START + timedelta(minutes=20), "0", sequence_no=3)
    duplicate = sample(target.timestamp, "0", sequence_no=3)
    later = sample(START + timedelta(minutes=25), "0", sequence_no=4)
    client = FakeDataClient([[later, duplicate, target]])

    result = find_next_sample(
        client,
        "TAG1",
        START,
        lambda item: item.value == "0",
    )

    assert result == target


def test_find_next_sample_checks_all_default_chunks_and_returns_none():
    client = FakeDataClient([[], [], [], [], []])

    result = find_next_sample(
        client,
        "TAG1",
        START,
        lambda item: item.value == "0",
    )

    assert result is None
    expected_ranges = []
    cursor = START
    for step in DEFAULT_FORWARD_SEARCH_STEPS:
        horizon_end = START + step
        if horizon_end > cursor:
            expected_ranges.append((cursor, horizon_end))
            cursor = horizon_end
    assert [call[1:] for call in client.calls] == expected_ranges


def test_find_next_sample_does_not_swallow_client_errors():
    class FailingClient:
        def get_history(self, tag, start_time, end_time):
            raise RuntimeError("history failed")

    with pytest.raises(RuntimeError, match="history failed"):
        find_next_sample(
            FailingClient(),
            "TAG1",
            START,
            lambda item: True,
        )


def test_multi_context_batches_initial_and_only_missing_lookback_tags():
    class MultiClient:
        def __init__(self):
            self.histories = {
                "TAG1": [sample(START - timedelta(minutes=5), "1")],
                "TAG2": [sample(START - timedelta(hours=1), "0")],
                "TAG3": [
                    sample(START - timedelta(minutes=10), "0"),
                    sample(START + timedelta(minutes=2), "1"),
                ],
            }
            self.calls = []

        def get_histories(self, tags, start_time, end_time):
            self.calls.append((list(tags), start_time, end_time))
            return {
                tag: [
                    item
                    for item in self.histories.get(tag, [])
                    if start_time <= item.timestamp < end_time
                ]
                for tag in tags
            }

    client = MultiClient()

    result = get_histories_with_previous_samples(
        client,
        ["TAG1", "TAG2", "TAG3"],
        START,
        END,
    )

    assert result["TAG1"] == [client.histories["TAG1"][0]]
    assert result["TAG2"] == [client.histories["TAG2"][0]]
    assert result["TAG3"] == client.histories["TAG3"]
    assert client.calls == [
        (["TAG1", "TAG2", "TAG3"], START, END),
        (["TAG1", "TAG2", "TAG3"], START - timedelta(minutes=30), START),
        (
            ["TAG2"],
            START - timedelta(hours=2),
            START - timedelta(minutes=30),
        ),
    ]


def test_multi_context_keeps_in_range_samples_and_no_fake_initial_state():
    class MultiClient:
        def get_histories(self, tags, start_time, end_time):
            return {
                "TAG1": [sample(START + timedelta(minutes=1), "1")],
                "TAG2": [],
            }

    result = get_histories_with_previous_samples(
        MultiClient(),
        ["TAG1", "TAG2"],
        START,
        END,
        lookback_steps=(timedelta(minutes=30),),
    )

    assert len(result["TAG1"]) == 1
    assert result["TAG2"] == []

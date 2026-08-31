from datetime import datetime, timedelta

import pytest

from dcs_performance.data.models import HistorySample
from dcs_performance.rules.persistent_high_alarm.detector import (
    DigitalStateParseError,
    PersistentHighAlarmDetector,
    parse_digital_state,
)


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


def detect(
    samples: list[HistorySample],
    *,
    start: datetime,
    end: datetime,
):
    return PersistentHighAlarmDetector().detect(
        samples,
        "LA-115077",
        "TAG1",
        start,
        observation_end=end,
    )


def test_parse_digital_state_is_explicit_and_supports_decimal_spellings():
    assert parse_digital_state("0") == 0
    assert parse_digital_state("1") == 1
    assert parse_digital_state("0.0") == 0
    assert parse_digital_state("1.0") == 1

    for value in ("ON", "BAD", "", "2"):
        with pytest.raises(DigitalStateParseError):
            parse_digital_state(value)


def test_detector_emits_one_occurrence_for_a_single_010_interval():
    start = datetime(2026, 8, 31, 10, 0)
    occurrences = detect(
        [
            sample(start, "0"),
            sample(start + timedelta(minutes=12), "1"),
            sample(start + timedelta(minutes=13), "1"),
            sample(start + timedelta(minutes=20), "0"),
        ],
        start=start,
        end=start + timedelta(hours=1),
    )

    assert len(occurrences) == 1
    assert occurrences[0].start_time == start + timedelta(minutes=12)
    assert occurrences[0].end_time == start + timedelta(minutes=20)
    assert occurrences[0].duration_seconds == 480
    assert occurrences[0].is_open is False


@pytest.mark.parametrize(
    "duration",
    [timedelta(seconds=299.999), timedelta(seconds=300)],
)
def test_detector_uses_strictly_greater_than_five_minutes(duration: timedelta):
    start = datetime(2026, 8, 31, 10, 0)
    alarm_start = start + timedelta(minutes=1)

    occurrences = detect(
        [sample(start, "0"), sample(alarm_start, "1"), sample(alarm_start + duration, "0")],
        start=start,
        end=alarm_start + duration + timedelta(seconds=1),
    )

    assert occurrences == []


def test_detector_accepts_300_001_seconds():
    start = datetime(2026, 8, 31, 10, 0)
    alarm_start = start + timedelta(minutes=1)
    duration = timedelta(seconds=300, microseconds=1000)

    occurrences = detect(
        [sample(start, "0"), sample(alarm_start, "1"), sample(alarm_start + duration, "0")],
        start=start,
        end=alarm_start + duration + timedelta(seconds=1),
    )

    assert len(occurrences) == 1
    assert occurrences[0].duration_seconds == 300.001


def test_detector_does_not_repeat_a_continuous_alarm_for_many_ones():
    start = datetime(2026, 8, 31, 10, 0)
    samples = [sample(start, "0"), sample(start + timedelta(minutes=1), "1")]
    samples.extend(
        sample(start + timedelta(minutes=2 + index), "1") for index in range(59)
    )
    samples.append(sample(start + timedelta(hours=1), "0", sequence_no=100))

    occurrences = detect(samples, start=start, end=start + timedelta(hours=2))

    assert len(occurrences) == 1


def test_detector_reports_a_new_occurrence_after_recovery():
    start = datetime(2026, 8, 31, 10, 0)
    occurrences = detect(
        [
            sample(start, "0"),
            sample(start + timedelta(minutes=1), "1"),
            sample(start + timedelta(minutes=7), "0"),
            sample(start + timedelta(minutes=10), "1"),
            sample(start + timedelta(minutes=16), "0"),
        ],
        start=start,
        end=start + timedelta(hours=1),
    )

    assert [item.start_time for item in occurrences] == [
        start + timedelta(minutes=1),
        start + timedelta(minutes=10),
    ]


def test_detector_uses_previous_alarm_state_without_recounting_it():
    window_start = datetime(2026, 8, 31, 7, 50)
    occurrences = detect(
        [
            sample(datetime(2026, 8, 31, 7, 40), "1"),
            sample(window_start, "1"),
            sample(datetime(2026, 8, 31, 8, 30), "0"),
            sample(datetime(2026, 8, 31, 10, 0), "1"),
            sample(datetime(2026, 8, 31, 10, 10), "0"),
        ],
        start=window_start,
        end=datetime(2026, 8, 31, 19, 55, 1),
    )

    assert len(occurrences) == 1
    assert occurrences[0].start_time == datetime(2026, 8, 31, 10, 0)


def test_detector_is_conservative_when_no_previous_state_exists():
    start = datetime(2026, 8, 31, 10, 0)

    occurrences = detect(
        [
            sample(start, "1"),
            sample(start + timedelta(minutes=10), "0"),
            sample(start + timedelta(minutes=20), "1"),
            sample(start + timedelta(minutes=26), "0"),
        ],
        start=start,
        end=start + timedelta(hours=1),
    )

    assert len(occurrences) == 1
    assert occurrences[0].start_time == start + timedelta(minutes=20)


def test_detector_reports_open_alarm_at_observation_horizon():
    start = datetime(2026, 8, 31, 19, 0)
    observation_end = datetime(2026, 8, 31, 19, 55, 1)

    occurrences = detect(
        [sample(start, "0"), sample(datetime(2026, 8, 31, 19, 48), "1")],
        start=start,
        end=observation_end,
    )

    assert len(occurrences) == 1
    assert occurrences[0].is_open is True
    assert occurrences[0].end_time is None
    assert occurrences[0].duration_seconds == 421


def test_detector_sorts_and_deduplicates_samples_before_transition_processing():
    start = datetime(2026, 8, 31, 10, 0)
    alarm = sample(start + timedelta(minutes=1), "1", sequence_no=2)
    recovery = sample(start + timedelta(minutes=7), "0", sequence_no=3)

    occurrences = detect(
        [
            recovery,
            alarm,
            alarm,
            sample(start, "0"),
        ],
        start=start,
        end=start + timedelta(hours=1),
    )

    assert len(occurrences) == 1
    assert occurrences[0].start_time == alarm.timestamp


def test_detector_keeps_same_timestamp_sequences_without_creating_zero_duration_event():
    start = datetime(2026, 8, 31, 10, 0)

    occurrences = detect(
        [
            sample(start, "0", sequence_no=1),
            sample(start + timedelta(minutes=1), "1", sequence_no=1),
            sample(start + timedelta(minutes=1), "0", sequence_no=2),
        ],
        start=start,
        end=start + timedelta(hours=1),
    )

    assert occurrences == []


def test_detector_rejects_invalid_threshold_and_history_values():
    with pytest.raises(ValueError, match="threshold_seconds"):
        PersistentHighAlarmDetector(threshold_seconds=0)

    start = datetime(2026, 8, 31, 10, 0)
    with pytest.raises(DigitalStateParseError):
        detect(
            [sample(start, "0"), sample(start + timedelta(minutes=1), "ON")],
            start=start,
            end=start + timedelta(hours=1),
        )

from datetime import datetime, timedelta

import pytest

from dcs_performance.rules.analog_limit_exceedance.config import parse_config
from dcs_performance.rules.analog_limit_exceedance.detector import (
    AnalogLimitExceedanceDetector,
    LimitEventType,
    detect_limit_occurrences,
    parse_analog_value,
)

from tests.fakes import make_history_sample


BASE = datetime(2026, 9, 1, 10, 0, 0)


def point(
    *,
    low_limit=80,
    high_limit=120,
    low_min=300,
    high_min=300,
    low_gap=20,
    high_gap=20,
    low_enabled=True,
    high_enabled=True,
):
    return parse_config(
        {
            "id": "analog_limit_exceedance",
            "name": "连续量上下限超限考核",
            "enabled": True,
            "parameters": {
                "points": [
                    {
                        "id": "P",
                        "history_tag": "TAG-P",
                        "enabled": True,
                        "low": {
                            "enabled": low_enabled,
                            "limit": low_limit,
                            "min_duration_seconds": low_min,
                            "merge_gap_seconds": low_gap,
                        },
                        "high": {
                            "enabled": high_enabled,
                            "limit": high_limit,
                            "min_duration_seconds": high_min,
                            "merge_gap_seconds": high_gap,
                        },
                    }
                ]
            },
            "scoring": {"default_score_per_event": 1},
        }
    ).points[0]


def sample(seconds, value, *, sequence_no=1):
    return make_history_sample(
        BASE + timedelta(seconds=seconds),
        str(value),
        sequence_no=sequence_no,
    )


def detect(samples, config=None, *, observation_end=None, start_time=None):
    return detect_limit_occurrences(
        samples,
        config or point(),
        start_time=start_time,
        observation_end=observation_end,
    )


def test_parse_analog_value_accepts_finite_number_spellings():
    assert parse_analog_value("10") == 10.0
    assert parse_analog_value("10.5") == 10.5
    assert parse_analog_value("-3.2") == -3.2
    assert parse_analog_value("1e2") == 100.0


def test_no_event_when_values_always_normal():
    events = detect([sample(0, 100), sample(300, 100)])

    assert events == []


def test_detects_high_limit_occurrence():
    events = detect(
        [sample(-1, 100), sample(0, 121), sample(301, 121), sample(302, 100)]
    )

    assert len(events) == 1
    assert events[0].event_type == LimitEventType.HIGH.value
    assert events[0].start_time == BASE
    assert events[0].end_time == BASE + timedelta(seconds=302)
    assert events[0].duration_seconds == pytest.approx(302)


def test_detects_low_limit_occurrence():
    events = detect(
        [sample(-1, 100), sample(0, 79), sample(301, 79), sample(302, 100)]
    )

    assert len(events) == 1
    assert events[0].event_type == LimitEventType.LOW.value
    assert events[0].start_time == BASE


def test_value_equal_high_limit_is_normal():
    events = detect([sample(-1, 100), sample(0, 120), sample(301, 120)])

    assert events == []


def test_value_equal_low_limit_is_normal():
    events = detect([sample(-1, 100), sample(0, 80), sample(301, 80)])

    assert events == []


def test_high_299_seconds_does_not_qualify():
    events = detect([sample(-1, 100), sample(0, 121), sample(299, 100)])

    assert events == []


def test_high_exactly_300_seconds_does_not_qualify():
    events = detect([sample(-1, 100), sample(0, 121), sample(300, 100)])

    assert events == []


def test_high_301_seconds_qualifies():
    events = detect([sample(-1, 100), sample(0, 121), sample(301, 100)])

    assert len(events) == 1
    assert events[0].duration_seconds == pytest.approx(301)


def test_low_exactly_threshold_does_not_qualify():
    events = detect(
        [sample(-1, 100), sample(0, 79), sample(300, 100)],
        point(low_min=300),
    )

    assert events == []


def test_low_above_threshold_qualifies():
    events = detect(
        [sample(-1, 100), sample(0, 79), sample(301, 100)],
        point(low_min=300),
    )

    assert len(events) == 1


def test_same_high_events_merge_when_gap_below_threshold():
    events = detect(
        [
            sample(-1, 100),
            sample(0, 121),
            sample(100, 100),
            sample(109, 121),
            sample(400, 100),
        ],
        point(high_min=150, high_gap=10),
    )

    assert len(events) == 1
    assert events[0].start_time == BASE
    assert events[0].end_time == BASE + timedelta(seconds=400)
    assert events[0].duration_seconds == pytest.approx(400)


def test_same_high_events_merge_when_gap_equals_threshold():
    events = detect(
        [
            sample(-1, 100),
            sample(0, 121),
            sample(100, 100),
            sample(110, 121),
            sample(400, 100),
        ],
        point(high_min=150, high_gap=10),
    )

    assert len(events) == 1


def test_same_high_events_do_not_merge_when_gap_above_threshold():
    events = detect(
        [
            sample(-1, 100),
            sample(0, 121),
            sample(100, 100),
            sample(111, 121),
            sample(400, 100),
        ],
        point(high_min=50, high_gap=10),
    )

    assert len(events) == 2
    assert events[0].end_time == BASE + timedelta(seconds=100)
    assert events[1].start_time == BASE + timedelta(seconds=111)


def test_same_low_events_merge():
    events = detect(
        [
            sample(-1, 100),
            sample(0, 79),
            sample(100, 100),
            sample(110, 79),
            sample(400, 100),
        ],
        point(low_min=150, low_gap=10),
    )

    assert len(events) == 1
    assert events[0].event_type == "low_limit"
    assert events[0].duration_seconds == pytest.approx(400)


def test_high_and_low_never_merge():
    events = detect(
        [
            sample(-1, 100),
            sample(0, 121),
            sample(100, 100),
            sample(110, 79),
            sample(400, 100),
        ],
        point(low_min=50, high_min=50, low_gap=1000, high_gap=1000),
    )

    assert [event.event_type for event in events] == ["high_limit", "low_limit"]


def test_high_event_tracks_maximum_value_and_time():
    events = detect(
        [
            sample(-1, 100),
            sample(0, 121),
            sample(120, 124),
            sample(300, 137.4),
            sample(400, 130),
            sample(401, 100),
        ]
    )

    assert events[0].extreme_value == pytest.approx(137.4)
    assert events[0].extreme_time == BASE + timedelta(seconds=300)


def test_low_event_tracks_minimum_value_and_time():
    events = detect(
        [
            sample(-1, 100),
            sample(0, 79),
            sample(120, 76),
            sample(300, 42.6),
            sample(400, 50),
            sample(401, 100),
        ]
    )

    assert events[0].extreme_value == pytest.approx(42.6)
    assert events[0].extreme_time == BASE + timedelta(seconds=300)


def test_equal_extreme_uses_first_time():
    events = detect(
        [
            sample(-1, 100),
            sample(0, 121),
            sample(100, 130),
            sample(200, 130),
            sample(301, 100),
        ]
    )

    assert events[0].extreme_time == BASE + timedelta(seconds=100)


def test_detector_sorts_unordered_samples():
    events = detect([sample(301, 100), sample(0, 121), sample(-1, 100)])

    assert len(events) == 1
    assert events[0].start_time == BASE


def test_exact_duplicate_sample_is_deduplicated():
    high = sample(0, 121)
    events = detect([sample(-1, 100), high, high, sample(301, 100)])

    assert len(events) == 1


def test_same_timestamp_different_sequence_is_preserved():
    events = detect(
        [
            sample(-1, 100),
            sample(0, 100, sequence_no=1),
            sample(0, 121, sequence_no=2),
            sample(301, 100),
        ]
    )

    assert len(events) == 1
    assert events[0].start_time == BASE


def test_pre_window_normal_allows_new_high_event():
    events = detect(
        [sample(-1, 100), sample(0, 121), sample(301, 100)],
        start_time=BASE,
    )

    assert len(events) == 1


def test_pre_window_high_does_not_create_new_event_at_window_start():
    events = detect(
        [sample(-60, 121), sample(0, 121), sample(301, 100)],
        start_time=BASE,
    )

    assert events == []


def test_pre_window_low_does_not_create_new_event_at_window_start():
    events = detect(
        [sample(-60, 79), sample(0, 79), sample(301, 100)],
        start_time=BASE,
    )

    assert events == []


def test_unknown_initial_high_does_not_invent_event_start():
    events = detect(
        [sample(0, 121), sample(301, 121), sample(302, 100)],
        start_time=BASE,
    )

    assert events == []


def test_unknown_initial_low_does_not_invent_event_start():
    events = detect(
        [sample(0, 79), sample(301, 79), sample(302, 100)],
        start_time=BASE,
    )

    assert events == []


def test_unknown_initial_low_can_start_at_an_explicit_semantic_boundary():
    events = detect_limit_occurrences(
        [sample(0, 79), sample(301, 79), sample(302, 100)],
        point(),
        start_time=BASE,
        allow_initial_abnormal=True,
    )

    assert len(events) == 1
    assert events[0].start_time == BASE


def test_unknown_initial_state_can_use_a_later_normal_to_find_edge():
    events = detect(
        [sample(0, 121), sample(10, 100), sample(20, 121), sample(321, 100)],
        start_time=BASE,
    )

    assert len(events) == 1
    assert events[0].start_time == BASE + timedelta(seconds=20)


def test_open_high_can_qualify_at_observation_end():
    observation_end = BASE + timedelta(seconds=302)
    events = detect(
        [sample(-1, 100), sample(0, 121)],
        observation_end=observation_end,
    )

    assert len(events) == 1
    assert events[0].is_open is True
    assert events[0].end_time is None
    assert events[0].duration_seconds == pytest.approx(302)


def test_open_low_can_qualify_at_observation_end():
    observation_end = BASE + timedelta(seconds=302)
    events = detect(
        [sample(-1, 100), sample(0, 79)],
        observation_end=observation_end,
    )

    assert len(events) == 1
    assert events[0].event_type == "low_limit"
    assert events[0].is_open is True


def test_open_event_below_threshold_is_not_returned():
    events = detect(
        [sample(-1, 100), sample(0, 121)],
        observation_end=BASE + timedelta(seconds=300),
    )

    assert events == []


def test_rejects_non_numeric_history_value():
    with pytest.raises(ValueError):
        detect([sample(-1, 100), sample(0, "BAD")])


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_rejects_non_finite_history_value(value):
    with pytest.raises(ValueError):
        detect([sample(-1, 100), sample(0, value)])


def test_detector_facade_accepts_constructor_config():
    config = point()
    events = AnalogLimitExceedanceDetector(config).detect(
        [sample(-1, 100), sample(0, 121), sample(301, 100)]
    )

    assert len(events) == 1

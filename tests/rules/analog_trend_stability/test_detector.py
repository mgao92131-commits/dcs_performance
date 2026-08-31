from datetime import datetime, timedelta

import pytest

from dcs_performance.rules.analog_trend_stability.config import (
    DriftConfig,
    DriftWindowConfig,
    StabilityConfig,
)
from dcs_performance.rules.analog_trend_stability.detector import (
    detect_drift_events,
    detect_stability_events,
)
from dcs_performance.rules.analog_trend_stability.trend import DriftPoint, TrendPoint


BASE = datetime(2026, 8, 31, 10, 0)


def trend_point(seconds, deviation, *, segment_id=0):
    return TrendPoint(
        timestamp=BASE + timedelta(seconds=seconds),
        pv=float(deviation),
        trend=0.0,
        deviation=float(deviation),
        segment_id=segment_id,
    )


def stability_config(**overrides):
    values = {
        "enabled": True,
        "warning_deviation": 1.0,
        "high_deviation": 2.0,
        "min_duration_seconds": 60,
        "merge_gap_seconds": 20,
    }
    values.update(overrides)
    return StabilityConfig(**values)


def drift_point(seconds, change, window_id="short"):
    return DriftPoint(
        timestamp=BASE + timedelta(seconds=seconds),
        window_id=window_id,
        window_seconds=600,
        change=float(change),
        segment_id=0,
    )


def test_instantaneous_warning_does_not_create_event():
    events = detect_stability_events(
        [trend_point(0, 1.5), trend_point(10, 0)],
        stability_config(),
    )

    assert events == []


def test_sustained_warning_creates_warning_event():
    events = detect_stability_events(
        [trend_point(0, 1.5), trend_point(30, 1.4), trend_point(60, 1.3), trend_point(90, 0)],
        stability_config(),
    )

    assert len(events) == 1
    assert (events[0].start_time, events[0].end_time) == (
        BASE,
        BASE + timedelta(seconds=90),
    )
    assert events[0].severity == "warning"
    assert events[0].max_abs_deviation == pytest.approx(1.5)


def test_high_value_during_warning_upgrades_whole_event():
    events = detect_stability_events(
        [trend_point(0, 1.5), trend_point(30, 2.5), trend_point(60, 1.5), trend_point(90, 0)],
        stability_config(),
    )

    assert len(events) == 1
    assert events[0].severity == "high"


def test_short_recovery_within_merge_gap_is_one_event():
    events = detect_stability_events(
        [
            trend_point(0, 1.5),
            trend_point(30, 0),
            trend_point(40, 1.4),
            trend_point(70, 0),
        ],
        stability_config(min_duration_seconds=60, merge_gap_seconds=10),
    )

    assert len(events) == 1
    assert (events[0].start_time, events[0].end_time) == (
        BASE,
        BASE + timedelta(seconds=70),
    )


def test_recovery_longer_than_merge_gap_splits_events():
    events = detect_stability_events(
        [
            trend_point(0, 1.5),
            trend_point(30, 0),
            trend_point(50, 1.4),
            trend_point(110, 0),
        ],
        stability_config(min_duration_seconds=20, merge_gap_seconds=10),
    )

    assert len(events) == 2


def test_drift_direction_and_multi_window_evidence_are_merged_once():
    windows = (
        DriftWindowConfig("short", 600, 1, 2, 100),
        DriftWindowConfig("long", 3600, 2, 3, 100),
    )
    drift = DriftConfig(enabled=True, merge_gap_seconds=60, windows=windows)
    points = [
        drift_point(0, 1.5, "short"),
        drift_point(100, 1.5, "short"),
        drift_point(200, 1.5, "short"),
        drift_point(600, 1.5, "short"),
        drift_point(700, 0, "short"),
        drift_point(100, 4, "long"),
        drift_point(200, 4, "long"),
        drift_point(600, 4, "long"),
        drift_point(700, 0, "long"),
    ]

    events = detect_drift_events(points, drift)

    assert len(events) == 1
    assert events[0].direction == "up"
    assert events[0].severity == "high"
    assert [item.window_id for item in events[0].evidence] == ["short", "long"]
    assert [item.peak_change for item in events[0].evidence] == [1.5, 4]


def test_downward_drift_has_down_direction():
    window = DriftWindowConfig("short", 60, 1, 2, 20)
    points = [
        drift_point(0, -1.5),
        drift_point(30, -1.5),
        drift_point(60, -1.5),
        drift_point(90, 0),
    ]

    events = detect_drift_events(
        points,
        DriftConfig(enabled=True, merge_gap_seconds=0, windows=(window,)),
    )

    assert len(events) == 1
    assert events[0].direction == "down"
    assert events[0].evidence[0].peak_change == pytest.approx(-1.5)


def test_detector_does_not_accept_points_from_multiple_segments():
    with pytest.raises(ValueError, match="one numeric segment"):
        detect_stability_events(
            [trend_point(0, 2, segment_id=0), trend_point(60, 2, segment_id=1)],
            stability_config(),
        )

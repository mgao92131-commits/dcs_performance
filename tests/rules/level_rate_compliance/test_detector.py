from datetime import datetime, timedelta

from dcs_performance.data.models import HistorySample
from dcs_performance.rules.level_rate_compliance.config import PointConfig, SmoothingConfig
from dcs_performance.rules.level_rate_compliance.detector import (
    RATE_DOWN,
    RatePoint,
    detect_rate_events,
)


START = datetime(2026, 9, 1, 8, 0)


def point(**overrides):
    values = {
        "id": "LICA-012019",
        "history_tag": "LICA-012019/PID1/PV.CV",
        "enabled": True,
        "smoothing": SmoothingConfig(enabled=True, window_seconds=60, min_samples=1),
        "rate_window_seconds": 7200,
        "lower_rate": -0.14,
        "upper_rate": 0.14,
        "persistence_seconds": 7200,
        "max_gap_seconds": 60,
        "merge_gap_seconds": 0,
    }
    values.update(overrides)
    return PointConfig(**values)


def rate_point(seconds, rate):
    return RatePoint(START + timedelta(seconds=seconds), 72.0, rate, 0)


def test_rate_event_requires_two_hours_and_accepts_exact_duration():
    points = [rate_point(seconds, -0.15) for seconds in range(0, 7200, 60)]
    points.append(rate_point(7200, 0.0))

    events = detect_rate_events(points, point())

    assert len(events) == 1
    assert events[0].direction == RATE_DOWN
    assert events[0].start_time == START
    assert events[0].end_time == START + timedelta(seconds=7200)
    assert events[0].confirmation_time == START + timedelta(seconds=7200)


def test_rate_boundary_is_not_an_exceedance():
    points = [rate_point(seconds, 0.14) for seconds in range(0, 9000, 60)]

    assert detect_rate_events(points, point()) == []


def test_rate_gap_breaks_continuity():
    points = [rate_point(seconds, -0.2) for seconds in range(0, 7141, 60)]
    points.extend(rate_point(seconds, -0.2) for seconds in range(7261, 9000, 60))

    assert detect_rate_events(points, point()) == []


def test_history_sample_shape_is_accepted_by_rate_calculation():
    sample = HistorySample(
        timestamp=START,
        value="72",
        data_type="Analog",
        delta_v_status="Good",
        archive_status="HistoryDataIsValid",
        sequence_no=1,
        is_history_hole=False,
        is_cr_hole=False,
        is_manually_deleted=False,
        is_manually_inserted=False,
    )
    from dcs_performance.rules.level_rate_compliance.detector import calculate_rate_points

    assert calculate_rate_points([sample], point()) == []

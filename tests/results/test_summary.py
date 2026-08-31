from datetime import datetime

import pytest

from dcs_performance.core.result import AssignedAssessmentEvent
from dcs_performance.core.window import TimeRange
from dcs_performance.results.summary import (
    PointAssessmentSummary,
    ShiftAssessmentSummary,
    build_shift_summary,
)
from dcs_performance.shifts.model import Shift


SHIFT = Shift(
    team_id="B",
    shift_type="day",
    start_time=datetime(2026, 8, 31, 8, 0),
    end_time=datetime(2026, 8, 31, 20, 0),
)
WINDOW = TimeRange(
    start_time=datetime(2026, 8, 31, 7, 50),
    end_time=datetime(2026, 8, 31, 19, 50),
)


def assigned(point_id: str, score: float) -> AssignedAssessmentEvent:
    return AssignedAssessmentEvent(
        rule_id="persistent_high_alarm",
        rule_name="持续高报考核",
        team_id="B",
        shift_start=SHIFT.start_time,
        shift_end=SHIFT.end_time,
        event_start=datetime(2026, 8, 31, 10, 0),
        event_end=datetime(2026, 8, 31, 10, 10),
        score=score,
        message="高报",
        window_start=WINDOW.start_time,
        window_end=WINDOW.end_time,
        data={"point_id": point_id},
    )


POINTS = [
    {"id": "LA-115077"},
    {"id": "LA-115177"},
    {"id": "LA-117075"},
]


def test_summary_includes_zero_event_configured_points():
    summary = build_shift_summary(
        [assigned("LA-115077", 1), assigned("LA-115177", 1)],
        points=POINTS,
    )

    assert isinstance(summary, ShiftAssessmentSummary)
    assert summary.team_id == "B"
    assert summary.shift_start == SHIFT.start_time
    assert summary.shift_end == SHIFT.end_time
    assert summary.window_start == WINDOW.start_time
    assert summary.window_end == WINDOW.end_time
    assert summary.event_count == 2
    assert summary.total_score == 2
    assert summary.by_point == {
        "LA-115077": PointAssessmentSummary("LA-115077", 1, 1),
        "LA-115177": PointAssessmentSummary("LA-115177", 1, 1),
        "LA-117075": PointAssessmentSummary("LA-117075", 0, 0),
    }


def test_empty_summary_can_use_shift_window_and_rule_config():
    summary = build_shift_summary(
        [],
        shift=SHIFT,
        window=WINDOW,
        rule_config={"parameters": {"points": POINTS}},
    )

    assert summary.event_count == 0
    assert summary.total_score == 0
    assert all(item.event_count == 0 for item in summary.by_point.values())


def test_summary_rejects_events_from_multiple_shifts():
    other = AssignedAssessmentEvent(
        **{
            **assigned("LA-115077", 1).__dict__,
            "team_id": "C",
        }
    )

    with pytest.raises(ValueError, match="multiple shifts"):
        build_shift_summary([assigned("LA-115077", 1), other], points=POINTS)

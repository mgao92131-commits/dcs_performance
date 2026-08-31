from datetime import datetime

from dcs_performance.core.evaluation import EvaluatedAssessmentEvent
from dcs_performance.core.event import AssessmentEvent
from dcs_performance.core.window import TimeRange
from dcs_performance.results.scorer import AssessmentScorer
from dcs_performance.shifts.model import Shift


def make_evaluated(point_id: str) -> EvaluatedAssessmentEvent:
    shift = Shift(
        team_id="B",
        shift_type="day",
        start_time=datetime(2026, 8, 31, 8, 0),
        end_time=datetime(2026, 8, 31, 20, 0),
    )
    window = TimeRange(
        start_time=datetime(2026, 8, 31, 7, 50),
        end_time=datetime(2026, 8, 31, 19, 50),
    )
    event = AssessmentEvent(
        start_time=datetime(2026, 8, 31, 10, 12),
        end_time=datetime(2026, 8, 31, 10, 20),
        message="高报",
        data={"point_id": point_id, "event_key": "stable-key"},
    )
    return EvaluatedAssessmentEvent(
        rule_id="persistent_high_alarm",
        rule_name="持续高报考核",
        shift=shift,
        window=window,
        event=event,
        config={
            "scoring": {
                "default_score_per_event": 1,
                "by_point": {"LA-115077": 2},
            }
        },
    )


def test_scorer_prefers_point_specific_score_and_keeps_context():
    assigned = AssessmentScorer().score(make_evaluated("LA-115077"))

    assert assigned.rule_id == "persistent_high_alarm"
    assert assigned.rule_name == "持续高报考核"
    assert assigned.team_id == "B"
    assert assigned.shift_start == datetime(2026, 8, 31, 8, 0)
    assert assigned.shift_end == datetime(2026, 8, 31, 20, 0)
    assert assigned.window_start == datetime(2026, 8, 31, 7, 50)
    assert assigned.window_end == datetime(2026, 8, 31, 19, 50)
    assert assigned.event_start == datetime(2026, 8, 31, 10, 12)
    assert assigned.event_end == datetime(2026, 8, 31, 10, 20)
    assert assigned.score == 2
    assert assigned.message == "高报"
    assert assigned.data["event_key"] == "stable-key"


def test_scorer_uses_configured_default_for_unmapped_point():
    evaluated = make_evaluated("LA-OTHER")

    assigned = AssessmentScorer().score(evaluated)

    assert assigned.score == 1

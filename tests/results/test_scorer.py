from datetime import datetime

import pytest

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


def test_scorer_resolves_point_score_key_without_breaking_numeric_by_point():
    evaluated = make_evaluated("LA-115077")
    evaluated = EvaluatedAssessmentEvent(
        rule_id=evaluated.rule_id,
        rule_name=evaluated.rule_name,
        shift=evaluated.shift,
        window=evaluated.window,
        event=AssessmentEvent(
            start_time=evaluated.event.start_time,
            end_time=evaluated.event.end_time,
            data={
                "point_id": "LA-115077",
                "event_type": "trend_drift",
                "score_key": "trend_drift.high",
            },
        ),
        config={
            "scoring": {
                "default_score_per_event": 1,
                "by_point": {
                    "LA-115077": {
                        "stability_deviation": {"warning": 1, "high": 2},
                        "trend_drift": {"warning": 1, "high": 2},
                    }
                },
            }
        },
    )

    assert AssessmentScorer().score(evaluated).score == 2


def test_scorer_uses_default_when_nested_score_key_is_not_configured():
    evaluated = make_evaluated("LA-115077")
    evaluated = EvaluatedAssessmentEvent(
        rule_id=evaluated.rule_id,
        rule_name=evaluated.rule_name,
        shift=evaluated.shift,
        window=evaluated.window,
        event=AssessmentEvent(
            start_time=evaluated.event.start_time,
            end_time=evaluated.event.end_time,
            data={
                "point_id": "LA-115077",
                "score_key": "trend_drift.high",
            },
        ),
        config={
            "scoring": {
                "default_score_per_event": 1,
                "by_point": {"LA-115077": {"trend_drift": {"warning": 3}}},
            }
        },
    )

    assert AssessmentScorer().score(evaluated).score == 1


def test_scorer_prefers_point_and_event_type_score():
    evaluated = make_evaluated("LA-115077")
    evaluated = EvaluatedAssessmentEvent(
        rule_id=evaluated.rule_id,
        rule_name=evaluated.rule_name,
        shift=evaluated.shift,
        window=evaluated.window,
        event=AssessmentEvent(
            start_time=evaluated.event.start_time,
            end_time=evaluated.event.end_time,
            data={
                "point_id": "LA-115077",
                "event_type": "switch_timeout",
            },
        ),
        config={
            "scoring": {
                "default_score_per_event": 1,
                "by_event_type": {"switch_timeout": 3},
                "by_point": {"LA-115077": 4},
                "by_point_event_type": {
                    "LA-115077": {"switch_timeout": 7}
                },
            }
        },
    )

    assert AssessmentScorer().score(evaluated).score == 7


def test_scorer_falls_back_from_point_event_type_to_event_type_then_point():
    evaluated = make_evaluated("LA-115077")
    evaluated = EvaluatedAssessmentEvent(
        rule_id=evaluated.rule_id,
        rule_name=evaluated.rule_name,
        shift=evaluated.shift,
        window=evaluated.window,
        event=AssessmentEvent(
            start_time=evaluated.event.start_time,
            end_time=evaluated.event.end_time,
            data={"point_id": "LA-115077", "event_type": "low_flow"},
        ),
        config={
            "scoring": {
                "default_score_per_event": 1,
                "by_event_type": {"low_flow": 3},
                "by_point": {"LA-115077": 4},
                "by_point_event_type": {"LA-115077": {"switch_timeout": 7}},
            }
        },
    )
    assert AssessmentScorer().score(evaluated).score == 3

    evaluated = EvaluatedAssessmentEvent(
        rule_id=evaluated.rule_id,
        rule_name=evaluated.rule_name,
        shift=evaluated.shift,
        window=evaluated.window,
        event=AssessmentEvent(
            start_time=evaluated.event.start_time,
            end_time=evaluated.event.end_time,
            data={"point_id": "LA-115077", "event_type": "other"},
        ),
        config={
            "scoring": {
                "default_score_per_event": 1,
                "by_point": {"LA-115077": 4},
            }
        },
    )
    assert AssessmentScorer().score(evaluated).score == 4


def test_scorer_applies_event_score_multiplier_after_resolving_base_score():
    evaluated = make_evaluated("LA-115077")
    evaluated = EvaluatedAssessmentEvent(
        rule_id=evaluated.rule_id,
        rule_name=evaluated.rule_name,
        shift=evaluated.shift,
        window=evaluated.window,
        event=AssessmentEvent(
            start_time=evaluated.event.start_time,
            end_time=evaluated.event.end_time,
            data={
                "point_id": "LA-115077",
                "event_type": "persistent_high",
                "score_multiplier": 4,
            },
        ),
        config=evaluated.config,
    )

    assigned = AssessmentScorer().score(evaluated)

    assert assigned.score == 8
    assert assigned.data["score_multiplier"] == 4
    assert assigned.data["base_score"] == 2


@pytest.mark.parametrize("multiplier", [True, -1, float("nan"), "4"])
def test_scorer_rejects_invalid_score_multiplier(multiplier):
    evaluated = make_evaluated("LA-115077")
    evaluated = EvaluatedAssessmentEvent(
        rule_id=evaluated.rule_id,
        rule_name=evaluated.rule_name,
        shift=evaluated.shift,
        window=evaluated.window,
        event=AssessmentEvent(
            start_time=evaluated.event.start_time,
            end_time=evaluated.event.end_time,
            data={"point_id": "LA-115077", "score_multiplier": multiplier},
        ),
        config=evaluated.config,
    )

    with pytest.raises(ValueError, match="score_multiplier"):
        AssessmentScorer().score(evaluated)

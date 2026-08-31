"""Configuration-driven scoring of evaluated assessment events."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite

from dcs_performance.core.evaluation import EvaluatedAssessmentEvent
from dcs_performance.core.result import AssignedAssessmentEvent


class AssessmentScorer:
    """Turn one detailed event into an assigned event with a configured score."""

    def score(
        self,
        evaluated: EvaluatedAssessmentEvent,
    ) -> AssignedAssessmentEvent:
        if not isinstance(evaluated, EvaluatedAssessmentEvent):
            raise TypeError("evaluated must be an EvaluatedAssessmentEvent")
        scoring = evaluated.config.get("scoring")
        if not isinstance(scoring, Mapping):
            raise ValueError("rule config scoring must be an object")

        point_id = evaluated.event.data.get("point_id")
        by_point = scoring.get("by_point", {})
        if not isinstance(by_point, Mapping):
            raise ValueError("rule config scoring.by_point must be an object")

        if isinstance(point_id, str) and point_id in by_point:
            score = _score_value(by_point[point_id], f"scoring.by_point.{point_id}")
        elif "default_score_per_event" in scoring:
            score = _score_value(
                scoring["default_score_per_event"],
                "scoring.default_score_per_event",
            )
        elif "score_per_event" in scoring:
            # Keep the earlier example configuration usable while the
            # production rule uses the explicit default_score_per_event name.
            score = _score_value(scoring["score_per_event"], "scoring.score_per_event")
        else:
            raise ValueError(
                "rule config scoring must define default_score_per_event "
                "or a matching by_point value"
            )

        return AssignedAssessmentEvent(
            rule_id=evaluated.rule_id,
            rule_name=evaluated.rule_name,
            team_id=evaluated.shift.team_id,
            shift_start=evaluated.shift.start_time,
            shift_end=evaluated.shift.end_time,
            event_start=evaluated.event.start_time,
            event_end=evaluated.event.end_time,
            score=score,
            message=evaluated.event.message,
            window_start=evaluated.window.start_time,
            window_end=evaluated.window.end_time,
            data=dict(evaluated.event.data),
        )


def _score_value(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{field_name} must be a finite number")
    return numeric


# Concise compatibility name for callers that refer to the stage as Scoring.
Scorer = AssessmentScorer

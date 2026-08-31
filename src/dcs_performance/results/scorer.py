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
        event_type = evaluated.event.data.get("event_type")

        by_point_event_type = scoring.get("by_point_event_type", {})
        if not isinstance(by_point_event_type, Mapping):
            raise ValueError(
                "rule config scoring.by_point_event_type must be an object"
            )
        by_event_type = scoring.get("by_event_type", {})
        if not isinstance(by_event_type, Mapping):
            raise ValueError("rule config scoring.by_event_type must be an object")

        by_point = scoring.get("by_point", {})
        if not isinstance(by_point, Mapping):
            raise ValueError("rule config scoring.by_point must be an object")

        point_event_scores = None
        if isinstance(point_id, str) and point_id in by_point_event_type:
            point_event_scores = by_point_event_type[point_id]
            if not isinstance(point_event_scores, Mapping):
                raise ValueError(
                    "rule config scoring.by_point_event_type."
                    f"{point_id} must be an object"
                )

        if (
            isinstance(event_type, str)
            and point_event_scores is not None
            and event_type in point_event_scores
        ):
            score = _score_value(
                point_event_scores[event_type],
                f"scoring.by_point_event_type.{point_id}.{event_type}",
            )
        elif isinstance(event_type, str) and event_type in by_event_type:
            score = _score_value(
                by_event_type[event_type],
                f"scoring.by_event_type.{event_type}",
            )
        elif isinstance(point_id, str) and point_id in by_point:
            point_score = by_point[point_id]
            if isinstance(point_score, Mapping):
                score_key = evaluated.event.data.get("score_key")
                configured_score = _lookup_score_key(point_score, score_key)
                if configured_score is not None:
                    score = _score_value(
                        configured_score,
                        f"scoring.by_point.{point_id}.{score_key}",
                    )
                else:
                    score = _default_score(scoring)
            else:
                # Preserve the original point -> number configuration used by
                # persistent_high_alarm and existing integrations.
                score = _score_value(point_score, f"scoring.by_point.{point_id}")
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
            score = _default_score(scoring)

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


def _default_score(scoring: Mapping[str, object]) -> float:
    if "default_score_per_event" in scoring:
        return _score_value(
            scoring["default_score_per_event"],
            "scoring.default_score_per_event",
        )
    if "score_per_event" in scoring:
        # Keep the earlier example configuration usable while the production
        # rule uses the explicit default_score_per_event name.
        return _score_value(scoring["score_per_event"], "scoring.score_per_event")
    raise ValueError(
        "rule config scoring must define a matching event/point score "
        "or default_score_per_event"
    )


def _lookup_score_key(
    point_score: Mapping[str, object],
    score_key: object,
) -> object | None:
    """Resolve ``stability_deviation.high`` through nested mappings."""

    if not isinstance(score_key, str) or not score_key:
        return None
    if score_key in point_score:
        return point_score[score_key]

    current: object = point_score
    for part in score_key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


# Concise compatibility name for callers that refer to the stage as Scoring.
Scorer = AssessmentScorer

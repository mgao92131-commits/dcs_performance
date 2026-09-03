"""Build a rule's effective assessment window from a concrete shift."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from dcs_performance.shifts.model import Shift


@dataclass(frozen=True)
class TimeRange:
    """A half-open time range passed to a rule."""

    start_time: datetime
    end_time: datetime

    def __post_init__(self) -> None:
        if self.end_time <= self.start_time:
            raise ValueError("time range end_time must be after start_time")


def build_assessment_window(
    shift: Shift,
    config: Mapping[str, Any],
) -> TimeRange:
    """Apply the phase-one ``assessment_window`` minute offsets.

    ``start_offset_minutes`` is added to the shift start and
    ``end_offset_minutes`` is subtracted from the shift end.  More elaborate
    window DSLs are intentionally outside this phase.
    """

    raw_window = config.get("assessment_window", {})
    if not isinstance(raw_window, Mapping):
        raise TypeError("config.assessment_window must be an object")

    start_offset = _offset_minutes(raw_window.get("start_offset_minutes", 0))
    end_offset = _offset_minutes(raw_window.get("end_offset_minutes", 0))

    return TimeRange(
        start_time=shift.start_time + timedelta(minutes=start_offset),
        end_time=shift.end_time - timedelta(minutes=end_offset),
    )


def build_point_assessment_window(
    shift: Shift,
    rule_config: Mapping[str, Any],
    point_config: Mapping[str, Any],
) -> TimeRange:
    """Build the effective window for one configured assessment point.

    A point-level ``assessment_window`` is an override of the rule-level
    default.  Each offset may be supplied independently; an omitted point
    offset inherits the corresponding rule-level value.  Keeping this merge
    here means every rule and every delivery path use the same window
    semantics without changing the :class:`AssessmentRule` protocol.
    """

    if not isinstance(rule_config, Mapping):
        raise TypeError("rule_config must be an object")
    if not isinstance(point_config, Mapping):
        raise TypeError("point_config must be an object")

    raw_rule_window = rule_config.get("assessment_window", {})
    if not isinstance(raw_rule_window, Mapping):
        raise TypeError("config.assessment_window must be an object")
    raw_point_window = point_config.get("assessment_window", {})
    if not isinstance(raw_point_window, Mapping):
        raise TypeError("point.assessment_window must be an object")

    merged_window = dict(raw_rule_window)
    for key in ("start_offset_minutes", "end_offset_minutes"):
        if key in raw_point_window:
            merged_window[key] = raw_point_window[key]

    return build_assessment_window(
        shift,
        {"assessment_window": merged_window},
    )


def _offset_minutes(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("assessment window offsets must be integers")
    if not isinstance(value, int):
        raise TypeError("assessment window offsets must be integers")
    return value

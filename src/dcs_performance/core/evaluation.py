"""Evaluation results that retain rule, shift, window, and config context."""

from dataclasses import dataclass, field
from typing import Any
from collections.abc import Mapping

from dcs_performance.shifts.model import Shift

from .event import AssessmentEvent
from .window import TimeRange


@dataclass(frozen=True)
class EvaluatedAssessmentEvent:
    """An event together with the context in which a rule produced it."""

    rule_id: str
    rule_name: str
    shift: Shift
    window: TimeRange
    event: AssessmentEvent
    config: Mapping[str, Any]


@dataclass(frozen=True)
class RuleExecutionResult:
    """One rule execution, including the important zero-event case."""

    rule_id: str
    rule_name: str
    shift: Shift
    window: TimeRange
    config: Mapping[str, Any]
    events: tuple[EvaluatedAssessmentEvent, ...]
    # Most rules use one window for the whole execution.  When a rule has
    # point-local overrides, this mapping records the effective window for
    # each enabled point while ``window`` remains the rule-level default for
    # backwards-compatible callers and rule summaries.
    point_windows: Mapping[str, TimeRange] = field(default_factory=dict)

    def window_for_point(self, point_id: str) -> TimeRange:
        """Return a point override, or the rule-level default window."""

        return self.point_windows.get(point_id, self.window)

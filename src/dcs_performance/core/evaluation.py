"""Evaluation results that retain rule, shift, window, and config context."""

from dataclasses import dataclass
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

"""Result-package models, separate from assessment-domain results."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from dcs_performance.core.result import AssignedAssessmentEvent
from dcs_performance.core.window import TimeRange
from dcs_performance.shifts.model import Shift


@dataclass(frozen=True)
class PointAssessmentResult:
    rule_id: str
    rule_name: str
    point_id: str
    event_count: int
    score: float
    status: str
    data_status: str
    image_path: str
    events: tuple[AssignedAssessmentEvent, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # Effective point-local assessment window.  Optional only to preserve
    # source compatibility for callers that construct this delivery model
    # directly; published Result Packages always populate it.
    window: TimeRange | None = None


@dataclass(frozen=True)
class RuleAssessmentResult:
    rule_id: str
    rule_name: str
    window: TimeRange
    event_count: int
    score: float
    points: tuple[PointAssessmentResult, ...]


@dataclass(frozen=True)
class DeliveryResult:
    run_id: str
    package_path: Path
    result_json_path: Path
    images_path: Path
    shift: Shift
    generated_at: datetime
    rule_count: int
    point_count: int
    event_count: int
    total_score: float

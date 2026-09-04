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
    # The effective assessment window is part of the point's identity in a
    # Result Package, including the zero-event case.
    window: TimeRange
    metadata: Mapping[str, Any] = field(default_factory=dict)


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

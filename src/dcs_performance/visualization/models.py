"""Rule-visualization boundary models."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from dcs_performance.core.result import AssignedAssessmentEvent
from dcs_performance.core.window import TimeRange
from dcs_performance.data.client import DcsDataClient
from dcs_performance.shifts.model import Shift


@dataclass(frozen=True)
class PointVisualizationContext:
    rule_id: str
    rule_name: str
    point_id: str
    point_config: Mapping[str, Any]
    rule_config: Mapping[str, Any]
    shift: Shift
    window: TimeRange
    events: tuple[AssignedAssessmentEvent, ...]
    data_client: DcsDataClient

    @property
    def score(self) -> float:
        return sum(event.score for event in self.events)


@dataclass(frozen=True)
class VisualizationArtifact:
    image_path: str
    data_status: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

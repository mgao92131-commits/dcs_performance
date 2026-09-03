from pathlib import Path
from typing import Protocol

from .models import PointVisualizationContext, VisualizationArtifact


class RuleVisualizer(Protocol):
    def render_point(
        self,
        context: PointVisualizationContext,
        output_path: Path,
    ) -> VisualizationArtifact: ...

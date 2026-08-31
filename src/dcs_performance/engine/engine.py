"""The phase-one orchestration entry point."""

from pathlib import Path

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.data.client import DcsDataClient
from dcs_performance.shifts.model import Shift

from .loader import RuleLoader
from .runner import RuleRunner


class AssessmentEngine:
    """Run all enabled rules for one shift and collect their events.

    The engine contains no rule-specific conditionals.  A new rule is added
    by adding its directory and local configuration.
    """

    def __init__(
        self,
        loader: RuleLoader | None = None,
        runner: RuleRunner | None = None,
        *,
        rules_dir: str | Path | None = None,
        data_client: DcsDataClient | None = None,
    ) -> None:
        self.loader = loader or RuleLoader(
            rules_dir=rules_dir,
            data_client=data_client,
        )
        self.runner = runner or RuleRunner()

    def run(self, shift: Shift) -> list[AssessmentEvent]:
        """Evaluate every enabled rule for ``shift``."""

        events: list[AssessmentEvent] = []
        for loaded_rule in self.loader.load_enabled():
            events.extend(self.runner.run(shift, loaded_rule))
        return events

    def evaluate(self, shift: Shift) -> list[AssessmentEvent]:
        """Alias for ``run`` to make the engine easy to call from a pipeline."""

        return self.run(shift)

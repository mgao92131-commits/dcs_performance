"""Run one loaded rule for one concrete shift."""

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.core.window import build_assessment_window
from dcs_performance.shifts.model import Shift

from .loader import LoadedRule


class RuleRunner:
    """Build a rule window and invoke the rule's common interface."""

    def run(
        self,
        shift: Shift,
        loaded_rule: LoadedRule,
    ) -> list[AssessmentEvent]:
        window = build_assessment_window(shift, loaded_rule.config)
        return loaded_rule.rule.evaluate(window.start_time, window.end_time)


# Short name retained for the terminology used in the project brief.
Runner = RuleRunner

"""Run one loaded rule for one concrete shift."""

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.core.evaluation import (
    EvaluatedAssessmentEvent,
    RuleExecutionResult,
)
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
        return [
            evaluated.event
            for evaluated in self.run_detailed(shift, loaded_rule)
        ]

    def run_detailed(
        self,
        shift: Shift,
        loaded_rule: LoadedRule,
    ) -> list[EvaluatedAssessmentEvent]:
        """Evaluate one rule while retaining all execution context."""

        return list(self.run_execution(shift, loaded_rule).events)

    def run_execution(
        self,
        shift: Shift,
        loaded_rule: LoadedRule,
    ) -> RuleExecutionResult:
        """Evaluate a rule while retaining context even when it finds nothing."""

        window = build_assessment_window(shift, loaded_rule.config)
        events = loaded_rule.rule.evaluate(window.start_time, window.end_time)
        evaluated = tuple(
            EvaluatedAssessmentEvent(
                rule_id=loaded_rule.id,
                rule_name=loaded_rule.name,
                shift=shift,
                window=window,
                event=event,
                config=loaded_rule.config,
            )
            for event in events
        )
        return RuleExecutionResult(
            rule_id=loaded_rule.id,
            rule_name=loaded_rule.name,
            shift=shift,
            window=window,
            config=loaded_rule.config,
            events=evaluated,
        )


# Short name retained for the terminology used in the project brief.
Runner = RuleRunner

"""Run one loaded rule for one concrete shift."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.core.evaluation import (
    EvaluatedAssessmentEvent,
    RuleExecutionResult,
)
from dcs_performance.core.window import (
    TimeRange,
    build_assessment_window,
    build_point_assessment_window,
)
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
        """Evaluate a rule while retaining context even when it finds nothing.

        Rules keep the shared ``evaluate(start_time, end_time)`` contract.  If
        enabled points use different windows, the runner invokes that same
        method once per distinct window and routes returned events by their
        configured ``point_id``.
        """

        window = build_assessment_window(shift, loaded_rule.config)
        point_configs = _enabled_point_configs(loaded_rule.config)
        point_windows = {
            point_id: build_point_assessment_window(
                shift,
                loaded_rule.config,
                point_config,
            )
            for point_id, point_config in point_configs
        }

        # Preserve the original one-call execution path whenever no point
        # supplies an override.  This keeps rule behaviour and data-access
        # costs unchanged for existing configurations.
        if not any("assessment_window" in point for _, point in point_configs):
            events = loaded_rule.rule.evaluate(window.start_time, window.end_time)
            evaluated = _evaluate_events(
                events,
                rule=loaded_rule,
                shift=shift,
                window=window,
            )
        else:
            evaluated_items: list[EvaluatedAssessmentEvent] = []
            configured_ids = set(point_windows)
            groups: dict[tuple[datetime, datetime], list[str]] = {}
            for point_id, point_window in point_windows.items():
                groups.setdefault(
                    (point_window.start_time, point_window.end_time),
                    [],
                ).append(point_id)

            # The public rule protocol remains evaluate(start, end).  To
            # honour different point windows without changing that protocol,
            # evaluate once per distinct effective window and retain only the
            # events belonging to the points in that window group.
            for (start_time, end_time), group_point_ids in groups.items():
                group_window = TimeRange(start_time, end_time)
                events = loaded_rule.rule.evaluate(
                    group_window.start_time,
                    group_window.end_time,
                )
                for evaluated in _evaluate_events(
                    events,
                    rule=loaded_rule,
                    shift=shift,
                    window=group_window,
                ):
                    point_id = evaluated.event.data.get("point_id")
                    if not isinstance(point_id, str) or not point_id:
                        # _evaluate_events already raises; this guard keeps
                        # the type narrowing explicit for the filter below.
                        raise ValueError(
                            f"Rule {loaded_rule.id} returned event without point_id"
                        )
                    if point_id not in configured_ids:
                        raise ValueError(
                            f"Rule {loaded_rule.id} returned event for unknown or "
                            f"disabled point_id {point_id!r}"
                        )
                    if point_id in group_point_ids:
                        evaluated_items.append(evaluated)
            evaluated = tuple(evaluated_items)

        return RuleExecutionResult(
            rule_id=loaded_rule.id,
            rule_name=loaded_rule.name,
            shift=shift,
            window=window,
            config=loaded_rule.config,
            events=evaluated,
            point_windows=point_windows,
        )


def _evaluate_events(
    events: list[AssessmentEvent],
    *,
    rule: LoadedRule,
    shift: Shift,
    window: TimeRange,
) -> tuple[EvaluatedAssessmentEvent, ...]:
    """Attach execution context to the events returned by one rule call."""

    return tuple(
        EvaluatedAssessmentEvent(
            rule_id=rule.id,
            rule_name=rule.name,
            shift=shift,
            window=window,
            event=event,
            config=rule.config,
        )
        for event in events
    )


def _enabled_point_configs(
    config: Mapping[str, Any],
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    """Read enough raw point inventory to resolve point-local windows.

    Rule-specific loaders remain responsible for full configuration
    validation.  This helper intentionally accepts only a valid-looking
    ``parameters.points`` collection and otherwise leaves the existing rule
    execution path untouched; DeliveryManager performs the authoritative
    inventory validation before publication.
    """

    parameters = config.get("parameters")
    if not isinstance(parameters, Mapping):
        return ()
    raw_points = parameters.get("points")
    if not isinstance(raw_points, list):
        return ()

    result: list[tuple[str, Mapping[str, Any]]] = []
    for point in raw_points:
        if not isinstance(point, Mapping):
            return ()
        point_id = point.get("id")
        enabled = point.get("enabled", True)
        if not isinstance(point_id, str) or not point_id:
            return ()
        if not isinstance(enabled, bool):
            return ()
        if enabled:
            result.append((point_id, point))
    return tuple(result)


# Short name retained for the terminology used in the project brief.
Runner = RuleRunner

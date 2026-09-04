"""Run one loaded rule for one concrete shift."""

from collections.abc import Iterable, Mapping
from datetime import datetime
from inspect import Parameter, signature
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

        Enabled points are grouped by effective assessment window. Each group
        is passed to a point-aware rule as ``point_ids`` so the rule can limit
        history reads and detector work to the requested points. A short-term
        two-argument compatibility path is retained for legacy rules; those
        rules are filtered after each call because they cannot provide the
        point-level query optimization.
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

        if not point_configs:
            events = _invoke_evaluate(
                loaded_rule.rule,
                window.start_time,
                window.end_time,
                point_ids=None,
            )
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

            supports_point_ids = _supports_point_ids(loaded_rule.rule)
            for (start_time, end_time), group_point_ids in groups.items():
                group_window = TimeRange(start_time, end_time)
                events = _invoke_evaluate(
                    loaded_rule.rule,
                    group_window.start_time,
                    group_window.end_time,
                    point_ids=group_point_ids,
                )
                for event in events:
                    point_id = event.data.get("point_id")
                    if not isinstance(point_id, str) or not point_id:
                        raise ValueError(
                            f"Rule {loaded_rule.id} returned event without point_id"
                        )
                    if point_id not in configured_ids:
                        raise ValueError(
                            f"Rule {loaded_rule.id} returned event for unknown or "
                            f"disabled point_id {point_id!r}"
                        )
                    if point_id not in group_point_ids:
                        if supports_point_ids:
                            raise ValueError(
                                f"Rule {loaded_rule.id} returned event for point_id "
                                f"{point_id!r} outside requested point_ids"
                            )
                        # Legacy rules return the complete rule result. Keep
                        # only this group's events because they cannot accept
                        # the point subset keyword.
                        continue
                    evaluated_items.append(
                        EvaluatedAssessmentEvent(
                            rule_id=loaded_rule.id,
                            rule_name=loaded_rule.name,
                            shift=shift,
                            window=group_window,
                            event=event,
                            config=loaded_rule.config,
                        )
                    )
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
    events: Iterable[AssessmentEvent],
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


def _invoke_evaluate(
    rule: object,
    start_time: datetime,
    end_time: datetime,
    *,
    point_ids: list[str] | None,
) -> Iterable[AssessmentEvent]:
    """Invoke a current or legacy rule without masking rule exceptions."""

    evaluate = getattr(rule, "evaluate", None)
    if not callable(evaluate):
        raise TypeError("loaded rule must provide evaluate()")
    if point_ids is not None and _supports_point_ids(rule):
        return evaluate(start_time, end_time, point_ids=point_ids)
    return evaluate(start_time, end_time)


def _supports_point_ids(rule: object) -> bool:
    """Return whether ``rule.evaluate`` accepts the point subset keyword."""

    evaluate = getattr(rule, "evaluate", None)
    if not callable(evaluate):
        return False
    try:
        parameters = signature(evaluate).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "point_ids"
        or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
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

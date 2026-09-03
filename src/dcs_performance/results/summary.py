"""班次级别的考核结果汇总。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from dcs_performance.core.result import AssignedAssessmentEvent
from dcs_performance.core.window import TimeRange
from dcs_performance.shifts.model import Shift


@dataclass(frozen=True)
class PointAssessmentSummary:
    point_id: str
    event_count: int
    score: float


@dataclass(frozen=True)
class ShiftAssessmentSummary:
    team_id: str
    shift_start: datetime
    shift_end: datetime
    window_start: datetime
    window_end: datetime
    event_count: int
    total_score: float
    by_point: dict[str, PointAssessmentSummary]


# Short name used by the pipeline vocabulary while retaining the more
# descriptive public dataclass name above.
ShiftSummary = ShiftAssessmentSummary


class AssessmentSummarizer:
    """Build a summary from scored events and optional configured point IDs."""

    def __init__(
        self,
        *,
        point_ids: Iterable[str] | None = None,
        rule_config: Mapping[str, object] | None = None,
    ) -> None:
        self.point_ids = _resolve_point_ids(point_ids, rule_config)

    def summarize(
        self,
        assigned_events: Iterable[AssignedAssessmentEvent],
        *,
        shift: Shift | None = None,
        window: TimeRange | None = None,
        point_ids: Iterable[str] | None = None,
        rule_config: Mapping[str, object] | None = None,
        allow_multiple_windows: bool = False,
    ) -> ShiftAssessmentSummary:
        events = list(assigned_events)
        configured_points = (
            _resolve_point_ids(point_ids, rule_config)
            if point_ids is not None or rule_config is not None
            else self.point_ids
        )

        context = _resolve_context(events, shift=shift, window=window)
        team_id, shift_start, shift_end, window_start, window_end = context
        point_totals: dict[str, list[float]] = {
            point_id: [0.0, 0.0] for point_id in configured_points
        }

        total_score = 0.0
        for event in events:
            _validate_event_context(
                event,
                team_id,
                shift_start,
                shift_end,
                window_start,
                window_end,
                allow_multiple_windows=allow_multiple_windows,
            )
            point_id = _point_id(event)
            totals = point_totals.setdefault(point_id, [0.0, 0.0])
            totals[0] += 1
            totals[1] += event.score
            total_score += event.score

        by_point = {
            point_id: PointAssessmentSummary(
                point_id=point_id,
                event_count=int(totals[0]),
                score=totals[1],
            )
            for point_id, totals in point_totals.items()
        }
        return ShiftAssessmentSummary(
            team_id=team_id,
            shift_start=shift_start,
            shift_end=shift_end,
            window_start=window_start,
            window_end=window_end,
            event_count=len(events),
            total_score=total_score,
            by_point=by_point,
        )


def build_shift_summary(
    assigned_events: Iterable[AssignedAssessmentEvent],
    *,
    shift: Shift | None = None,
    window: TimeRange | None = None,
    point_ids: Iterable[str] | None = None,
    points: Iterable[object] | None = None,
    configured_points: Iterable[object] | None = None,
    rule_config: Mapping[str, object] | None = None,
    allow_multiple_windows: bool = False,
) -> ShiftAssessmentSummary:
    """Build one shift summary, optionally retaining zero-event configured points."""

    supplied_point_items = [
        item for item in (points, configured_points) if item is not None
    ]
    if len(supplied_point_items) > 1:
        raise ValueError("provide only one configured point collection")
    if supplied_point_items:
        if point_ids is not None:
            raise ValueError("provide either points or point_ids, not both")
        point_ids = _point_ids_from_items(supplied_point_items[0])
    return AssessmentSummarizer(
        point_ids=point_ids,
        rule_config=rule_config,
    ).summarize(
        assigned_events,
        shift=shift,
        window=window,
        allow_multiple_windows=allow_multiple_windows,
    )


def summarize_shift(
    assigned_events: Iterable[AssignedAssessmentEvent],
    **kwargs: object,
) -> ShiftAssessmentSummary:
    """Alias for :func:`build_shift_summary`."""

    return build_shift_summary(assigned_events, **kwargs)  # type: ignore[arg-type]


def summarize(
    assigned_events: Iterable[AssignedAssessmentEvent],
    **kwargs: object,
) -> ShiftAssessmentSummary:
    """Short alias for :func:`build_shift_summary`."""

    return build_shift_summary(assigned_events, **kwargs)  # type: ignore[arg-type]


summarize_assessment_events = build_shift_summary


def _resolve_context(
    events: list[AssignedAssessmentEvent],
    *,
    shift: Shift | None,
    window: TimeRange | None,
) -> tuple[str, datetime, datetime, datetime, datetime]:
    if events:
        first = events[0]
        team_id = first.team_id
        shift_start = first.shift_start
        shift_end = first.shift_end
        if shift is not None and (
            shift.team_id,
            shift.start_time,
            shift.end_time,
        ) != (team_id, shift_start, shift_end):
            raise ValueError("assigned events do not belong to the supplied shift")
        if window is None:
            if first.window_start is None or first.window_end is None:
                raise ValueError("assigned event has no assessment window context")
            window_start = first.window_start
            window_end = first.window_end
        else:
            window_start = window.start_time
            window_end = window.end_time
        return team_id, shift_start, shift_end, window_start, window_end

    if shift is None or window is None:
        raise ValueError("an empty summary requires shift and window context")
    return (
        shift.team_id,
        shift.start_time,
        shift.end_time,
        window.start_time,
        window.end_time,
    )


def _validate_event_context(
    event: AssignedAssessmentEvent,
    team_id: str,
    shift_start: datetime,
    shift_end: datetime,
    window_start: datetime,
    window_end: datetime,
    *,
    allow_multiple_windows: bool = False,
) -> None:
    if (event.team_id, event.shift_start, event.shift_end) != (
        team_id,
        shift_start,
        shift_end,
    ):
        raise ValueError("assigned events from multiple shifts cannot be summarized")
    if not allow_multiple_windows:
        if event.window_start is not None and event.window_start != window_start:
            raise ValueError(
                "assigned events from multiple assessment windows cannot be summarized"
            )
        if event.window_end is not None and event.window_end != window_end:
            raise ValueError(
                "assigned events from multiple assessment windows cannot be summarized"
            )


def _point_id(event: AssignedAssessmentEvent) -> str:
    point_id = event.data.get("point_id")
    if not isinstance(point_id, str) or not point_id:
        return "__unknown__"
    return point_id


def _resolve_point_ids(
    point_ids: Iterable[str] | None,
    rule_config: Mapping[str, object] | None,
) -> tuple[str, ...]:
    if point_ids is not None:
        return _normalise_point_ids(point_ids)
    if rule_config is not None:
        parameters = rule_config.get("parameters")
        if isinstance(parameters, Mapping):
            points = parameters.get("points")
            if isinstance(points, list):
                return _point_ids_from_items(points)
    return ()


def _point_ids_from_items(items: Iterable[object]) -> tuple[str, ...]:
    values: list[str] = []
    for item in items:
        if isinstance(item, str):
            point_id = item
        elif isinstance(item, Mapping) and isinstance(item.get("id"), str):
            point_id = item["id"]
        else:
            raise ValueError("summary point entries must be IDs or objects with id")
        values.append(point_id)
    return _normalise_point_ids(values)


def _normalise_point_ids(point_ids: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for point_id in point_ids:
        if not isinstance(point_id, str) or not point_id:
            raise ValueError("summary point IDs must be non-empty text")
        if point_id not in result:
            result.append(point_id)
    return tuple(result)

"""Pure streaming detector for slurry-feed versus SY-total balance."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite

from dcs_performance.data.history_quality import (
    NumericHistorySample,
    PreparedHistory,
    prepare_numeric_history,
    trailing_mean_segments,
)
from dcs_performance.data.models import HistorySample

from .config import PointConfig


FLOW_LOW = "flow_low"
FLOW_HIGH = "flow_high"


@dataclass(frozen=True)
class FlowBalancePoint:
    timestamp: datetime
    logic_flow: float
    sy_total: float
    difference: float


@dataclass(frozen=True)
class FlowBalanceTimelinePoint:
    """One detector-ready timestamp, retaining explicit quality boundaries."""

    timestamp: datetime
    point: FlowBalancePoint | None
    quality_boundary: bool


@dataclass(frozen=True)
class FlowBalanceOccurrence:
    start_time: datetime
    end_time: datetime
    direction: str
    mean_difference: float
    minimum_difference: float
    maximum_difference: float
    peak_difference: float
    is_open: bool


class FlowBalanceDetector:
    def detect(
        self,
        histories: Mapping[str, Iterable[HistorySample]],
        config: PointConfig,
        *,
        observation_end: datetime | None = None,
    ) -> list[FlowBalanceOccurrence]:
        return detect_flow_balance_events(
            histories,
            config,
            observation_end=observation_end,
        )


def detect_flow_balance_events(
    histories: Mapping[str, Iterable[HistorySample]],
    config: PointConfig,
    *,
    observation_end: datetime | None = None,
) -> list[FlowBalanceOccurrence]:
    timeline = build_flow_balance_timeline(histories, config)
    if not timeline:
        return []

    run: _Run | None = None
    previous_valid_time: datetime | None = None
    occurrences: list[FlowBalanceOccurrence] = []

    for observation in timeline:
        timestamp = observation.timestamp
        if observation.quality_boundary:
            if run is not None:
                close_time = run.pending_recovery or previous_valid_time
                if close_time is not None:
                    _append_occurrence(occurrences, run, close_time, config)
                run = None
            previous_valid_time = None

        point = observation.point
        if point is None:
            if run is not None:
                close_time = run.pending_recovery or previous_valid_time
                if close_time is not None:
                    _append_occurrence(occurrences, run, close_time, config)
                run = None
            previous_valid_time = None
            continue

        if (
            previous_valid_time is not None
            and (timestamp - previous_valid_time).total_seconds() > config.max_gap_seconds
        ):
            if run is not None:
                close_time = run.pending_recovery or previous_valid_time
                if close_time is not None:
                    _append_occurrence(occurrences, run, close_time, config)
                run = None

        direction = _direction(point.difference, config)
        if direction is None:
            if run is not None:
                run.recover(timestamp)
        elif run is None:
            run = _Run.start(point, direction)
        elif run.pending_recovery is not None:
            gap = (timestamp - run.pending_recovery).total_seconds()
            if direction == run.direction and gap <= config.merge_gap_seconds:
                run.resume(point)
            else:
                _append_occurrence(occurrences, run, run.pending_recovery, config)
                run = _Run.start(point, direction)
        elif direction != run.direction:
            _append_occurrence(occurrences, run, timestamp, config)
            run = _Run.start(point, direction)
        else:
            run.add(point)
        previous_valid_time = timestamp

    if run is not None:
        end_time = run.pending_recovery
        is_open = end_time is None
        if end_time is None:
            last_valid_time = previous_valid_time or timeline[-1].timestamp
            if observation_end is not None and (
                observation_end - last_valid_time
            ).total_seconds() <= config.max_gap_seconds:
                end_time = observation_end
            else:
                end_time = last_valid_time
                is_open = False
        _append_occurrence(occurrences, run, end_time, config, is_open=is_open)
    return occurrences


def calculate_flow_balance_points(
    histories: Mapping[str, Iterable[HistorySample]],
    config: PointConfig,
) -> list[FlowBalancePoint]:
    """Return the exact calculated evidence series used by event detection."""

    return [
        observation.point
        for observation in build_flow_balance_timeline(histories, config)
        if observation.point is not None
    ]


def build_flow_balance_timeline(
    histories: Mapping[str, Iterable[HistorySample]],
    config: PointConfig,
) -> list[FlowBalanceTimelinePoint]:
    """Prepare and align all TAGs once for both detection and visualization."""

    tags = (config.logic_tag, *config.sy_tags)
    prepared: dict[str, PreparedHistory] = {}
    smoothed: dict[str, list[NumericHistorySample]] = {}
    for tag in tags:
        raw = histories.get(tag, [])
        prepared[tag] = prepare_numeric_history(
            raw,
            parse_value=_parse_value,
            max_gap_seconds=config.max_gap_seconds,
        )
        if config.smoothing.enabled:
            smoothed[tag] = trailing_mean_segments(
                prepared[tag].segments,
                window_seconds=config.smoothing.window_seconds,
                min_samples=config.smoothing.min_samples,
            )
        else:
            smoothed[tag] = [
                sample
                for segment in prepared[tag].segments
                for sample in segment
            ]

    timeline = sorted(
        {
            sample.timestamp
            for tag in tags
            for sample in smoothed[tag]
        }
        | {
            timestamp
            for tag in tags
            for timestamp in prepared[tag].break_times
        }
    )
    current: dict[str, NumericHistorySample | None] = {tag: None for tag in tags}
    pointers = {tag: 0 for tag in tags}
    break_pointers = {tag: 0 for tag in tags}
    result: list[FlowBalanceTimelinePoint] = []

    for timestamp in timeline:
        quality_boundary = False
        for tag in tags:
            breaks = prepared[tag].break_times
            break_pointer = break_pointers[tag]
            while break_pointer < len(breaks) and breaks[break_pointer] <= timestamp:
                if current[tag] is not None:
                    quality_boundary = True
                current[tag] = None
                break_pointer += 1
            break_pointers[tag] = break_pointer

            values = smoothed[tag]
            pointer = pointers[tag]
            while pointer < len(values) and values[pointer].timestamp <= timestamp:
                if (
                    current[tag] is not None
                    and current[tag].segment_id != values[pointer].segment_id
                ):
                    quality_boundary = True
                current[tag] = values[pointer]
                pointer += 1
            pointers[tag] = pointer

        if not _all_current_values_are_valid(current, timestamp, config.max_gap_seconds):
            result.append(FlowBalanceTimelinePoint(timestamp, None, quality_boundary))
            continue

        logic_flow = current[config.logic_tag].value  # type: ignore[union-attr]
        sy_total = sum(
            current[tag].value  # type: ignore[union-attr]
            for tag in config.sy_tags
        )
        point = FlowBalancePoint(timestamp, logic_flow, sy_total, logic_flow - sy_total)
        result.append(FlowBalanceTimelinePoint(timestamp, point, quality_boundary))
    return result


@dataclass
class _Run:
    start_time: datetime
    direction: str
    values: list[float]
    pending_recovery: datetime | None = None

    @classmethod
    def start(cls, point: FlowBalancePoint, direction: str) -> _Run:
        return cls(point.timestamp, direction, [point.difference])

    def add(self, point: FlowBalancePoint) -> None:
        self.values.append(point.difference)

    def recover(self, timestamp: datetime) -> None:
        if self.pending_recovery is None:
            self.pending_recovery = timestamp

    def resume(self, point: FlowBalancePoint) -> None:
        self.pending_recovery = None
        self.add(point)


def _append_occurrence(
    occurrences: list[FlowBalanceOccurrence],
    run: _Run,
    end_time: datetime,
    config: PointConfig,
    *,
    is_open: bool = False,
) -> None:
    if end_time <= run.start_time:
        return
    duration = (end_time - run.start_time).total_seconds()
    if duration < config.min_duration_seconds:
        return
    occurrences.append(
        FlowBalanceOccurrence(
            start_time=run.start_time,
            end_time=end_time,
            direction=run.direction,
            mean_difference=sum(run.values) / len(run.values),
            minimum_difference=min(run.values),
            maximum_difference=max(run.values),
            peak_difference=(
                min(run.values) if run.direction == FLOW_LOW else max(run.values)
            ),
            is_open=is_open,
        )
    )


def _direction(difference: float, config: PointConfig) -> str | None:
    if difference < config.low_limit:
        return FLOW_LOW
    if difference > config.high_limit:
        return FLOW_HIGH
    return None


def _all_current_values_are_valid(
    current: Mapping[str, NumericHistorySample | None],
    timestamp: datetime,
    max_gap_seconds: float,
) -> bool:
    for item in current.values():
        if item is None:
            return False
        if (timestamp - item.timestamp).total_seconds() > max_gap_seconds:
            return False
        if not isfinite(item.value):
            return False
    return True


def _parse_value(value: str) -> float:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("flow value must be a non-empty number")
    try:
        result = float(value)
    except ValueError as exc:
        raise ValueError(f"invalid flow value: {value!r}") from exc
    if not isfinite(result):
        raise ValueError(f"invalid flow value: {value!r}")
    return result

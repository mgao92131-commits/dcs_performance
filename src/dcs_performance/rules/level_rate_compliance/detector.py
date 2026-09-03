"""Pure calculation and detection for sustained liquid-level rate events."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite

from dcs_performance.data.history_quality import (
    NumericHistorySample,
    prepare_numeric_history,
    trailing_mean_segments,
)
from dcs_performance.data.models import HistorySample

from .config import PointConfig, SmoothingConfig


RATE_DOWN = "rate_down"
RATE_UP = "rate_up"


@dataclass(frozen=True)
class RatePoint:
    timestamp: datetime
    smoothed_level: float
    rate_per_hour: float
    segment_id: int


@dataclass(frozen=True)
class LevelRateOccurrence:
    start_time: datetime
    end_time: datetime
    direction: str
    confirmation_time: datetime
    peak_rate: float
    mean_rate: float
    segment_id: int
    is_open: bool


class LevelRateDetector:
    """Calculate a 60-second-smoothed level rate and sustained events."""

    def calculate(self, samples: Iterable[HistorySample], config: PointConfig) -> list[RatePoint]:
        return calculate_rate_points(samples, config)

    def detect(
        self,
        samples: Iterable[HistorySample],
        config: PointConfig,
        *,
        observation_end: datetime | None = None,
    ) -> list[LevelRateOccurrence]:
        return detect_rate_events(
            calculate_rate_points(samples, config),
            config,
            observation_end=observation_end,
        )


def calculate_rate_points(
    samples: Iterable[HistorySample],
    config: PointConfig,
) -> list[RatePoint]:
    result: list[RatePoint] = []
    prepared = prepare_numeric_history(
        samples,
        parse_value=_parse_value,
        max_gap_seconds=config.max_gap_seconds,
    )
    for segment_id, segment in enumerate(prepared.segments):
        smoothed = _smooth_segment(segment, config.smoothing)
        smoothed_times = [timestamp for timestamp, _ in smoothed]
        for index, (timestamp, value) in enumerate(smoothed):
            target = timestamp - timedelta(seconds=config.rate_window_seconds)
            target_value = _interpolate(smoothed, smoothed_times, target, index)
            if target_value is None:
                continue
            rate = (value - target_value) / (config.rate_window_seconds / 3600.0)
            if isfinite(rate):
                result.append(
                    RatePoint(
                        timestamp=timestamp,
                        smoothed_level=value,
                        rate_per_hour=rate,
                        segment_id=segment_id,
                    )
                )
    return result


def detect_rate_events(
    rate_points: Iterable[RatePoint],
    config: PointConfig,
    *,
    observation_end: datetime | None = None,
) -> list[LevelRateOccurrence]:
    points = sorted(rate_points, key=lambda item: item.timestamp)
    if not points:
        return []

    occurrences: list[LevelRateOccurrence] = []
    run: _Run | None = None
    previous: RatePoint | None = None

    for point in points:
        if previous is not None and (
            point.segment_id != previous.segment_id
            or (point.timestamp - previous.timestamp).total_seconds() > config.max_gap_seconds
        ):
            if run is not None:
                _append_occurrence(
                    occurrences,
                    run,
                    run.pending_recovery or previous.timestamp,
                    config,
                )
                run = None

        direction = _direction(point.rate_per_hour, config)
        if direction is None:
            if run is not None:
                run.recover(point.timestamp)
        elif run is None:
            run = _Run.start(point, direction)
        elif run.pending_recovery is not None:
            gap = (point.timestamp - run.pending_recovery).total_seconds()
            if direction == run.direction and gap <= config.merge_gap_seconds:
                run.resume(point)
            else:
                _append_occurrence(occurrences, run, run.pending_recovery, config)
                run = _Run.start(point, direction)
        elif direction != run.direction:
            _append_occurrence(occurrences, run, point.timestamp, config)
            run = _Run.start(point, direction)
        else:
            run.add(point)
        previous = point

    if run is not None:
        end_time = run.pending_recovery
        is_open = end_time is None
        if end_time is None:
            if observation_end is not None and (
                observation_end - points[-1].timestamp
            ).total_seconds() <= config.max_gap_seconds:
                end_time = observation_end
            else:
                end_time = points[-1].timestamp
                is_open = False
        _append_occurrence(occurrences, run, end_time, config, is_open=is_open)
    return occurrences


@dataclass
class _Run:
    start_time: datetime
    direction: str
    values: list[float]
    segment_id: int
    pending_recovery: datetime | None = None

    @classmethod
    def start(cls, point: RatePoint, direction: str) -> _Run:
        return cls(
            point.timestamp,
            direction,
            [point.rate_per_hour],
            point.segment_id,
        )

    def add(self, point: RatePoint) -> None:
        self.values.append(point.rate_per_hour)

    def recover(self, timestamp: datetime) -> None:
        if self.pending_recovery is None:
            self.pending_recovery = timestamp

    def resume(self, point: RatePoint) -> None:
        self.pending_recovery = None
        self.add(point)


def _append_occurrence(
    occurrences: list[LevelRateOccurrence],
    run: _Run,
    end_time: datetime,
    config: PointConfig,
    *,
    is_open: bool = False,
) -> None:
    if end_time <= run.start_time:
        return
    duration = (end_time - run.start_time).total_seconds()
    if duration < config.persistence_seconds:
        return
    occurrence_end = end_time
    occurrences.append(
        LevelRateOccurrence(
            start_time=run.start_time,
            end_time=occurrence_end,
            direction=run.direction,
            confirmation_time=run.start_time + timedelta(seconds=config.persistence_seconds),
            peak_rate=(min(run.values) if run.direction == RATE_DOWN else max(run.values)),
            mean_rate=sum(run.values) / len(run.values),
            segment_id=run.segment_id,
            is_open=is_open,
        )
    )


def _direction(rate: float, config: PointConfig) -> str | None:
    if rate < config.lower_rate:
        return RATE_DOWN
    if rate > config.upper_rate:
        return RATE_UP
    return None


def _smooth_segment(
    samples: Sequence[NumericHistorySample],
    config: SmoothingConfig,
) -> list[tuple[datetime, float]]:
    if not config.enabled:
        return [(sample.timestamp, sample.value) for sample in samples]
    return [
        (sample.timestamp, sample.value)
        for sample in trailing_mean_segments(
            (samples,),
            window_seconds=config.window_seconds,
            min_samples=config.min_samples,
        )
    ]


def _interpolate(
    points: Sequence[tuple[datetime, float]],
    times: Sequence[datetime],
    target: datetime,
    current_index: int,
) -> float | None:
    if not points or target < points[0][0] or target > points[current_index][0]:
        return None
    right = bisect_left(times, target, 0, current_index + 1)
    if points[right][0] == target:
        return points[right][1]
    if right == 0:
        return None
    left = right - 1
    left_time, left_value = points[left]
    right_time, right_value = points[right]
    span = (right_time - left_time).total_seconds()
    if span <= 0:
        return left_value
    fraction = (target - left_time).total_seconds() / span
    return left_value + (right_value - left_value) * fraction


def _parse_value(value: str) -> float:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("level value must be a non-empty number")
    try:
        result = float(value)
    except ValueError as exc:
        raise ValueError(f"invalid level value: {value!r}") from exc
    if not isfinite(result):
        raise ValueError(f"invalid level value: {value!r}")
    return result

"""Pure preprocessing and disturbance detection for the viscosity rule."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

from dcs_performance.data.models import HistorySample
from dcs_performance.rules.analog_limit_exceedance.detector import (
    parse_analog_value,
)

from .config import ExclusionConfig


@dataclass(frozen=True)
class MinuteMedian:
    """One fixed one-minute bucket and its median value."""

    timestamp: datetime
    value: float
    sample_count: int


@dataclass(frozen=True)
class MetricPoint:
    """One calculated viscosity-proxy point."""

    timestamp: datetime
    value: float


@dataclass(frozen=True)
class DisturbanceWindow:
    """A detected core disturbance and its exclusion window."""

    core_start: datetime
    core_end: datetime
    remove_start: datetime
    remove_end: datetime


def aggregate_minute_medians(
    samples: Iterable[HistorySample],
    *,
    bucket_seconds: int = 60,
    min_samples: int = 1,
) -> list[MinuteMedian]:
    """Aggregate finite Historian values into fixed-time median buckets."""

    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be greater than zero")
    if min_samples <= 0:
        raise ValueError("min_samples must be greater than zero")

    buckets: dict[datetime, list[float]] = defaultdict(list)
    seen: set[tuple[datetime, int]] = set()
    for sample in samples:
        if not isinstance(sample, HistorySample):
            raise TypeError("history samples must contain HistorySample values")
        identity = (sample.timestamp, sample.sequence_no)
        if identity in seen:
            continue
        seen.add(identity)
        value = parse_analog_value(sample.value)
        buckets[_bucket_start(sample.timestamp, bucket_seconds)].append(value)

    return [
        MinuteMedian(timestamp=timestamp, value=float(median(values)), sample_count=len(values))
        for timestamp, values in sorted(buckets.items())
        if len(values) >= min_samples
    ]


def calculate_trailing_mean(
    minute_values: Iterable[MinuteMedian],
    *,
    bucket_seconds: int,
    window_seconds: int,
    min_samples: int,
) -> list[MetricPoint]:
    """Calculate a trailing mean only on complete consecutive buckets."""

    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be greater than zero")
    if window_seconds <= 0 or window_seconds % bucket_seconds != 0:
        raise ValueError("window_seconds must be a positive bucket multiple")
    if min_samples <= 0:
        raise ValueError("min_samples must be greater than zero")

    ordered = sorted(minute_values, key=lambda item: item.timestamp)
    window_buckets = window_seconds // bucket_seconds
    bucket_delta = timedelta(seconds=bucket_seconds)
    result: list[MetricPoint] = []
    for index in range(window_buckets - 1, len(ordered)):
        window = ordered[index - window_buckets + 1 : index + 1]
        if len(window) < min_samples:
            continue
        if any(
            current.timestamp != previous.timestamp + bucket_delta
            for previous, current in zip(window, window[1:])
        ):
            continue
        result.append(
            MetricPoint(
                timestamp=window[-1].timestamp,
                value=sum(item.value for item in window) / len(window),
            )
        )
    return result


def calculate_metric(
    samples: Iterable[HistorySample],
    *,
    bucket_seconds: int,
    bucket_min_samples: int,
    smoothing_enabled: bool,
    window_seconds: int,
    smoothing_min_samples: int,
) -> list[MetricPoint]:
    """Run the configured one-minute median and trailing-mean pipeline."""

    minute_values = aggregate_minute_medians(
        samples,
        bucket_seconds=bucket_seconds,
        min_samples=bucket_min_samples,
    )
    if not smoothing_enabled:
        return [MetricPoint(item.timestamp, item.value) for item in minute_values]
    return calculate_trailing_mean(
        minute_values,
        bucket_seconds=bucket_seconds,
        window_seconds=window_seconds,
        min_samples=smoothing_min_samples,
    )


def detect_disturbance_windows(
    metric_points: Iterable[MetricPoint],
    config: ExclusionConfig,
    *,
    bucket_seconds: int,
) -> list[DisturbanceWindow]:
    """Find robust-deviation cores and extend each from its start."""

    if not config.enabled:
        return []

    ordered = sorted(metric_points, key=lambda item: item.timestamp)
    if config.method == "rolling_range":
        return _detect_rolling_range_windows(
            ordered,
            config,
            bucket_seconds=bucket_seconds,
        )

    bucket_delta = timedelta(seconds=bucket_seconds)
    abnormal: list[tuple[datetime, datetime]] = []
    active_start: datetime | None = None
    previous: MetricPoint | None = None
    for point in ordered:
        is_contiguous = previous is not None and point.timestamp == previous.timestamp + bucket_delta
        is_abnormal = abs(point.value - config.baseline) > config.deviation_threshold
        if is_abnormal and (active_start is None or not is_contiguous):
            if active_start is not None and previous is not None:
                abnormal.append((active_start, previous.timestamp + bucket_delta))
            active_start = point.timestamp
        elif not is_abnormal and active_start is not None:
            abnormal.append((active_start, point.timestamp))
            active_start = None
        previous = point
    if active_start is not None and previous is not None:
        abnormal.append((active_start, previous.timestamp + bucket_delta))

    merged = _merge_intervals(abnormal, config.merge_gap_seconds)
    after_start = timedelta(seconds=config.remove_after_start_seconds)
    return [
        DisturbanceWindow(
            core_start=start,
            core_end=end,
            remove_start=start,
            remove_end=start + after_start,
        )
        for start, end in merged
    ]


def _detect_rolling_range_windows(
    ordered: list[MetricPoint],
    config: ExclusionConfig,
    *,
    bucket_seconds: int,
) -> list[DisturbanceWindow]:
    """Detect windows whose one-hour range exceeds the configured threshold.

    The metric is normally produced once per aggregation bucket.  A candidate
    starts at the largest observed adjacent change inside the triggering
    window, which is a better approximation of the disturbance onset than the
    time at which the rolling window becomes fully populated.
    """

    if config.window_seconds % bucket_seconds != 0:
        raise ValueError("rolling-range window must be a bucket multiple")
    window_buckets = config.window_seconds // bucket_seconds
    if window_buckets < 2 or len(ordered) < window_buckets:
        return []

    bucket_delta = timedelta(seconds=bucket_seconds)
    candidates: list[tuple[datetime, datetime]] = []
    for index in range(window_buckets - 1, len(ordered)):
        window = ordered[index - window_buckets + 1 : index + 1]
        if any(
            current.timestamp != previous.timestamp + bucket_delta
            for previous, current in zip(window, window[1:])
        ):
            continue
        values = [point.value for point in window]
        if max(values) - min(values) <= config.range_threshold:
            continue

        jump_index = max(
            range(1, len(window)),
            key=lambda item: abs(window[item].value - window[item - 1].value),
        )
        candidates.append(
            (
                window[jump_index].timestamp,
                window[-1].timestamp + bucket_delta,
            )
        )

    merged = _merge_intervals(candidates, config.merge_gap_seconds)
    after_start = timedelta(seconds=config.remove_after_start_seconds)
    return [
        DisturbanceWindow(
            core_start=start,
            core_end=end,
            remove_start=start,
            remove_end=start + after_start,
        )
        for start, end in merged
    ]


def exclude_disturbances(
    metric_points: Iterable[MetricPoint],
    windows: Iterable[DisturbanceWindow],
    *,
    bucket_seconds: int,
) -> list[MetricPoint]:
    """Remove disturbance buckets and preserve gaps for later detection."""

    window_list = list(windows)
    return [
        point
        for point in metric_points
        if not any(window.remove_start <= point.timestamp < window.remove_end for window in window_list)
    ]


def split_contiguous_segments(
    metric_points: Iterable[MetricPoint],
    *,
    bucket_seconds: int,
) -> list[list[MetricPoint]]:
    """Split at missing minute buckets so events cannot bridge data gaps."""

    ordered = sorted(metric_points, key=lambda item: item.timestamp)
    if not ordered:
        return []
    bucket_delta = timedelta(seconds=bucket_seconds)
    segments: list[list[MetricPoint]] = [[ordered[0]]]
    for point in ordered[1:]:
        if point.timestamp != segments[-1][-1].timestamp + bucket_delta:
            segments.append([])
        segments[-1].append(point)
    return segments


def _bucket_start(timestamp: datetime, bucket_seconds: int) -> datetime:
    midnight = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = int((timestamp - midnight).total_seconds())
    return midnight + timedelta(seconds=(elapsed // bucket_seconds) * bucket_seconds)


def _merge_intervals(
    intervals: list[tuple[datetime, datetime]],
    gap_seconds: float,
) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged: list[list[datetime]] = [[ordered[0][0], ordered[0][1]]]
    max_gap = timedelta(seconds=gap_seconds)
    for start, end in ordered[1:]:
        if start - merged[-1][1] <= max_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]

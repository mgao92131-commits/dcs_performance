"""Pure numeric preparation, rolling trends, and drift calculations.

Nothing in this module knows about shifts, scores, or ``AssessmentEvent``.
Raw Historian samples are converted into finite numeric segments first.  All
later calculations receive one segment at a time, which makes crossing a
quality hole impossible by construction.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite

from dcs_performance.data.models import HistorySample

from dcs_performance.rules.analog_trend_stability.config import (
    DriftConfig,
    DriftWindowConfig,
    TrendConfig,
)


@dataclass(frozen=True)
class NumericSample:
    """One finite value that is safe for trend mathematics."""

    timestamp: datetime
    value: float
    sequence_no: int


@dataclass(frozen=True)
class TrendPoint:
    """A PV, its time-window trend, and the resulting deviation."""

    timestamp: datetime
    pv: float
    trend: float
    deviation: float
    segment_id: int


@dataclass(frozen=True)
class DriftPoint:
    """The change between two trend values separated by one configured window."""

    timestamp: datetime
    window_id: str
    window_seconds: float
    change: float
    segment_id: int


def split_numeric_segments(
    samples: Iterable[HistorySample | NumericSample],
    max_gap_seconds: int | float,
) -> tuple[tuple[NumericSample, ...], ...]:
    """Convert raw samples into finite, quality-continuous numeric segments.

    The input order is intentional.  A timestamp that moves backwards is a
    quality boundary rather than something to silently sort away.  Invalid
    values, Historian/CR holes, manual edits, and gaps strictly larger than
    ``max_gap_seconds`` all terminate the current segment.  The invalid sample
    itself is never included in the numeric series.
    """

    gap_limit = _positive_number(max_gap_seconds, "max_gap_seconds")
    current: list[NumericSample] = []
    segments: list[tuple[NumericSample, ...]] = []
    previous_timestamp: datetime | None = None

    def close_segment() -> None:
        nonlocal current, previous_timestamp
        if current:
            segments.append(tuple(current))
        current = []
        previous_timestamp = None

    for raw_sample in samples:
        sample = _coerce_numeric_sample(raw_sample)
        if sample is None:
            close_segment()
            continue

        if previous_timestamp is not None:
            delta = (sample.timestamp - previous_timestamp).total_seconds()
            if delta < 0 or delta > gap_limit:
                close_segment()

        current.append(sample)
        previous_timestamp = sample.timestamp

    close_segment()
    return tuple(segments)


# Names that make the data-quality boundary easy to discover from callers.
build_numeric_segments = split_numeric_segments
numeric_segments = split_numeric_segments
prepare_numeric_segments = split_numeric_segments


def calculate_trend(
    samples: Sequence[NumericSample],
    config: TrendConfig,
    *,
    segment_id: int = 0,
) -> list[TrendPoint]:
    """Calculate the configured trend for one already-valid segment."""

    if not isinstance(config, TrendConfig):
        raise TypeError("trend config must be a TrendConfig")
    if config.method != "rolling_mean":
        raise ValueError(f"unsupported trend method: {config.method!r}")
    return rolling_mean(
        samples,
        window_seconds=config.window_seconds,
        min_samples=config.min_samples,
        alignment=config.alignment,
        segment_id=segment_id,
    )


def rolling_mean(
    samples: Sequence[NumericSample],
    window_seconds: int | float | TrendConfig,
    min_samples: int = 1,
    alignment: str = "centered",
    *,
    segment_id: int = 0,
) -> list[TrendPoint]:
    """Return time-window rolling means in linear time.

    ``centered`` uses ``[t-W/2, t+W/2]`` and ``trailing`` uses
    ``[t-W, t]``.  The endpoints are included when a sample exists there.
    The two moving indices and a running sum avoid assuming a fixed sampling
    period or rescanning every window.
    """

    if isinstance(window_seconds, TrendConfig):
        config = window_seconds
        window_seconds = config.window_seconds
        min_samples = config.min_samples
        alignment = config.alignment
    window = _positive_number(window_seconds, "window_seconds")
    if isinstance(min_samples, bool) or not isinstance(min_samples, int):
        raise ValueError("min_samples must be a positive integer")
    if min_samples <= 0:
        raise ValueError("min_samples must be a positive integer")
    if alignment not in {"centered", "trailing"}:
        raise ValueError("alignment must be 'centered' or 'trailing'")

    ordered = _validate_numeric_sequence(samples)
    if not ordered:
        return []

    timestamps = [sample.timestamp for sample in ordered]
    values = [sample.value for sample in ordered]
    result: list[TrendPoint] = []
    left = 0
    right = 0
    running_sum = 0.0

    for sample_index, sample in enumerate(ordered):
        if alignment == "centered":
            half_window = window / 2.0
            lower = sample.timestamp - timedelta(seconds=half_window)
            upper = sample.timestamp + timedelta(seconds=half_window)
        else:
            lower = sample.timestamp - timedelta(seconds=window)
            upper = sample.timestamp

        while left < right and timestamps[left] < lower:
            running_sum -= values[left]
            left += 1

        while right < len(ordered) and timestamps[right] <= upper:
            running_sum += values[right]
            right += 1

        sample_count = right - left
        if sample_count < min_samples:
            continue

        trend_value = running_sum / sample_count
        result.append(
            TrendPoint(
                timestamp=sample.timestamp,
                pv=sample.value,
                trend=trend_value,
                deviation=sample.value - trend_value,
                segment_id=segment_id,
            )
        )

    return result


def calculate_drift(
    trend_points: Sequence[TrendPoint],
    windows: Iterable[DriftWindowConfig] | DriftConfig,
) -> list[DriftPoint]:
    """Calculate every configured drift horizon using same-segment interpolation."""

    if isinstance(windows, DriftConfig):
        window_configs = windows.windows
    else:
        window_configs = tuple(windows)
    for window in window_configs:
        if not isinstance(window, DriftWindowConfig):
            raise TypeError("drift windows must contain DriftWindowConfig values")

    all_points = tuple(trend_points)
    _validate_trend_points(all_points)
    if not all_points or not window_configs:
        return []

    result: list[DriftPoint] = []
    points_by_segment: dict[int, list[TrendPoint]] = {}
    for point in all_points:
        points_by_segment.setdefault(point.segment_id, []).append(point)

    for segment_points in points_by_segment.values():
        ordered = _validate_trend_sequence(segment_points)
        for window in window_configs:
            interpolation_index = 0
            for point in ordered:
                target_time = point.timestamp - timedelta(seconds=window.window_seconds)
                previous_value, interpolation_index = _interpolate_with_pointer(
                    ordered,
                    target_time,
                    interpolation_index,
                )
                if previous_value is None:
                    continue
                result.append(
                    DriftPoint(
                        timestamp=point.timestamp,
                        window_id=window.id,
                        window_seconds=window.window_seconds,
                        change=point.trend - previous_value,
                        segment_id=point.segment_id,
                    )
                )

    result.sort(key=lambda item: (item.timestamp, item.window_id))
    return result


def interpolate_trend(
    trend_points: Sequence[TrendPoint],
    timestamp: datetime,
) -> float | None:
    """Linearly interpolate a trend at ``timestamp`` within one segment."""

    all_points = tuple(trend_points)
    _validate_trend_points(all_points)
    points_by_segment: dict[int, list[TrendPoint]] = {}
    for point in all_points:
        points_by_segment.setdefault(point.segment_id, []).append(point)
    for segment_points in points_by_segment.values():
        ordered = _validate_trend_sequence(segment_points)
        value, _ = _interpolate_with_pointer(ordered, timestamp, 0)
        if value is not None:
            return value
    return None


def calculate_trends_for_segments(
    segments: Iterable[Sequence[NumericSample]],
    config: TrendConfig,
) -> list[TrendPoint]:
    """Convenience helper for applying one trend configuration to many segments."""

    result: list[TrendPoint] = []
    for segment_id, segment in enumerate(segments):
        result.extend(calculate_trend(segment, config, segment_id=segment_id))
    return result


# Alternate verbs retained for small callers that use ``compute_*`` naming.
compute_trend = calculate_trend
compute_drift = calculate_drift


def _coerce_numeric_sample(
    sample: HistorySample | NumericSample,
) -> NumericSample | None:
    if isinstance(sample, NumericSample):
        if not isinstance(sample.timestamp, datetime):
            raise TypeError("NumericSample.timestamp must be a datetime")
        if isinstance(sample.sequence_no, bool) or not isinstance(sample.sequence_no, int):
            raise TypeError("NumericSample.sequence_no must be an integer")
        if not isfinite(sample.value):
            return None
        return sample

    if not isinstance(sample, HistorySample):
        raise TypeError("history input must contain HistorySample values")
    if not isinstance(sample.timestamp, datetime):
        raise TypeError("HistorySample.timestamp must be a datetime")
    if isinstance(sample.sequence_no, bool) or not isinstance(sample.sequence_no, int):
        raise TypeError("HistorySample.sequence_no must be an integer")
    if (
        sample.is_history_hole
        or sample.is_cr_hole
        or sample.is_manually_deleted
        or sample.is_manually_inserted
    ):
        return None

    value = _finite_float(sample.value)
    if value is None:
        return None
    return NumericSample(
        timestamp=sample.timestamp,
        value=value,
        sequence_no=sample.sequence_no,
    )


def _validate_numeric_sequence(
    samples: Sequence[NumericSample],
) -> tuple[NumericSample, ...]:
    ordered = tuple(samples)
    previous: datetime | None = None
    for sample in ordered:
        if not isinstance(sample, NumericSample):
            raise TypeError("trend input must contain NumericSample values")
        if not isinstance(sample.timestamp, datetime):
            raise TypeError("NumericSample.timestamp must be a datetime")
        if isinstance(sample.sequence_no, bool) or not isinstance(sample.sequence_no, int):
            raise TypeError("NumericSample.sequence_no must be an integer")
        if not isfinite(sample.value):
            raise ValueError("trend input values must be finite")
        if previous is not None and sample.timestamp < previous:
            raise ValueError("trend input must be ordered by timestamp")
        previous = sample.timestamp
    return ordered


def _validate_trend_sequence(
    trend_points: Sequence[TrendPoint],
) -> tuple[TrendPoint, ...]:
    ordered = tuple(trend_points)
    previous: datetime | None = None
    for point in ordered:
        if not isinstance(point, TrendPoint):
            raise TypeError("drift input must contain TrendPoint values")
        if not isinstance(point.timestamp, datetime):
            raise TypeError("TrendPoint.timestamp must be a datetime")
        if not all(isfinite(value) for value in (point.pv, point.trend, point.deviation)):
            raise ValueError("trend point values must be finite")
        if previous is not None and point.timestamp < previous:
            raise ValueError("trend points must be ordered by timestamp")
        previous = point.timestamp
    return ordered


def _validate_trend_points(trend_points: Sequence[TrendPoint]) -> None:
    for point in trend_points:
        if not isinstance(point, TrendPoint):
            raise TypeError("drift input must contain TrendPoint values")
        if not isinstance(point.timestamp, datetime):
            raise TypeError("TrendPoint.timestamp must be a datetime")
        if not all(isfinite(value) for value in (point.pv, point.trend, point.deviation)):
            raise ValueError("trend point values must be finite")


def _interpolate_with_pointer(
    points: Sequence[TrendPoint],
    target_time: datetime,
    pointer: int,
) -> tuple[float | None, int]:
    if not points or target_time < points[0].timestamp:
        return None, pointer
    if target_time > points[-1].timestamp:
        return None, pointer

    pointer = min(max(pointer, 0), len(points) - 1)
    while pointer + 1 < len(points) and points[pointer + 1].timestamp <= target_time:
        pointer += 1

    left = points[pointer]
    if left.timestamp == target_time:
        return left.trend, pointer
    if pointer + 1 >= len(points):
        return None, pointer

    right = points[pointer + 1]
    if right.timestamp < target_time:
        return None, pointer
    span = (right.timestamp - left.timestamp).total_seconds()
    if span <= 0:
        return right.trend, pointer + 1
    fraction = (target_time - left.timestamp).total_seconds() / span
    return left.trend + (right.trend - left.trend) * fraction, pointer


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return numeric if isfinite(numeric) else None


def _positive_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a positive finite number")
    numeric = float(value)
    if not isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{field_name} must be a positive finite number")
    return numeric

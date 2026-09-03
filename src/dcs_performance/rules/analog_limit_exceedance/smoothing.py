"""Time-based preprocessing for analog limit assessment."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime

from dcs_performance.data.history_quality import (
    NumericHistorySample,
    PreparedHistory,
    prepare_numeric_history,
    trailing_mean_segments,
)
from dcs_performance.data.models import HistorySample
from dcs_performance.rules.analog_limit_exceedance.config import SmoothingConfig
from dcs_performance.rules.analog_limit_exceedance.detector import parse_analog_value


@dataclass(frozen=True)
class SmoothedHistory:
    """Smoothed valid samples grouped by quality-continuous segment."""

    segments: tuple[tuple[HistorySample, ...], ...]
    terminated_by_boundary: tuple[bool, ...]


def smooth_history_segments(
    samples: Iterable[HistorySample],
    config: SmoothingConfig,
    *,
    max_gap_seconds: int | float | None = None,
) -> SmoothedHistory:
    """Filter quality-invalid samples and smooth each valid segment alone."""

    if not isinstance(config, SmoothingConfig):
        raise TypeError("config must be a SmoothingConfig")
    if config.enabled and config.method != "trailing_mean":
        raise ValueError(f"unsupported smoothing method: {config.method!r}")

    raw_samples = list(samples)
    prepared = prepare_numeric_history(
        raw_samples,
        parse_value=parse_analog_value,
        max_gap_seconds=max_gap_seconds,
    )
    source_by_key: dict[tuple[datetime, int], HistorySample] = {}
    for sample in raw_samples:
        source_by_key.setdefault((sample.timestamp, sample.sequence_no), sample)

    segments: list[tuple[HistorySample, ...]] = []
    terminated_by_boundary: list[bool] = []
    for index, segment in enumerate(prepared.segments):
        numeric_values: list[NumericHistorySample]
        if config.enabled:
            numeric_values = trailing_mean_segments(
                (segment,),
                window_seconds=config.window_seconds,
                min_samples=config.min_samples,
            )
        else:
            numeric_values = list(segment)
        if not numeric_values:
            continue

        converted = tuple(
            (
                replace(
                    source_by_key[(sample.timestamp, sample.sequence_no)],
                    value=f"{sample.value:.12g}",
                )
                if config.enabled
                else source_by_key[(sample.timestamp, sample.sequence_no)]
            )
            for sample in numeric_values
        )
        segments.append(converted)
        terminated_by_boundary.append(
            _raw_segment_has_boundary(prepared, index)
        )

    return SmoothedHistory(tuple(segments), tuple(terminated_by_boundary))


def smooth_history_samples(
    samples: Iterable[HistorySample],
    config: SmoothingConfig,
) -> list[HistorySample]:
    """Apply smoothing and flatten the resulting quality-continuous segments."""

    prepared = smooth_history_segments(samples, config)
    return [sample for segment in prepared.segments for sample in segment]


def _raw_segment_has_boundary(
    prepared: PreparedHistory,
    segment_index: int,
) -> bool:
    segment = prepared.segments[segment_index]
    last_timestamp = segment[-1].timestamp
    next_timestamp = (
        prepared.segments[segment_index + 1][0].timestamp
        if segment_index + 1 < len(prepared.segments)
        else None
    )
    return any(
        break_time >= last_timestamp
        and (next_timestamp is None or break_time <= next_timestamp)
        for break_time in prepared.break_times
    )

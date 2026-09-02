"""Time-based preprocessing for analog limit assessment."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Iterable

from dcs_performance.data.models import HistorySample
from dcs_performance.rules.analog_limit_exceedance.config import SmoothingConfig
from dcs_performance.rules.analog_limit_exceedance.detector import parse_analog_value


def smooth_history_samples(
    samples: Iterable[HistorySample],
    config: SmoothingConfig,
) -> list[HistorySample]:
    """Apply a trailing mean while preserving sample timestamps and metadata."""

    if not isinstance(config, SmoothingConfig):
        raise TypeError("config must be a SmoothingConfig")

    ordered = _ordered_unique_samples(samples)
    if not config.enabled:
        return ordered
    if config.method != "trailing_mean":
        raise ValueError(f"unsupported smoothing method: {config.method!r}")

    horizon = timedelta(seconds=config.window_seconds)
    window: deque[tuple[datetime, float]] = deque()
    running_sum = 0.0
    result: list[HistorySample] = []

    for sample in ordered:
        value = parse_analog_value(sample.value)
        window.append((sample.timestamp, value))
        running_sum += value
        cutoff = sample.timestamp - horizon
        while window and window[0][0] < cutoff:
            _, removed = window.popleft()
            running_sum -= removed
        if len(window) < config.min_samples:
            continue
        result.append(replace(sample, value=f"{running_sum / len(window):.12g}"))
    return result


def _ordered_unique_samples(
    samples: Iterable[HistorySample],
) -> list[HistorySample]:
    unique: dict[tuple[datetime, int], HistorySample] = {}
    for sample in samples:
        if not isinstance(sample, HistorySample):
            raise TypeError("smoothing input must contain HistorySample values")
        unique.setdefault((sample.timestamp, sample.sequence_no), sample)
    return sorted(
        unique.values(),
        key=lambda sample: (sample.timestamp, sample.sequence_no),
    )

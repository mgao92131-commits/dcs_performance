"""Shared Historian quality filtering and numeric segmentation helpers.

The DCS service keeps values as text and exposes quality information beside
each value.  Assessment rules must make that boundary before doing any
numeric aggregation or interpolation; otherwise a flagged value can silently
participate in a trend, or an old value can be carried across a quality hole.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite

from .models import HistorySample


GOOD_DELTA_V_STATUS = "Good"
SERVICE_GOOD_DELTA_V_STATUS = "DeltaV.Historian.Data.DeltaVStatus"
GOOD_DELTA_V_STATUSES = frozenset(
    {GOOD_DELTA_V_STATUS, SERVICE_GOOD_DELTA_V_STATUS}
)
VALID_ARCHIVE_STATUS = "HistoryDataIsValid"

ValueParser = Callable[[str], int | float]


@dataclass(frozen=True)
class NumericHistorySample:
    """One finite value that passed the Historian quality boundary."""

    timestamp: datetime
    value: float
    sequence_no: int
    segment_id: int = 0


@dataclass(frozen=True)
class PreparedHistory:
    """Quality-clean numeric segments and the boundaries that created them."""

    segments: tuple[tuple[NumericHistorySample, ...], ...]
    break_times: tuple[datetime, ...]


def is_usable_history_sample(sample: HistorySample) -> bool:
    """Return whether a raw sample is eligible for numeric calculations."""

    return (
        sample.delta_v_status in GOOD_DELTA_V_STATUSES
        and sample.archive_status == VALID_ARCHIVE_STATUS
        and not sample.is_history_hole
        and not sample.is_cr_hole
        and not sample.is_manually_deleted
        and not sample.is_manually_inserted
    )


def prepare_numeric_history(
    samples: Iterable[HistorySample],
    *,
    parse_value: ValueParser,
    max_gap_seconds: int | float | None = None,
) -> PreparedHistory:
    """Filter raw history and return finite, quality-continuous segments.

    Invalid quality samples and values are treated as boundaries, not as
    zeros and not as exceptions from the assessment algorithm.  A configured
    time gap is also a boundary.  Exact duplicates are removed by
    ``(timestamp, sequence_no)`` while distinct sequences at one timestamp
    remain available to the caller.
    """

    if not callable(parse_value):
        raise TypeError("parse_value must be callable")
    gap_limit = _validate_gap(max_gap_seconds)

    ordered = _normalise_samples(samples)
    segments: list[tuple[NumericHistorySample, ...]] = []
    break_times: set[datetime] = set()
    current: list[NumericHistorySample] = []
    previous_timestamp: datetime | None = None
    segment_id = 0

    def close_segment() -> None:
        nonlocal current, previous_timestamp, segment_id
        if current:
            segments.append(tuple(current))
            segment_id += 1
        current = []
        previous_timestamp = None

    for sample in ordered:
        if not is_usable_history_sample(sample):
            break_times.add(sample.timestamp)
            close_segment()
            continue

        try:
            parsed = parse_value(sample.value)
        except (TypeError, ValueError, OverflowError):
            # A malformed value has the same mathematical effect as a
            # quality hole: it must not bridge two valid portions of history.
            break_times.add(sample.timestamp)
            close_segment()
            continue

        value = float(parsed)
        if not isfinite(value):
            break_times.add(sample.timestamp)
            close_segment()
            continue

        if previous_timestamp is not None:
            delta = (sample.timestamp - previous_timestamp).total_seconds()
            if delta < 0 or (gap_limit is not None and delta > gap_limit):
                break_times.add(sample.timestamp)
                close_segment()

        current.append(
            NumericHistorySample(
                timestamp=sample.timestamp,
                value=value,
                sequence_no=sample.sequence_no,
                segment_id=segment_id,
            )
        )
        previous_timestamp = sample.timestamp

    close_segment()
    return PreparedHistory(tuple(segments), tuple(sorted(break_times)))


def trailing_mean_segments(
    segments: Iterable[Sequence[NumericHistorySample]],
    *,
    window_seconds: int | float,
    min_samples: int,
) -> list[NumericHistorySample]:
    """Apply a trailing time-window mean independently to each segment."""

    if window_seconds <= 0:
        raise ValueError("window_seconds must be greater than zero")
    if isinstance(min_samples, bool) or not isinstance(min_samples, int) or min_samples <= 0:
        raise ValueError("min_samples must be a positive integer")

    result: list[NumericHistorySample] = []
    horizon = timedelta(seconds=window_seconds)
    for segment in segments:
        window: deque[NumericHistorySample] = deque()
        running_sum = 0.0
        for sample in segment:
            window.append(sample)
            running_sum += sample.value
            cutoff = sample.timestamp - horizon
            while window and window[0].timestamp < cutoff:
                running_sum -= window.popleft().value
            if len(window) < min_samples:
                continue
            result.append(
                NumericHistorySample(
                    timestamp=sample.timestamp,
                    value=running_sum / len(window),
                    sequence_no=sample.sequence_no,
                    segment_id=sample.segment_id,
                )
            )
    return result


# Concise aliases make the shared boundary discoverable from rule code and
# preserve a vocabulary close to the existing analog-trend implementation.
split_valid_numeric_segments = prepare_numeric_history
build_numeric_segments = prepare_numeric_history


def _normalise_samples(samples: Iterable[HistorySample]) -> list[HistorySample]:
    unique: dict[tuple[datetime, int], HistorySample] = {}
    timezone_aware: bool | None = None
    for sample in samples:
        if not isinstance(sample, HistorySample):
            raise TypeError("history input must contain HistorySample values")
        if not isinstance(sample.timestamp, datetime):
            raise TypeError("HistorySample.timestamp must be a datetime")
        sample_timezone_aware = sample.timestamp.tzinfo is not None
        if timezone_aware is None:
            timezone_aware = sample_timezone_aware
        elif sample_timezone_aware != timezone_aware:
            raise ValueError(
                "sample datetimes must all be timezone-naive or timezone-aware"
            )
        if isinstance(sample.sequence_no, bool) or not isinstance(sample.sequence_no, int):
            raise TypeError("HistorySample.sequence_no must be an integer")
        unique.setdefault((sample.timestamp, sample.sequence_no), sample)
    return sorted(unique.values(), key=lambda item: (item.timestamp, item.sequence_no))


def _validate_gap(value: int | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("max_gap_seconds must be a positive finite number")
    result = float(value)
    if not isfinite(result) or result <= 0:
        raise ValueError("max_gap_seconds must be a positive finite number")
    return result

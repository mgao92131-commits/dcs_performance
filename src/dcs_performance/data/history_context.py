"""Helpers for reading a range together with its latest preceding state."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from .client import DcsDataClient
from .models import HistorySample


DEFAULT_LOOKBACK_STEPS = (
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
    timedelta(hours=48),
)


def get_history_with_previous_sample(
    client: DcsDataClient,
    tag: str,
    start_time: datetime,
    end_time: datetime,
    *,
    lookback_steps: tuple[timedelta, ...] = DEFAULT_LOOKBACK_STEPS,
) -> list[HistorySample]:
    """Return the latest pre-range sample followed by range samples.

    The normal request is always made first for ``[start_time, end_time)``.
    If that response does not contain a sample before ``start_time``, the
    helper tries the configured lookback ranges in order.  Only one previous
    sample is retained; all returned samples in the requested range are
    retained.  Queries and DCS exceptions are deliberately not swallowed.

    The function uses ``(timestamp, sequence_no)`` as its sample identity, so
    duplicate responses from overlapping lookback requests are removed while
    distinct Historian sequences at one timestamp remain distinct.
    """

    _validate_arguments(client, tag, start_time, end_time, lookback_steps)

    collected: dict[tuple[datetime, int], HistorySample] = {}

    def add_samples(samples: Iterable[HistorySample]) -> None:
        for sample in samples:
            _validate_sample(sample)
            key = (sample.timestamp, sample.sequence_no)
            # Keep the first copy deterministically if overlapping calls
            # return the same Historian sample more than once.
            collected.setdefault(key, sample)

    add_samples(client.get_history(tag, start_time, end_time))
    if not _has_previous_sample(collected.values(), start_time):
        for step in lookback_steps:
            lookback_start = start_time - step
            add_samples(client.get_history(tag, lookback_start, start_time))
            if _has_previous_sample(collected.values(), start_time):
                break

    previous = _latest_previous_sample(collected.values(), start_time)
    in_range = [
        sample
        for sample in collected.values()
        if start_time <= sample.timestamp < end_time
    ]
    in_range.sort(key=_sample_sort_key)

    if previous is None:
        return in_range
    return [previous, *in_range]


class HistoryContext:
    """Small reusable object form of :func:`get_history_with_previous_sample`."""

    def __init__(
        self,
        client: DcsDataClient,
        *,
        lookback_steps: tuple[timedelta, ...] = DEFAULT_LOOKBACK_STEPS,
    ) -> None:
        _validate_client(client)
        _validate_lookback_steps(lookback_steps)
        self.client = client
        self.lookback_steps = lookback_steps

    def get_history_with_previous_sample(
        self,
        tag: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[HistorySample]:
        return get_history_with_previous_sample(
            self.client,
            tag,
            start_time,
            end_time,
            lookback_steps=self.lookback_steps,
        )


def _validate_arguments(
    client: DcsDataClient,
    tag: str,
    start_time: datetime,
    end_time: datetime,
    lookback_steps: tuple[timedelta, ...],
) -> None:
    _validate_client(client)
    if not isinstance(tag, str) or not tag:
        raise ValueError("tag must be non-empty text")
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        raise TypeError("start_time and end_time must be datetime values")
    if (start_time.tzinfo is None) != (end_time.tzinfo is None):
        raise ValueError(
            "start_time and end_time must both be timezone-naive or timezone-aware"
        )
    if end_time <= start_time:
        raise ValueError("end_time must be after start_time")
    _validate_lookback_steps(lookback_steps)


def _validate_client(client: DcsDataClient) -> None:
    if client is None:
        raise ValueError("data client must not be None")
    if not callable(getattr(client, "get_history", None)):
        raise TypeError("data client must provide get_history()")


def _validate_lookback_steps(lookback_steps: tuple[timedelta, ...]) -> None:
    if not isinstance(lookback_steps, tuple):
        raise TypeError("lookback_steps must be a tuple of timedeltas")
    for step in lookback_steps:
        if not isinstance(step, timedelta) or step <= timedelta(0):
            raise ValueError("lookback steps must be positive timedeltas")


def _validate_sample(sample: HistorySample) -> None:
    if not isinstance(sample, HistorySample):
        raise TypeError("get_history() must return HistorySample values")
    if not isinstance(sample.timestamp, datetime):
        raise TypeError("HistorySample.timestamp must be a datetime")
    if isinstance(sample.sequence_no, bool) or not isinstance(sample.sequence_no, int):
        raise TypeError("HistorySample.sequence_no must be an integer")


def _sample_sort_key(sample: HistorySample) -> tuple[datetime, int]:
    return sample.timestamp, sample.sequence_no


def _has_previous_sample(
    samples: Iterable[HistorySample],
    start_time: datetime,
) -> bool:
    return any(sample.timestamp < start_time for sample in samples)


def _latest_previous_sample(
    samples: Iterable[HistorySample],
    start_time: datetime,
) -> HistorySample | None:
    previous = [sample for sample in samples if sample.timestamp < start_time]
    if not previous:
        return None
    return max(previous, key=_sample_sort_key)

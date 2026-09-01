"""Helpers for reading a range together with its latest preceding state."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import datetime, timedelta

from .client import DcsDataClient
from .models import HistorySample


DEFAULT_LOOKBACK_STEPS = (
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
    timedelta(hours=48),
)

# These are cumulative forward horizons from the logical search start.  The
# helper turns them into non-overlapping requests, so a later chunk starts at
# the cursor reached by the previous chunk.
DEFAULT_FORWARD_SEARCH_STEPS = (
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
    timedelta(hours=48),
)
DEFAULT_RECOVERY_SEARCH_STEPS = DEFAULT_FORWARD_SEARCH_STEPS


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
        for lookback_start, lookback_end in _iter_reverse_lookback_ranges(
            start_time,
            lookback_steps,
        ):
            add_samples(client.get_history(tag, lookback_start, lookback_end))
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


def get_histories_with_previous_samples(
    client: DcsDataClient,
    tags: list[str],
    start_time: datetime,
    end_time: datetime,
    *,
    lookback_steps: tuple[timedelta, ...] = DEFAULT_LOOKBACK_STEPS,
) -> dict[str, list[HistorySample]]:
    """Read several TAGs together with one preceding sample per TAG.

    The range request is made through ``get_histories()`` so the data client
    can apply its own concurrency limit.  Lookback requests are also batched,
    and only TAGs that still lack a sample before ``start_time`` are queried.
    The returned value has the same shape as ``get_histories``; when a
    preceding sample exists it is placed before the sorted in-range samples.

    This helper deliberately does not invent a value for a TAG with no
    history context.  Rules can therefore keep an unknown initial state
    instead of accidentally treating missing data as zero.
    """

    _validate_multi_history_arguments(
        client,
        tags,
        start_time,
        end_time,
        lookback_steps,
    )
    unique_tags = list(dict.fromkeys(tags))
    if not unique_tags:
        return {}

    collected: dict[str, dict[tuple[datetime, int], HistorySample]] = {
        tag: {} for tag in unique_tags
    }

    def add_response(response: object, requested_tags: list[str]) -> None:
        if not isinstance(response, Mapping):
            raise TypeError("get_histories() must return a mapping of TAG histories")
        for tag in requested_tags:
            samples = response.get(tag, [])
            if samples is None:
                samples = []
            if isinstance(samples, (str, bytes)):
                raise TypeError(
                    f"get_histories()[{tag!r}] must be an iterable of HistorySample"
                )
            for sample in samples:
                _validate_sample(sample)
                collected[tag].setdefault(
                    (sample.timestamp, sample.sequence_no),
                    sample,
                )

    add_response(
        client.get_histories(unique_tags, start_time, end_time),
        unique_tags,
    )
    missing = [
        tag
        for tag in unique_tags
        if not _has_previous_sample(collected[tag].values(), start_time)
    ]

    for lookback_start, lookback_end in _iter_reverse_lookback_ranges(
        start_time,
        lookback_steps,
    ):
        if not missing:
            break
        add_response(
            client.get_histories(missing, lookback_start, lookback_end),
            missing,
        )
        missing = [
            tag
            for tag in missing
            if not _has_previous_sample(collected[tag].values(), start_time)
        ]

    result: dict[str, list[HistorySample]] = {}
    for tag in unique_tags:
        values = collected[tag].values()
        previous = _latest_previous_sample(values, start_time)
        in_range = sorted(
            (
                sample
                for sample in collected[tag].values()
                if start_time <= sample.timestamp < end_time
            ),
            key=_sample_sort_key,
        )
        result[tag] = in_range if previous is None else [previous, *in_range]
    return result


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

    def get_histories_with_previous_samples(
        self,
        tags: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, list[HistorySample]]:
        return get_histories_with_previous_samples(
            self.client,
            tags,
            start_time,
            end_time,
            lookback_steps=self.lookback_steps,
        )

    def find_next_sample(
        self,
        tag: str,
        start_time: datetime,
        predicate: Callable[[HistorySample], bool],
        *,
        search_steps: tuple[timedelta, ...] = DEFAULT_FORWARD_SEARCH_STEPS,
        cursor_time: datetime | None = None,
    ) -> HistorySample | None:
        """Find the first matching sample in non-overlapping future chunks."""

        return find_next_sample(
            self.client,
            tag,
            start_time,
            predicate,
            search_steps=search_steps,
            cursor_time=cursor_time,
        )


def find_next_sample(
    client: DcsDataClient,
    tag: str,
    start_time: datetime,
    predicate: Callable[[HistorySample], bool],
    *,
    search_steps: tuple[timedelta, ...] = DEFAULT_FORWARD_SEARCH_STEPS,
    cursor_time: datetime | None = None,
) -> HistorySample | None:
    """Find the first matching History sample in cumulative future horizons.

    ``search_steps`` contains cumulative horizons from ``start_time``.  For
    example, the default creates requests ``[start, +30m)``, ``[+30m, +2h)``,
    ``[+2h, +12h)``, and ``[+12h, +48h)``.  ``cursor_time`` identifies the
    first point not already covered by an earlier query; it is useful when the
    caller has already read ``[start_time, cursor_time)``.

    The earliest matching sample is returned in ``(timestamp, sequence_no)``
    order.  Data-client errors and predicate errors are intentionally allowed
    to propagate to the caller.
    """

    _validate_forward_search_arguments(
        client,
        tag,
        start_time,
        predicate,
        search_steps,
        cursor_time,
    )
    cursor = cursor_time or start_time

    for horizon in search_steps:
        horizon_end = start_time + horizon
        if horizon_end <= cursor:
            continue

        query_start = cursor
        samples = client.get_history(tag, query_start, horizon_end)
        ordered = _normalise_samples(samples)
        for sample in ordered:
            if not (query_start <= sample.timestamp < horizon_end):
                continue
            if sample.timestamp <= start_time:
                continue
            if predicate(sample):
                return sample
        cursor = horizon_end

    return None


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


def _iter_reverse_lookback_ranges(
    start_time: datetime,
    lookback_steps: tuple[timedelta, ...],
) -> Iterator[tuple[datetime, datetime]]:
    """Yield nearest-first, non-overlapping lookback ranges.

    Lookback steps are cumulative horizons. Each gap is queried from the
    nearest boundary backwards. The ranges are business search horizons; the
    dcs-service owns any internal stream windowing.
    """

    ordered_boundaries = [timedelta(0), *lookback_steps]
    for previous_horizon, horizon in zip(
        ordered_boundaries,
        ordered_boundaries[1:],
    ):
        yield start_time - horizon, start_time - previous_horizon


def _validate_multi_history_arguments(
    client: DcsDataClient,
    tags: list[str],
    start_time: datetime,
    end_time: datetime,
    lookback_steps: tuple[timedelta, ...],
) -> None:
    _validate_histories_client(client)
    if not isinstance(tags, list):
        raise TypeError("tags must be a list of strings")
    for tag in tags:
        if not isinstance(tag, str) or not tag:
            raise ValueError("tags must contain non-empty text")
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


def _validate_histories_client(client: DcsDataClient) -> None:
    if client is None:
        raise ValueError("data client must not be None")
    if not callable(getattr(client, "get_histories", None)):
        raise TypeError("data client must provide get_histories()")


def _validate_lookback_steps(lookback_steps: tuple[timedelta, ...]) -> None:
    if not isinstance(lookback_steps, tuple):
        raise TypeError("lookback_steps must be a tuple of timedeltas")
    previous = timedelta(0)
    for step in lookback_steps:
        if not isinstance(step, timedelta) or step <= previous:
            raise ValueError(
                "lookback steps must contain strictly increasing positive timedeltas"
            )
        previous = step


def _validate_forward_search_arguments(
    client: DcsDataClient,
    tag: str,
    start_time: datetime,
    predicate: Callable[[HistorySample], bool],
    search_steps: tuple[timedelta, ...],
    cursor_time: datetime | None,
) -> None:
    _validate_client(client)
    if not isinstance(tag, str) or not tag:
        raise ValueError("tag must be non-empty text")
    if not isinstance(start_time, datetime):
        raise TypeError("start_time must be a datetime value")
    if not callable(predicate):
        raise TypeError("predicate must be callable")
    _validate_forward_search_steps(search_steps)

    if cursor_time is not None:
        if not isinstance(cursor_time, datetime):
            raise TypeError("cursor_time must be a datetime value")
        if (cursor_time.tzinfo is None) != (start_time.tzinfo is None):
            raise ValueError(
                "start_time and cursor_time must both be timezone-naive or timezone-aware"
            )
        if cursor_time < start_time:
            raise ValueError("cursor_time must not be before start_time")


def _validate_forward_search_steps(search_steps: tuple[timedelta, ...]) -> None:
    if not isinstance(search_steps, tuple):
        raise TypeError("search_steps must be a tuple of timedeltas")
    previous = timedelta(0)
    for step in search_steps:
        if not isinstance(step, timedelta) or step <= previous:
            raise ValueError(
                "search_steps must contain strictly increasing positive timedeltas"
            )
        previous = step


def _validate_sample(sample: HistorySample) -> None:
    if not isinstance(sample, HistorySample):
        raise TypeError("get_history() must return HistorySample values")
    if not isinstance(sample.timestamp, datetime):
        raise TypeError("HistorySample.timestamp must be a datetime")
    if isinstance(sample.sequence_no, bool) or not isinstance(sample.sequence_no, int):
        raise TypeError("HistorySample.sequence_no must be an integer")


def _sample_sort_key(sample: HistorySample) -> tuple[datetime, int]:
    return sample.timestamp, sample.sequence_no


def _normalise_samples(samples: Iterable[HistorySample]) -> list[HistorySample]:
    unique: dict[tuple[datetime, int], HistorySample] = {}
    for sample in samples:
        _validate_sample(sample)
        unique.setdefault((sample.timestamp, sample.sequence_no), sample)
    return sorted(unique.values(), key=_sample_sort_key)


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

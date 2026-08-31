"""Pure state-machine detection for persistent digital high alarms."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from math import isfinite

from dcs_performance.data.models import HistorySample


class DigitalStateParseError(ValueError):
    """Raised when a Historian value is not an explicit digital state."""


def parse_digital_state(value: str) -> int:
    """Parse a digital state without treating non-empty text as ``True``.

    Integer and common decimal spellings of zero and one are accepted.  Any
    other value is rejected explicitly, including ``ON``, ``BAD`` and empty
    text.
    """

    if not isinstance(value, str):
        raise DigitalStateParseError(
            f"digital state must be text containing 0 or 1; got {value!r}"
        )
    text = value.strip()
    if not text:
        raise DigitalStateParseError("digital state must not be empty")
    try:
        numeric = Decimal(text)
    except InvalidOperation as exc:
        raise DigitalStateParseError(
            f"unknown digital state {value!r}; expected 0 or 1"
        ) from exc
    if not numeric.is_finite() or numeric not in {Decimal(0), Decimal(1)}:
        raise DigitalStateParseError(
            f"unknown digital state {value!r}; expected 0 or 1"
        )
    return int(numeric)


@dataclass(frozen=True)
class AlarmOccurrence:
    """One continuous high interval observed by the state machine."""

    point_id: str
    history_tag: str
    start_time: datetime
    end_time: datetime | None
    duration_seconds: float | None
    is_open: bool

    def __post_init__(self) -> None:
        if not isinstance(self.point_id, str) or not self.point_id:
            raise ValueError("point_id must be non-empty text")
        if not isinstance(self.history_tag, str) or not self.history_tag:
            raise ValueError("history_tag must be non-empty text")
        if not isinstance(self.start_time, datetime):
            raise TypeError("alarm start_time must be a datetime")
        if self.end_time is not None and self.end_time <= self.start_time:
            raise ValueError("alarm end_time must be after start_time")
        if self.is_open and self.end_time is not None:
            raise ValueError("open alarm occurrence must not have an end_time")
        if not self.is_open and self.end_time is None:
            raise ValueError("closed alarm occurrence must have an end_time")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("alarm duration_seconds cannot be negative")


class _State(Enum):
    UNKNOWN = "unknown"
    NORMAL = "normal"
    ALARM = "alarm"


class PersistentHighAlarmDetector:
    """Detect continuous active intervals and keep only threshold breaches."""

    def __init__(
        self,
        *,
        threshold_seconds: int | float = 300,
        active_value: str | int = "1",
        point_id: str | None = None,
        history_tag: str | None = None,
    ) -> None:
        self.threshold_seconds = _validate_threshold(threshold_seconds)
        self.active_value = _parse_active_value(active_value)
        self.point_id = _optional_metadata(point_id, "point_id")
        self.history_tag = _optional_metadata(history_tag, "history_tag")

    def detect(
        self,
        samples: Iterable[HistorySample],
        point_id: str | None = None,
        history_tag: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        *,
        observation_end: datetime | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[AlarmOccurrence]:
        """Return qualifying occurrences from ordered or unordered samples.

        ``start_time`` identifies the responsibility/context boundary.  A
        sample before it is used only as the initial state and is never
        itself considered a new alarm start.  ``end_time`` is retained as a
        convenient alias for ``observation_end``; the latter is the horizon
        used to measure an alarm that is still active.
        """

        if window_start is not None:
            if start_time is not None and start_time != window_start:
                raise ValueError("start_time and window_start must match")
            start_time = window_start
        if window_end is not None:
            if end_time is not None and end_time != window_end:
                raise ValueError("end_time and window_end must match")
            end_time = window_end
        if end_time is not None:
            if observation_end is not None and observation_end != end_time:
                raise ValueError("end_time and observation_end must match")
            observation_end = end_time

        point_id = _metadata(point_id, self.point_id, "point_id")
        history_tag = _metadata(history_tag, self.history_tag, "history_tag")

        ordered = _normalise_samples(samples)
        if start_time is None:
            start_time = ordered[0].timestamp if ordered else None
        if start_time is not None and not isinstance(start_time, datetime):
            raise TypeError("start_time must be a datetime value")
        if observation_end is not None:
            if not isinstance(observation_end, datetime):
                raise TypeError("observation_end must be a datetime value")
            if start_time is not None and observation_end <= start_time:
                raise ValueError("observation_end must be after start_time")
            # The observation horizon is authoritative.  This also keeps the
            # detector safe with a test double or adapter that returns a few
            # samples beyond the requested range.
            ordered = [
                sample for sample in ordered if sample.timestamp < observation_end
            ]

        if not ordered or start_time is None:
            return []

        previous = _latest_before(ordered, start_time)
        current_samples = [
            sample
            for sample in ordered
            if sample.timestamp >= start_time
            and (observation_end is None or sample.timestamp < observation_end)
        ]

        if previous is None:
            state = _State.UNKNOWN
            alarm_start: datetime | None = None
        else:
            previous_state = parse_digital_state(previous.value)
            state = (
                _State.ALARM
                if previous_state == self.active_value
                else _State.NORMAL
            )
            alarm_start = previous.timestamp if state is _State.ALARM else None

        occurrences: list[AlarmOccurrence] = []
        for sample in current_samples:
            state_value = parse_digital_state(sample.value)
            is_active = state_value == self.active_value

            if state is _State.UNKNOWN:
                # A first active observation has no proven 0->1 edge.  Keep
                # the state active but leave its start unknown until a 0 is
                # observed and a subsequent active edge can be identified.
                if not is_active:
                    state = _State.NORMAL
                else:
                    state = _State.ALARM
                continue

            if state is _State.NORMAL:
                if is_active:
                    state = _State.ALARM
                    alarm_start = sample.timestamp
                continue

            # state == ALARM
            if is_active:
                continue

            if alarm_start is not None:
                # Distinct Historian sequences may share a timestamp.  A
                # same-timestamp active/recovery pair has zero duration and
                # is simply not a qualifying interval.
                if sample.timestamp > alarm_start:
                    occurrence = _closed_occurrence(
                        point_id,
                        history_tag,
                        alarm_start,
                        sample.timestamp,
                    )
                    if _qualifies(occurrence, self.threshold_seconds):
                        occurrences.append(occurrence)
            state = _State.NORMAL
            alarm_start = None

        if state is _State.ALARM and alarm_start is not None:
            horizon = observation_end or ordered[-1].timestamp
            occurrence = _open_occurrence(
                point_id,
                history_tag,
                alarm_start,
                horizon,
            )
            if _qualifies(occurrence, self.threshold_seconds):
                occurrences.append(occurrence)

        # An interval that began before the responsibility window was used
        # for state initialization only.  Its recovery may be observed here,
        # but it is not a new event for this window.
        return [
            occurrence
            for occurrence in occurrences
            if occurrence.start_time >= start_time
        ]


def detect_alarm_occurrences(
    samples: Iterable[HistorySample],
    point_id: str | None = None,
    history_tag: str | None = None,
    *,
    threshold_seconds: int | float = 300,
    active_value: str | int = "1",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    observation_end: datetime | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[AlarmOccurrence]:
    """Functional wrapper around :class:`PersistentHighAlarmDetector`."""

    return PersistentHighAlarmDetector(
        threshold_seconds=threshold_seconds,
        active_value=active_value,
        point_id=point_id,
        history_tag=history_tag,
    ).detect(
        samples,
        start_time=start_time,
        end_time=end_time,
        observation_end=observation_end,
        window_start=window_start,
        window_end=window_end,
    )


detect_persistent_high_alarms = detect_alarm_occurrences
detect = detect_alarm_occurrences


def _validate_threshold(value: int | float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("threshold_seconds must be a positive number")
    numeric = float(value)
    if not isfinite(numeric) or numeric <= 0:
        raise ValueError("threshold_seconds must be a positive number")
    return numeric


def _parse_active_value(value: str | int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        parsed = value
    elif isinstance(value, str):
        parsed = parse_digital_state(value)
    else:
        raise DigitalStateParseError(
            f"active_value must be an explicit digital state; got {value!r}"
        )
    if parsed not in {0, 1}:
        raise DigitalStateParseError(
            f"active_value must be 0 or 1; got {value!r}"
        )
    return parsed


def _optional_metadata(value: str | None, field_name: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{field_name} must be non-empty text")
    return value


def _metadata(
    value: str | None,
    configured_value: str | None,
    field_name: str,
) -> str:
    resolved = value if value is not None else configured_value
    if not isinstance(resolved, str) or not resolved:
        raise ValueError(
            f"{field_name} must be supplied to the detector or its constructor"
        )
    return resolved


def _normalise_samples(samples: Iterable[HistorySample]) -> list[HistorySample]:
    unique: dict[tuple[datetime, int], HistorySample] = {}
    for sample in samples:
        if not isinstance(sample, HistorySample):
            raise TypeError("detector input must contain HistorySample values")
        if not isinstance(sample.timestamp, datetime):
            raise TypeError("HistorySample.timestamp must be a datetime")
        if isinstance(sample.sequence_no, bool) or not isinstance(sample.sequence_no, int):
            raise TypeError("HistorySample.sequence_no must be an integer")
        unique.setdefault((sample.timestamp, sample.sequence_no), sample)
    return sorted(unique.values(), key=lambda item: (item.timestamp, item.sequence_no))


def _latest_before(
    samples: list[HistorySample],
    start_time: datetime,
) -> HistorySample | None:
    previous = [sample for sample in samples if sample.timestamp < start_time]
    return max(previous, key=lambda item: (item.timestamp, item.sequence_no), default=None)


def _closed_occurrence(
    point_id: str,
    history_tag: str,
    start_time: datetime,
    end_time: datetime,
) -> AlarmOccurrence:
    return AlarmOccurrence(
        point_id=point_id,
        history_tag=history_tag,
        start_time=start_time,
        end_time=end_time,
        duration_seconds=(end_time - start_time).total_seconds(),
        is_open=False,
    )


def _open_occurrence(
    point_id: str,
    history_tag: str,
    start_time: datetime,
    observation_end: datetime,
) -> AlarmOccurrence:
    if observation_end <= start_time:
        duration_seconds = 0.0
    else:
        duration_seconds = (observation_end - start_time).total_seconds()
    return AlarmOccurrence(
        point_id=point_id,
        history_tag=history_tag,
        start_time=start_time,
        end_time=None,
        duration_seconds=duration_seconds,
        is_open=True,
    )


def _qualifies(occurrence: AlarmOccurrence, threshold_seconds: float) -> bool:
    # The production rule says "超过 5 分钟": exactly 300 seconds is not
    # assessable.
    return (
        occurrence.duration_seconds is not None
        and occurrence.duration_seconds > threshold_seconds
    )

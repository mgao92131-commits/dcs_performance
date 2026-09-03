"""Pure state-machine detection for continuous analog limit violations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite

from dcs_performance.data.models import HistorySample
from dcs_performance.rules.analog_limit_exceedance.config import (
    LimitSideConfig,
    PointConfig,
)


class LimitEventType(str, Enum):
    """Stable event type values emitted by this rule."""

    LOW = "low_limit"
    HIGH = "high_limit"

    # Longer spellings are convenient for callers that prefer explicit names.
    LOW_LIMIT = "low_limit"
    HIGH_LIMIT = "high_limit"


class AnalogValueParseError(ValueError):
    """Raised when a Historian value is not a finite analog number."""


def parse_analog_value(value: str) -> float:
    """Parse one Historian analog value as a finite float.

    Historian values are transported as text.  Invalid text and non-finite
    values are data errors and therefore propagate to the caller; they are
    never converted into a normal value or silently skipped.
    """

    if not isinstance(value, str):
        raise AnalogValueParseError(
            f"analog value must be text containing a finite number; got {value!r}"
        )
    text = value.strip()
    if not text:
        raise AnalogValueParseError("analog value must not be empty")
    try:
        numeric = float(text)
    except (TypeError, ValueError) as exc:
        raise AnalogValueParseError(
            f"unknown analog value {value!r}; expected a finite number"
        ) from exc
    if not isfinite(numeric):
        raise AnalogValueParseError(
            f"unknown analog value {value!r}; expected a finite number"
        )
    return numeric


@dataclass(frozen=True)
class LimitOccurrence:
    """One qualified same-direction analog limit interval."""

    point_id: str
    history_tag: str
    event_type: str
    start_time: datetime
    end_time: datetime | None
    duration_seconds: float
    limit: float
    extreme_value: float
    extreme_time: datetime
    is_open: bool

    def __post_init__(self) -> None:
        if not isinstance(self.point_id, str) or not self.point_id:
            raise ValueError("point_id must be non-empty text")
        if not isinstance(self.history_tag, str) or not self.history_tag:
            raise ValueError("history_tag must be non-empty text")
        if self.event_type not in {
            LimitEventType.LOW.value,
            LimitEventType.HIGH.value,
        }:
            raise ValueError(f"unsupported analog limit event_type: {self.event_type!r}")
        if not isinstance(self.start_time, datetime):
            raise TypeError("limit start_time must be a datetime")
        if not isinstance(self.extreme_time, datetime):
            raise TypeError("limit extreme_time must be a datetime")
        if self.end_time is not None and self.end_time <= self.start_time:
            raise ValueError("limit end_time must be after start_time")
        if self.is_open and self.end_time is not None:
            raise ValueError("open limit occurrence must not have an end_time")
        if not self.is_open and self.end_time is None:
            raise ValueError("closed limit occurrence must have an end_time")
        if isinstance(self.duration_seconds, bool) or not isinstance(
            self.duration_seconds,
            (int, float),
        ):
            raise ValueError("limit duration_seconds must be a finite number")
        if not isfinite(float(self.duration_seconds)) or self.duration_seconds < 0:
            raise ValueError("limit duration_seconds must be a finite non-negative number")
        if isinstance(self.limit, bool) or not isinstance(self.limit, (int, float)):
            raise ValueError("limit must be a finite number")
        if not isfinite(float(self.limit)):
            raise ValueError("limit must be a finite number")
        if isinstance(self.extreme_value, bool) or not isinstance(
            self.extreme_value,
            (int, float),
        ):
            raise ValueError("extreme_value must be a finite number")
        if not isfinite(float(self.extreme_value)):
            raise ValueError("extreme_value must be a finite number")


class _State(Enum):
    UNKNOWN = "unknown"
    NORMAL = "normal"
    LOW = "low"
    HIGH = "high"


@dataclass
class _Run:
    """Mutable state for one same-direction run."""

    point_id: str
    history_tag: str
    event_type: LimitEventType
    side: LimitSideConfig
    start_time: datetime
    extreme_value: float
    extreme_time: datetime
    pending_recovery: datetime | None = None

    @classmethod
    def start(
        cls,
        point_id: str,
        history_tag: str,
        event_type: LimitEventType,
        side: LimitSideConfig,
        timestamp: datetime,
        value: float,
    ) -> _Run:
        return cls(
            point_id=point_id,
            history_tag=history_tag,
            event_type=event_type,
            side=side,
            start_time=timestamp,
            extreme_value=value,
            extreme_time=timestamp,
        )

    def add_value(self, timestamp: datetime, value: float) -> None:
        if self.event_type is LimitEventType.HIGH:
            if value > self.extreme_value:
                self.extreme_value = value
                self.extreme_time = timestamp
        elif value < self.extreme_value:
            self.extreme_value = value
            self.extreme_time = timestamp


class AnalogLimitExceedanceDetector:
    """Detect qualified analog limit occurrences without any I/O."""

    def __init__(self, config: PointConfig | None = None) -> None:
        if config is not None:
            _validate_point_config(config)
        self.config = config

    def detect(
        self,
        samples: Iterable[HistorySample],
        config: PointConfig | None = None,
        start_time: datetime | None = None,
        observation_end: datetime | None = None,
        *,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        end_time: datetime | None = None,
        allow_initial_abnormal: bool = False,
    ) -> list[LimitOccurrence]:
        """Return qualified occurrences from ordered or unordered samples.

        ``start_time`` is the responsibility-window boundary.  The latest
        sample before it initializes the state, but a violation already active
        at that point cannot become a new occurrence for this window.  With no
        preceding sample, the first abnormal observation remains UNKNOWN until
        a normal or opposite state establishes a real transition.  A caller
        may explicitly set ``allow_initial_abnormal`` when it has created a
        semantic boundary (for example, a deliberately removed disturbance
        window) and can therefore treat that first observation as a new edge.
        """

        if window_start is not None:
            if start_time is not None and start_time != window_start:
                raise ValueError("start_time and window_start must match")
            start_time = window_start
        if window_end is not None:
            if observation_end is not None and observation_end != window_end:
                raise ValueError("observation_end and window_end must match")
            observation_end = window_end
        if end_time is not None:
            if observation_end is not None and observation_end != end_time:
                raise ValueError("observation_end and end_time must match")
            observation_end = end_time

        resolved_config = config if config is not None else self.config
        if resolved_config is None:
            raise ValueError("a PointConfig is required")
        _validate_point_config(resolved_config)

        ordered = _normalise_samples(samples)
        # Parse every retained sample before applying the observation horizon.
        # A malformed Historian value is a data error even if it would not have
        # contributed to an occurrence.
        parsed = [(sample, parse_analog_value(sample.value)) for sample in ordered]

        if start_time is None:
            start_time = ordered[0].timestamp if ordered else None
        if start_time is not None and not isinstance(start_time, datetime):
            raise TypeError("start_time must be a datetime value")
        if observation_end is not None:
            if not isinstance(observation_end, datetime):
                raise TypeError("observation_end must be a datetime value")
            if start_time is not None and observation_end <= start_time:
                raise ValueError("observation_end must be after start_time")
        _validate_timezones(parsed, start_time, observation_end)

        if not parsed or start_time is None:
            return []

        previous = _latest_before(parsed, start_time)
        current = [
            item
            for item in parsed
            if item[0].timestamp >= start_time
            and (observation_end is None or item[0].timestamp < observation_end)
        ]
        horizon = observation_end or (
            current[-1][0].timestamp if current else ordered[-1].timestamp
        )

        state = _State.UNKNOWN
        run: _Run | None = None
        if previous is not None:
            previous_sample, previous_value = previous
            state, side, event_type = _classify(previous_value, resolved_config)
            if state is not _State.NORMAL:
                # This run is intentionally retained with its real pre-window
                # start.  It can absorb a short recovery, but the final
                # responsibility filter will never emit it for this window.
                run = _Run.start(
                    resolved_config.id,
                    resolved_config.history_tag,
                    event_type,
                    side,
                    previous_sample.timestamp,
                    previous_value,
                )

        occurrences: list[LimitOccurrence] = []
        for sample, value in current:
            new_state, side, event_type = _classify(value, resolved_config)

            if state is _State.UNKNOWN:
                if new_state is _State.NORMAL:
                    state = _State.NORMAL
                elif allow_initial_abnormal and run is None:
                    state = new_state
                    run = _Run.start(
                        resolved_config.id,
                        resolved_config.history_tag,
                        event_type,
                        side,
                        sample.timestamp,
                        value,
                    )
                else:
                    # No proven NORMAL -> violation edge exists yet.
                    state = new_state
                continue

            if state is _State.NORMAL:
                if new_state is _State.NORMAL:
                    continue
                state = new_state
                run = _Run.start(
                    resolved_config.id,
                    resolved_config.history_tag,
                    event_type,
                    side,
                    sample.timestamp,
                    value,
                )
                continue

            # A known abnormal state is active.  ``run`` is None only when the
            # initial state was UNKNOWN and the first observed value was
            # abnormal; that run is deliberately not given a fabricated start.
            if run is None:
                if new_state is _State.NORMAL:
                    state = _State.NORMAL
                elif new_state is not state:
                    state = new_state
                    run = _Run.start(
                        resolved_config.id,
                        resolved_config.history_tag,
                        event_type,
                        side,
                        sample.timestamp,
                        value,
                    )
                continue

            if new_state is state:
                if run.pending_recovery is None:
                    run.add_value(sample.timestamp, value)
                else:
                    gap = (
                        sample.timestamp - run.pending_recovery
                    ).total_seconds()
                    if gap <= run.side.merge_gap_seconds:
                        run.pending_recovery = None
                        run.add_value(sample.timestamp, value)
                    else:
                        _append_closed(occurrences, run, run.pending_recovery)
                        run = _Run.start(
                            resolved_config.id,
                            resolved_config.history_tag,
                            event_type,
                            side,
                            sample.timestamp,
                            value,
                        )
                continue

            if new_state is _State.NORMAL:
                if run.pending_recovery is None:
                    run.pending_recovery = sample.timestamp
                run.add_value(sample.timestamp, value)
                continue

            # HIGH -> LOW and LOW -> HIGH are separate events, regardless of
            # the time between them.  The transition sample closes the old
            # interval and starts the new one at the same observed timestamp.
            _append_closed(
                occurrences,
                run,
                run.pending_recovery or sample.timestamp,
            )
            run = _Run.start(
                resolved_config.id,
                resolved_config.history_tag,
                event_type,
                side,
                sample.timestamp,
                value,
            )
            state = new_state

        if run is not None:
            if run.pending_recovery is not None:
                _append_closed(occurrences, run, run.pending_recovery)
            else:
                _append_open(occurrences, run, horizon)

        # The detector can be used directly, but the Rule is also expected to
        # repeat this ownership check.  A merged occurrence beginning before
        # the window remains owned by the earlier window and is not repeated.
        result = [
            occurrence
            for occurrence in occurrences
            if occurrence.start_time >= start_time
        ]
        if observation_end is not None:
            result = [
                occurrence
                for occurrence in result
                if occurrence.start_time < observation_end
            ]
        return result


def detect_limit_occurrences(
    samples: Iterable[HistorySample],
    config: PointConfig,
    start_time: datetime | None = None,
    observation_end: datetime | None = None,
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    end_time: datetime | None = None,
    allow_initial_abnormal: bool = False,
) -> list[LimitOccurrence]:
    """Functional wrapper around :class:`AnalogLimitExceedanceDetector`."""

    return AnalogLimitExceedanceDetector().detect(
        samples,
        config,
        start_time=start_time,
        observation_end=observation_end,
        window_start=window_start,
        window_end=window_end,
        end_time=end_time,
        allow_initial_abnormal=allow_initial_abnormal,
    )


# Short aliases for callers that use detector-oriented vocabulary.
detect_analog_limit_exceedances = detect_limit_occurrences
detect_analog_limit_exceedance = detect_limit_occurrences
detect = detect_limit_occurrences
Detector = AnalogLimitExceedanceDetector


def _validate_point_config(config: PointConfig) -> None:
    if not isinstance(config, PointConfig):
        raise TypeError("detector config must be a PointConfig")
    if not config.enabled:
        return
    if not config.low.enabled and not config.high.enabled:
        raise ValueError("point must enable at least one limit side")
    if config.low.enabled and config.high.enabled and config.low.limit >= config.high.limit:
        raise ValueError("low.limit must be less than high.limit")


def _normalise_samples(
    samples: Iterable[HistorySample],
) -> list[HistorySample]:
    unique: dict[tuple[datetime, int], HistorySample] = {}
    timezone_aware: bool | None = None
    for sample in samples:
        if not isinstance(sample, HistorySample):
            raise TypeError("detector input must contain HistorySample values")
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
        # Keep the first response for a true duplicate, just as the shared
        # history-context helper does.
        unique.setdefault((sample.timestamp, sample.sequence_no), sample)
    return sorted(unique.values(), key=lambda item: (item.timestamp, item.sequence_no))


def _validate_timezones(
    parsed: list[tuple[HistorySample, float]],
    start_time: datetime | None,
    observation_end: datetime | None,
) -> None:
    values = [sample.timestamp for sample, _ in parsed]
    reference = start_time or (values[0] if values else None)
    if reference is None:
        return
    for other in [observation_end, *values]:
        if other is not None and (other.tzinfo is None) != (reference.tzinfo is None):
            raise ValueError(
                "sample and boundary datetimes must all be timezone-naive or timezone-aware"
            )


def _latest_before(
    parsed: list[tuple[HistorySample, float]],
    start_time: datetime,
) -> tuple[HistorySample, float] | None:
    previous = [item for item in parsed if item[0].timestamp < start_time]
    return max(
        previous,
        key=lambda item: (item[0].timestamp, item[0].sequence_no),
        default=None,
    )


def _classify(
    value: float,
    config: PointConfig,
) -> tuple[_State, LimitSideConfig, LimitEventType]:
    if config.low.enabled and value < config.low.limit:
        return _State.LOW, config.low, LimitEventType.LOW
    if config.high.enabled and value > config.high.limit:
        return _State.HIGH, config.high, LimitEventType.HIGH
    # The side is unused for NORMAL; a stable placeholder keeps the return
    # shape simple and is never used to create an occurrence.
    return _State.NORMAL, config.low, LimitEventType.LOW


def _append_closed(
    occurrences: list[LimitOccurrence],
    run: _Run,
    end_time: datetime,
) -> None:
    if end_time <= run.start_time:
        return
    duration = (end_time - run.start_time).total_seconds()
    if duration <= run.side.min_duration_seconds:
        return
    occurrences.append(
        LimitOccurrence(
            point_id=run.point_id,
            history_tag=run.history_tag,
            event_type=run.event_type.value,
            start_time=run.start_time,
            end_time=end_time,
            duration_seconds=duration,
            limit=run.side.limit,
            extreme_value=run.extreme_value,
            extreme_time=run.extreme_time,
            is_open=False,
        )
    )


def _append_open(
    occurrences: list[LimitOccurrence],
    run: _Run,
    observation_end: datetime,
) -> None:
    if observation_end <= run.start_time:
        return
    duration = (observation_end - run.start_time).total_seconds()
    if duration <= run.side.min_duration_seconds:
        return
    occurrences.append(
        LimitOccurrence(
            point_id=run.point_id,
            history_tag=run.history_tag,
            event_type=run.event_type.value,
            start_time=run.start_time,
            end_time=None,
            duration_seconds=duration,
            limit=run.side.limit,
            extreme_value=run.extreme_value,
            extreme_time=run.extreme_time,
            is_open=True,
        )
    )

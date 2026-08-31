"""Pure state-machine detection for pump switching and flow compliance."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from math import isfinite
import re

from dcs_performance.data.models import HistorySample


LOW_FLOW = "low_flow"
SWITCH_TIMEOUT = "switch_timeout"


class DigitalStateParseError(ValueError):
    """Raised when a Historian value is not an explicit digital state."""


class FlowValueParseError(ValueError):
    """Raised when a Historian flow value is not a finite number."""


class PumpMode(str, Enum):
    """The four observable A/B pump combinations."""

    UNKNOWN = "UNKNOWN"
    NORMAL_A = "NORMAL_A"
    NORMAL_B = "NORMAL_B"
    SWITCHING = "SWITCHING"


def parse_digital_state(value: str | int | float) -> int:
    """Parse a digital value strictly as zero or one.

    The production Historian exposes Enumerated values as
    ``mtr2-pv:<0|1>:<display text>``.  The numeric part is accepted because it
    is an explicit state encoding; arbitrary non-empty text is still rejected.
    """

    if isinstance(value, bool):
        raise DigitalStateParseError("digital state must be 0 or 1, not boolean")
    if not isinstance(value, (str, int, float)):
        raise DigitalStateParseError(
            f"digital state must contain 0 or 1; got {value!r}"
        )
    text = value.strip() if isinstance(value, str) else str(value)
    if not text:
        raise DigitalStateParseError("digital state must not be empty")
    enumerated_match = re.fullmatch(r"mtr2-pv:([+-]?\d+):.*", text, re.IGNORECASE)
    if enumerated_match is not None:
        text = enumerated_match.group(1)
    try:
        numeric = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise DigitalStateParseError(
            f"unknown digital state {value!r}; expected 0 or 1"
        ) from exc
    if not numeric.is_finite() or numeric not in {Decimal(0), Decimal(1)}:
        raise DigitalStateParseError(
            f"unknown digital state {value!r}; expected 0 or 1"
        )
    return int(numeric)


def parse_flow_value(value: str | int | float) -> float:
    """Parse a finite Historian flow value without converting errors to zero."""

    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise FlowValueParseError(f"flow value must be a finite number; got {value!r}")
    text = value.strip() if isinstance(value, str) else str(value)
    if not text:
        raise FlowValueParseError("flow value must not be empty")
    try:
        numeric = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise FlowValueParseError(
            f"invalid flow value {value!r}; expected a finite number"
        ) from exc
    if not numeric.is_finite():
        raise FlowValueParseError(
            f"invalid flow value {value!r}; expected a finite number"
        )
    result = float(numeric)
    if not isfinite(result):
        raise FlowValueParseError(
            f"invalid flow value {value!r}; expected a finite number"
        )
    return result


@dataclass(frozen=True)
class PumpFlowOccurrence:
    """One continuous low-flow or switch-timeout finding."""

    event_type: str
    start_time: datetime
    end_time: datetime
    data: dict[str, object]

    def __post_init__(self) -> None:
        if self.event_type not in {LOW_FLOW, SWITCH_TIMEOUT}:
            raise ValueError(f"unsupported pump-flow event type: {self.event_type!r}")
        if not isinstance(self.start_time, datetime) or not isinstance(
            self.end_time, datetime
        ):
            raise TypeError("pump-flow occurrence times must be datetime values")
        if self.end_time <= self.start_time:
            raise ValueError("pump-flow occurrence end_time must be after start_time")
        if not isinstance(self.data, dict):
            raise TypeError("pump-flow occurrence data must be a dictionary")


# A descriptive synonym for callers that use the term event at the detector
# boundary.  The Rule converts these values into the core AssessmentEvent.
PumpFlowEvent = PumpFlowOccurrence


class PumpFlowDetector:
    """Rebuild one pump group's state and emit independent compliance findings."""

    def __init__(
        self,
        point_id: str,
        pump_a_tag: str,
        pump_b_tag: str,
        flow_tag: str,
        running_value: str | int | float = "1",
        normal_min_flow: int | float = 1,
        switching_min_flow: int | float = 1,
        max_switch_duration_seconds: int | float = 1,
    ) -> None:
        self.point_id = _required_text(point_id, "point_id")
        self.pump_a_tag = _required_text(pump_a_tag, "pump_a_tag")
        self.pump_b_tag = _required_text(pump_b_tag, "pump_b_tag")
        self.flow_tag = _required_text(flow_tag, "flow_tag")
        if self.pump_a_tag == self.pump_b_tag:
            raise ValueError("pump_a_tag and pump_b_tag must be different")
        self.running_value = parse_digital_state(running_value)
        self.normal_min_flow = _positive_number(normal_min_flow, "normal_min_flow")
        self.switching_min_flow = _positive_number(
            switching_min_flow,
            "switching_min_flow",
        )
        self.max_switch_duration_seconds = _positive_number(
            max_switch_duration_seconds,
            "max_switch_duration_seconds",
        )

    def detect(
        self,
        samples_by_tag: Mapping[str, Iterable[HistorySample]] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        *,
        pump_a_samples: Iterable[HistorySample] | None = None,
        pump_b_samples: Iterable[HistorySample] | None = None,
        flow_samples: Iterable[HistorySample] | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        analysis_start: datetime | None = None,
        observation_end: datetime | None = None,
    ) -> list[PumpFlowOccurrence]:
        """Return findings whose logical start belongs to the responsibility window.

        ``samples_by_tag`` normally contains the three configured TAGs and is
        the form used by the production Rule.  The three explicit sample
        keyword arguments are accepted as a small convenience for pure unit
        tests.  ``analysis_start`` is the beginning of the extra historical
        context; when omitted, the earliest supplied sample is used.

        ``observation_end`` is the forward horizon used to close open
        intervals for observation purposes.  It does not change event
        ownership, which is always decided by ``window_start``/``window_end``.
        """

        window_start = _resolve_time_alias(
            window_start,
            start_time,
            "start_time",
            "window_start",
        )
        window_end = _resolve_time_alias(
            window_end,
            end_time,
            "end_time",
            "window_end",
        )
        _validate_range(window_start, window_end)
        if observation_end is None:
            observation_end = window_end
        if not isinstance(observation_end, datetime):
            raise TypeError("observation_end must be a datetime value")
        if observation_end < window_end:
            raise ValueError("observation_end must not be before window_end")

        histories = self._resolve_histories(
            samples_by_tag,
            pump_a_samples=pump_a_samples,
            pump_b_samples=pump_b_samples,
            flow_samples=flow_samples,
        )
        normalised = {
            tag: _normalise_samples(histories[tag])
            for tag in (self.pump_a_tag, self.pump_b_tag, self.flow_tag)
        }
        all_samples = [sample for values in normalised.values() for sample in values]
        if analysis_start is None:
            analysis_start = min(
                (sample.timestamp for sample in all_samples),
                default=window_start,
            )
        if not isinstance(analysis_start, datetime):
            raise TypeError("analysis_start must be a datetime value")
        if analysis_start > window_start:
            raise ValueError("analysis_start must not be after window_start")
        if (analysis_start.tzinfo is None) != (window_start.tzinfo is None):
            raise ValueError(
                "analysis_start and window_start must both be timezone-naive "
                "or timezone-aware"
            )

        parsed = {
            tag: [
                (sample, self._parse_sample(tag, sample))
                for sample in normalised[tag]
            ]
            for tag in normalised
        }
        current_a = _latest_before(parsed[self.pump_a_tag], analysis_start)
        current_b = _latest_before(parsed[self.pump_b_tag], analysis_start)
        current_flow = _latest_before(parsed[self.flow_tag], analysis_start)

        mode = _mode_for_values(
            current_a[1] if current_a is not None else None,
            current_b[1] if current_b is not None else None,
            self.running_value,
        )
        switch_start: datetime | None = None
        switch_from_mode: PumpMode | None = None

        low_active = False
        low_start: datetime | None = None
        low_minimum: float | None = None
        low_modes: list[str] = []
        occurrences: list[PumpFlowOccurrence] = []

        initial_violation = _is_low_flow(
            current_flow[1] if current_flow is not None else None,
            mode,
            self.normal_min_flow,
            self.switching_min_flow,
        )
        if initial_violation:
            low_active = True
            low_start = analysis_start
            low_minimum = current_flow[1]
            _append_unique(low_modes, mode.value)

        timeline = _build_timeline(
            parsed,
            analysis_start=analysis_start,
            observation_end=observation_end,
            tags=(self.pump_a_tag, self.pump_b_tag, self.flow_tag),
        )
        for timestamp, changes in timeline:
            previous_mode = mode
            for tag, value in changes:
                if tag == self.pump_a_tag:
                    current_a = (timestamp, value)
                elif tag == self.pump_b_tag:
                    current_b = (timestamp, value)
                else:
                    current_flow = (timestamp, value)

            mode = _mode_for_values(
                current_a[1] if current_a is not None else None,
                current_b[1] if current_b is not None else None,
                self.running_value,
            )

            if previous_mode is PumpMode.SWITCHING and mode is not PumpMode.SWITCHING:
                if switch_start is not None:
                    occurrences.extend(
                        self._timeout_occurrences(
                            switch_start=switch_start,
                            switch_end=timestamp,
                            from_mode=switch_from_mode,
                            to_mode=mode,
                            is_open=False,
                        )
                    )
                switch_start = None
                switch_from_mode = None
            elif previous_mode is not PumpMode.SWITCHING and mode is PumpMode.SWITCHING:
                # A transition observed after the context boundary proves the
                # beginning of this switch.  A switch already in progress at
                # the first known state intentionally keeps an unknown start.
                if previous_mode in {PumpMode.NORMAL_A, PumpMode.NORMAL_B}:
                    switch_start = timestamp
                    switch_from_mode = previous_mode
                else:
                    switch_start = None
                    switch_from_mode = None

            violating = _is_low_flow(
                current_flow[1] if current_flow is not None else None,
                mode,
                self.normal_min_flow,
                self.switching_min_flow,
            )
            if low_active:
                if violating:
                    if current_flow is not None:
                        low_minimum = min(low_minimum, current_flow[1])
                    _append_unique(low_modes, mode.value)
                else:
                    assert low_start is not None
                    assert low_minimum is not None
                    if timestamp > low_start:
                        occurrences.append(
                            self._low_flow_occurrence(
                                low_start,
                                timestamp,
                                low_minimum,
                                low_modes,
                                is_open=False,
                            )
                        )
                    low_active = False
                    low_start = None
                    low_minimum = None
                    low_modes = []
            elif violating:
                low_active = True
                low_start = timestamp
                low_minimum = current_flow[1]
                low_modes = [mode.value]

        if low_active:
            assert low_start is not None
            assert low_minimum is not None
            if observation_end > low_start:
                occurrences.append(
                    self._low_flow_occurrence(
                        low_start,
                        observation_end,
                        low_minimum,
                        low_modes,
                        is_open=True,
                    )
                )

        if mode is PumpMode.SWITCHING and switch_start is not None:
            occurrences.extend(
                self._timeout_occurrences(
                    switch_start=switch_start,
                    switch_end=observation_end,
                    from_mode=switch_from_mode,
                    to_mode=None,
                    is_open=True,
                )
            )

        # The detector is useful on its own, but the Rule remains the final
        # ownership boundary.  Keeping this filter here also protects direct
        # callers from receiving intervals that began before their window.
        return sorted(
            (
                occurrence
                for occurrence in occurrences
                if window_start <= occurrence.start_time < window_end
            ),
            key=lambda occurrence: (
                occurrence.start_time,
                occurrence.event_type,
            ),
        )

    def _resolve_histories(
        self,
        samples_by_tag: Mapping[str, Iterable[HistorySample]] | None,
        *,
        pump_a_samples: Iterable[HistorySample] | None,
        pump_b_samples: Iterable[HistorySample] | None,
        flow_samples: Iterable[HistorySample] | None,
    ) -> dict[str, Iterable[HistorySample]]:
        if samples_by_tag is not None:
            if not isinstance(samples_by_tag, Mapping):
                raise TypeError("samples_by_tag must be a mapping")
            result = {
                self.pump_a_tag: samples_by_tag.get(self.pump_a_tag, []),
                self.pump_b_tag: samples_by_tag.get(self.pump_b_tag, []),
                self.flow_tag: samples_by_tag.get(self.flow_tag, []),
            }
        else:
            result = {
                self.pump_a_tag: pump_a_samples or [],
                self.pump_b_tag: pump_b_samples or [],
                self.flow_tag: flow_samples or [],
            }
        for tag, samples in result.items():
            if isinstance(samples, (str, bytes)):
                raise TypeError(f"history for {tag!r} must be iterable samples")
        return result

    def _parse_sample(self, tag: str, sample: HistorySample) -> int | float:
        try:
            if tag == self.flow_tag:
                return parse_flow_value(sample.value)
            return parse_digital_state(sample.value)
        except (DigitalStateParseError, FlowValueParseError) as exc:
            raise type(exc)(
                f"{tag} at {sample.timestamp.isoformat()}: {exc}"
            ) from exc

    def _low_flow_occurrence(
        self,
        start_time: datetime,
        end_time: datetime,
        minimum_flow: float,
        modes_seen: list[str],
        *,
        is_open: bool,
    ) -> PumpFlowOccurrence:
        duration_seconds = (end_time - start_time).total_seconds()
        data: dict[str, object] = {
            "point_id": self.point_id,
            "event_type": LOW_FLOW,
            "flow_tag": self.flow_tag,
            "pump_a_tag": self.pump_a_tag,
            "pump_b_tag": self.pump_b_tag,
            "minimum_flow": minimum_flow,
            "normal_min_flow": self.normal_min_flow,
            "switching_min_flow": self.switching_min_flow,
            "modes_seen": list(modes_seen),
            "duration_seconds": duration_seconds,
            "is_open": is_open,
            "event_key": _event_key(self.point_id, LOW_FLOW, start_time),
        }
        if is_open:
            data["flow_end"] = None
        else:
            data["flow_end"] = end_time
        return PumpFlowOccurrence(LOW_FLOW, start_time, end_time, data)

    def _timeout_occurrences(
        self,
        *,
        switch_start: datetime,
        switch_end: datetime,
        from_mode: PumpMode | None,
        to_mode: PumpMode | None,
        is_open: bool,
    ) -> list[PumpFlowOccurrence]:
        duration_seconds = (switch_end - switch_start).total_seconds()
        timeout_start = switch_start + _seconds(self.max_switch_duration_seconds)
        if duration_seconds <= self.max_switch_duration_seconds:
            return []
        data: dict[str, object] = {
            "point_id": self.point_id,
            "event_type": SWITCH_TIMEOUT,
            "pump_a_tag": self.pump_a_tag,
            "pump_b_tag": self.pump_b_tag,
            "flow_tag": self.flow_tag,
            "switch_start": switch_start,
            "timeout_start": timeout_start,
            "switch_end": None if is_open else switch_end,
            "switch_duration_seconds": duration_seconds,
            "max_switch_duration_seconds": self.max_switch_duration_seconds,
            "overtime_seconds": duration_seconds - self.max_switch_duration_seconds,
            "from_mode": from_mode.value if from_mode is not None else None,
            "to_mode": to_mode.value if to_mode is not None else None,
            "is_open": is_open,
            "event_key": _event_key(self.point_id, SWITCH_TIMEOUT, timeout_start),
        }
        return [
            PumpFlowOccurrence(
                SWITCH_TIMEOUT,
                timeout_start,
                switch_end,
                data,
            )
        ]


# Compatibility name for callers that use the complete rule ID as the class
# name while keeping the shorter detector name convenient in the Rule.
PumpFlowComplianceDetector = PumpFlowDetector
Detector = PumpFlowDetector


def detect_pump_flow_compliance(
    detector: PumpFlowDetector,
    samples_by_tag: Mapping[str, Iterable[HistorySample]],
    start_time: datetime,
    end_time: datetime,
    **kwargs: object,
) -> list[PumpFlowOccurrence]:
    """Functional wrapper for callers that prefer a detector-free call site."""

    if not isinstance(detector, PumpFlowDetector):
        raise TypeError("detector must be a PumpFlowDetector")
    return detector.detect(
        samples_by_tag,
        start_time,
        end_time,
        **kwargs,
    )


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value


def _positive_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a positive finite number")
    numeric = float(value)
    if not isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{field_name} must be a positive finite number")
    return numeric


def _seconds(value: float) -> timedelta:
    # timedelta accepts int/float seconds; keeping this tiny helper makes the
    # strict boundary visible at the call site and avoids rounding durations.
    return timedelta(seconds=value)


def _resolve_time_alias(
    primary: datetime | None,
    alias: datetime | None,
    alias_name: str,
    primary_name: str,
) -> datetime:
    if primary is not None and alias is not None and primary != alias:
        raise ValueError(f"{alias_name} and {primary_name} must match")
    resolved = primary if primary is not None else alias
    if not isinstance(resolved, datetime):
        raise TypeError(f"{primary_name} must be a datetime value")
    return resolved


def _validate_range(start_time: datetime, end_time: datetime) -> None:
    if end_time <= start_time:
        raise ValueError("window end_time must be after start_time")
    if (start_time.tzinfo is None) != (end_time.tzinfo is None):
        raise ValueError(
            "window start_time and end_time must both be timezone-naive "
            "or timezone-aware"
        )


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
    samples: list[tuple[HistorySample, int | float]],
    start_time: datetime,
) -> tuple[datetime, int | float] | None:
    previous = [item for item in samples if item[0].timestamp < start_time]
    if not previous:
        return None
    sample, value = max(previous, key=lambda item: (item[0].timestamp, item[0].sequence_no))
    return sample.timestamp, value


def _mode_for_values(
    a_value: int | None,
    b_value: int | None,
    running_value: int,
) -> PumpMode:
    if a_value is None or b_value is None:
        return PumpMode.UNKNOWN
    a_running = a_value == running_value
    b_running = b_value == running_value
    if a_running and not b_running:
        return PumpMode.NORMAL_A
    if b_running and not a_running:
        return PumpMode.NORMAL_B
    return PumpMode.SWITCHING


def _required_flow(
    mode: PumpMode,
    normal_min_flow: float,
    switching_min_flow: float,
) -> float | None:
    if mode in {PumpMode.NORMAL_A, PumpMode.NORMAL_B}:
        return normal_min_flow
    if mode is PumpMode.SWITCHING:
        return switching_min_flow
    return None


def _is_low_flow(
    flow: int | float | None,
    mode: PumpMode,
    normal_min_flow: float,
    switching_min_flow: float,
) -> bool:
    required = _required_flow(mode, normal_min_flow, switching_min_flow)
    return flow is not None and required is not None and flow < required


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _build_timeline(
    parsed: Mapping[str, list[tuple[HistorySample, int | float]]],
    *,
    analysis_start: datetime,
    observation_end: datetime,
    tags: tuple[str, ...],
) -> list[tuple[datetime, list[tuple[str, int | float]]]]:
    grouped: dict[datetime, list[tuple[str, HistorySample, int | float]]] = {}
    for tag in tags:
        for sample, value in parsed[tag]:
            if analysis_start <= sample.timestamp < observation_end:
                grouped.setdefault(sample.timestamp, []).append((tag, sample, value))

    timeline: list[tuple[datetime, list[tuple[str, int | float]]]] = []
    for timestamp in sorted(grouped):
        changes: list[tuple[str, int | float]] = []
        for tag in tags:
            tag_changes = [
                (sample, value)
                for changed_tag, sample, value in grouped[timestamp]
                if changed_tag == tag
            ]
            # Apply same-TAG records in sequence order, retaining only the
            # resulting value for the timestamp.  State migration happens
            # once, after all A/B changes at this timestamp are applied.
            if tag_changes:
                tag_changes.sort(key=lambda item: item[0].sequence_no)
                changes.append((tag, tag_changes[-1][1]))
        timeline.append((timestamp, changes))
    return timeline


def _event_key(point_id: str, event_type: str, start_time: datetime) -> str:
    return f"pump_flow_compliance:{point_id}:{event_type}:{start_time.isoformat()}"

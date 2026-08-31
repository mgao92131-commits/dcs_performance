"""Pure event-interval detection for analog trend metrics.

The detector turns metric points into confirmed intervals.  It deliberately
does not construct ``AssessmentEvent`` values: the rule owns point metadata,
responsibility-window clipping, and event messages.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite

from dcs_performance.rules.analog_trend_stability.config import (
    DriftConfig,
    DriftWindowConfig,
    StabilityConfig,
)
from dcs_performance.rules.analog_trend_stability.trend import DriftPoint, TrendPoint


@dataclass(frozen=True)
class StabilityOccurrence:
    """One confirmed deviation interval within one numeric segment."""

    start_time: datetime
    end_time: datetime
    severity: str
    max_abs_deviation: float
    mean_abs_deviation: float
    segment_id: int


@dataclass(frozen=True)
class DriftEvidence:
    """Peak signed change contributed by one drift window."""

    window_id: str
    window_seconds: float
    peak_change: float


@dataclass(frozen=True)
class DriftOccurrence:
    """One merged drift interval, potentially supported by many windows."""

    start_time: datetime
    end_time: datetime
    direction: str
    severity: str
    evidence: tuple[DriftEvidence, ...]
    segment_id: int


def detect_stability_events(
    trend_points: Iterable[TrendPoint],
    config: StabilityConfig,
    *,
    observation_end: datetime | None = None,
) -> list[StabilityOccurrence]:
    """Detect sustained ``abs(deviation) > warning`` intervals.

    A short normal recovery is held as a merge candidate.  If another warning
    point arrives within ``merge_gap_seconds``, the candidate remains part of
    the same interval; otherwise the interval ends at the first recovery
    point.  Duration is measured in real timestamp seconds, never sample
    counts.
    """

    if not isinstance(config, StabilityConfig):
        raise TypeError("stability config must be a StabilityConfig")
    if not config.enabled:
        return []

    points = _normalise_trend_points(trend_points)
    if not points:
        return []
    points, horizon = _limit_to_observation_end(points, observation_end)
    if not points:
        return []

    occurrences: list[StabilityOccurrence] = []
    run: _StabilityRun | None = None
    for point in points:
        magnitude = abs(point.deviation)
        is_warning = magnitude > config.warning_deviation
        is_high = magnitude > config.high_deviation

        if is_warning:
            if run is None:
                run = _StabilityRun.start(point, is_high)
            elif run.pending_recovery is not None:
                recovery_gap = (
                    point.timestamp - run.pending_recovery
                ).total_seconds()
                if recovery_gap <= config.merge_gap_seconds:
                    run.resume(point, is_high)
                else:
                    _append_qualified_stability(occurrences, run, config)
                    run = _StabilityRun.start(point, is_high)
            else:
                run.add_abnormal(point, is_high)
            continue

        if run is not None:
            run.add_recovery(point)

    if run is not None:
        run.end_time = run.pending_recovery or horizon or points[-1].timestamp
        _append_qualified_stability(occurrences, run, config)

    return occurrences


def detect_drift_events(
    drift_points: Iterable[DriftPoint],
    config: DriftConfig,
    *,
    observation_end: datetime | None = None,
) -> list[DriftOccurrence]:
    """Detect each window independently, then merge same-direction evidence."""

    if not isinstance(config, DriftConfig):
        raise TypeError("drift config must be a DriftConfig")
    if not config.enabled:
        return []

    points = _normalise_drift_points(drift_points)
    if not points:
        return []
    points, _ = _limit_to_observation_end(points, observation_end)
    if not points:
        return []

    per_window: list[DriftOccurrence] = []
    for window in config.windows:
        window_points = [point for point in points if point.window_id == window.id]
        per_window.extend(
            _detect_one_drift_window(
                window_points,
                window,
                merge_gap_seconds=config.merge_gap_seconds,
                observation_end=observation_end,
            )
        )
    return merge_drift_occurrences(
        per_window,
        merge_gap_seconds=config.merge_gap_seconds,
        window_order=tuple(window.id for window in config.windows),
    )


def merge_drift_occurrences(
    occurrences: Iterable[DriftOccurrence],
    *,
    merge_gap_seconds: int | float = 0,
    window_order: Sequence[str] = (),
) -> list[DriftOccurrence]:
    """Merge overlapping/nearby same-direction occurrences and their evidence."""

    gap = _nonnegative_number(merge_gap_seconds, "merge_gap_seconds")
    ordered = sorted(
        occurrences,
        key=lambda item: (item.start_time, item.direction, item.segment_id),
    )
    if not ordered:
        return []

    merged: list[DriftOccurrence] = []
    current = ordered[0]
    for occurrence in ordered[1:]:
        same_direction = occurrence.direction == current.direction
        same_segment = occurrence.segment_id == current.segment_id
        close_enough = occurrence.start_time <= current.end_time + timedelta(seconds=gap)
        if same_direction and same_segment and close_enough:
            current = _merge_drift_pair(current, occurrence, window_order)
        else:
            merged.append(_sort_evidence(current, window_order))
            current = occurrence
    merged.append(_sort_evidence(current, window_order))
    return merged


class AnalogTrendStabilityDetector:
    """Small object facade over the pure detector functions."""

    def detect_stability(
        self,
        trend_points: Iterable[TrendPoint],
        config: StabilityConfig,
        *,
        observation_end: datetime | None = None,
    ) -> list[StabilityOccurrence]:
        return detect_stability_events(
            trend_points,
            config,
            observation_end=observation_end,
        )

    def detect_drift(
        self,
        drift_points: Iterable[DriftPoint],
        config: DriftConfig,
        *,
        observation_end: datetime | None = None,
    ) -> list[DriftOccurrence]:
        return detect_drift_events(
            drift_points,
            config,
            observation_end=observation_end,
        )


# Concise aliases for callers using detector-oriented vocabulary.
detect_stability = detect_stability_events
detect_drift = detect_drift_events
Detector = AnalogTrendStabilityDetector


@dataclass
class _StabilityRun:
    start_time: datetime
    end_time: datetime | None
    severity: str
    values: list[tuple[datetime, float]]
    segment_id: int
    pending_recovery: datetime | None = None

    @classmethod
    def start(cls, point: TrendPoint, is_high: bool) -> _StabilityRun:
        return cls(
            start_time=point.timestamp,
            end_time=None,
            severity="high" if is_high else "warning",
            values=[(point.timestamp, abs(point.deviation))],
            segment_id=point.segment_id,
        )

    def add_abnormal(self, point: TrendPoint, is_high: bool) -> None:
        self.values.append((point.timestamp, abs(point.deviation)))
        if is_high:
            self.severity = "high"

    def add_recovery(self, point: TrendPoint) -> None:
        if self.pending_recovery is None:
            self.pending_recovery = point.timestamp
        self.values.append((point.timestamp, abs(point.deviation)))

    def resume(self, point: TrendPoint, is_high: bool) -> None:
        self.pending_recovery = None
        self.add_abnormal(point, is_high)


def _append_qualified_stability(
    occurrences: list[StabilityOccurrence],
    run: _StabilityRun,
    config: StabilityConfig,
) -> None:
    end_time = run.end_time or run.pending_recovery
    if end_time is None or end_time <= run.start_time:
        return
    duration = (end_time - run.start_time).total_seconds()
    if duration < config.min_duration_seconds:
        return
    values = [value for timestamp, value in run.values if timestamp < end_time]
    if not values:
        return
    occurrences.append(
        StabilityOccurrence(
            start_time=run.start_time,
            end_time=end_time,
            severity=run.severity,
            max_abs_deviation=max(values),
            mean_abs_deviation=sum(values) / len(values),
            segment_id=run.segment_id,
        )
    )


def _detect_one_drift_window(
    points: Sequence[DriftPoint],
    window: DriftWindowConfig,
    *,
    merge_gap_seconds: float,
    observation_end: datetime | None,
) -> list[DriftOccurrence]:
    if not points:
        return []

    occurrences: list[DriftOccurrence] = []
    run: _DriftRun | None = None
    for point in points:
        magnitude = abs(point.change)
        direction = _direction(point.change)
        is_warning = magnitude > window.warning_change
        is_high = magnitude > window.high_change

        if is_warning:
            if run is None:
                run = _DriftRun.start(point, direction, is_high)
            elif run.pending_recovery is not None:
                recovery_gap = (
                    point.timestamp - run.pending_recovery
                ).total_seconds()
                if direction == run.direction and recovery_gap <= merge_gap_seconds:
                    run.resume(point, is_high)
                else:
                    _append_qualified_drift(occurrences, run, window)
                    run = _DriftRun.start(point, direction, is_high)
            elif direction != run.direction:
                run.end_time = point.timestamp
                _append_qualified_drift(occurrences, run, window)
                run = _DriftRun.start(point, direction, is_high)
            else:
                run.add_abnormal(point, is_high)
            continue

        if run is not None:
            run.add_recovery(point)

    if run is not None:
        run.end_time = run.pending_recovery or observation_end or points[-1].timestamp
        _append_qualified_drift(occurrences, run, window)
    return occurrences


@dataclass
class _DriftRun:
    start_time: datetime
    end_time: datetime | None
    direction: str
    severity: str
    peak_change: float
    segment_id: int
    pending_recovery: datetime | None = None

    @classmethod
    def start(
        cls,
        point: DriftPoint,
        direction: str,
        is_high: bool,
    ) -> _DriftRun:
        return cls(
            start_time=point.timestamp,
            end_time=None,
            direction=direction,
            severity="high" if is_high else "warning",
            peak_change=point.change,
            segment_id=point.segment_id,
        )

    def add_abnormal(self, point: DriftPoint, is_high: bool) -> None:
        if self.direction == "up":
            self.peak_change = max(self.peak_change, point.change)
        else:
            self.peak_change = min(self.peak_change, point.change)
        if is_high:
            self.severity = "high"

    def add_recovery(self, point: DriftPoint) -> None:
        if self.pending_recovery is None:
            self.pending_recovery = point.timestamp

    def resume(self, point: DriftPoint, is_high: bool) -> None:
        self.pending_recovery = None
        self.add_abnormal(point, is_high)


def _append_qualified_drift(
    occurrences: list[DriftOccurrence],
    run: _DriftRun,
    window: DriftWindowConfig,
) -> None:
    end_time = run.end_time or run.pending_recovery
    if end_time is None or end_time <= run.start_time:
        return
    duration = (end_time - run.start_time).total_seconds()
    if duration < window.min_duration_seconds:
        return
    occurrences.append(
        DriftOccurrence(
            start_time=run.start_time,
            end_time=end_time,
            direction=run.direction,
            severity=run.severity,
            evidence=(
                DriftEvidence(
                    window_id=window.id,
                    window_seconds=window.window_seconds,
                    peak_change=run.peak_change,
                ),
            ),
            segment_id=run.segment_id,
        )
    )


def _merge_drift_pair(
    left: DriftOccurrence,
    right: DriftOccurrence,
    window_order: Sequence[str],
) -> DriftOccurrence:
    by_window: dict[str, DriftEvidence] = {
        evidence.window_id: evidence for evidence in left.evidence
    }
    for evidence in right.evidence:
        existing = by_window.get(evidence.window_id)
        if existing is None:
            by_window[evidence.window_id] = evidence
        elif left.direction == "up":
            by_window[evidence.window_id] = DriftEvidence(
                window_id=existing.window_id,
                window_seconds=existing.window_seconds,
                peak_change=max(existing.peak_change, evidence.peak_change),
            )
        else:
            by_window[evidence.window_id] = DriftEvidence(
                window_id=existing.window_id,
                window_seconds=existing.window_seconds,
                peak_change=min(existing.peak_change, evidence.peak_change),
            )

    severity = "high" if "high" in {left.severity, right.severity} else "warning"
    return DriftOccurrence(
        start_time=min(left.start_time, right.start_time),
        end_time=max(left.end_time, right.end_time),
        direction=left.direction,
        severity=severity,
        evidence=tuple(by_window.values()),
        segment_id=left.segment_id,
    )


def _sort_evidence(
    occurrence: DriftOccurrence,
    window_order: Sequence[str],
) -> DriftOccurrence:
    order = {window_id: index for index, window_id in enumerate(window_order)}
    evidence = tuple(
        sorted(
            occurrence.evidence,
            key=lambda item: (order.get(item.window_id, len(order)), item.window_id),
        )
    )
    if evidence == occurrence.evidence:
        return occurrence
    return DriftOccurrence(
        start_time=occurrence.start_time,
        end_time=occurrence.end_time,
        direction=occurrence.direction,
        severity=occurrence.severity,
        evidence=evidence,
        segment_id=occurrence.segment_id,
    )


def _normalise_trend_points(
    points: Iterable[TrendPoint],
) -> list[TrendPoint]:
    ordered = list(points)
    previous: datetime | None = None
    segment_id: int | None = None
    for point in ordered:
        if not isinstance(point, TrendPoint):
            raise TypeError("stability detector input must contain TrendPoint values")
        if not isinstance(point.timestamp, datetime):
            raise TypeError("TrendPoint.timestamp must be a datetime")
        if not all(isfinite(value) for value in (point.pv, point.trend, point.deviation)):
            raise ValueError("TrendPoint values must be finite")
        if previous is not None and point.timestamp < previous:
            raise ValueError("TrendPoint values must be ordered by timestamp")
        if segment_id is None:
            segment_id = point.segment_id
        elif point.segment_id != segment_id:
            raise ValueError("detector input must contain one numeric segment")
        previous = point.timestamp
    return ordered


def _normalise_drift_points(
    points: Iterable[DriftPoint],
) -> list[DriftPoint]:
    ordered = sorted(points, key=lambda item: (item.timestamp, item.window_id))
    segment_id: int | None = None
    for point in ordered:
        if not isinstance(point, DriftPoint):
            raise TypeError("drift detector input must contain DriftPoint values")
        if not isinstance(point.timestamp, datetime):
            raise TypeError("DriftPoint.timestamp must be a datetime")
        if not isfinite(point.change):
            raise ValueError("DriftPoint.change must be finite")
        if segment_id is None:
            segment_id = point.segment_id
        elif point.segment_id != segment_id:
            raise ValueError("detector input must contain one numeric segment")
    return ordered


def _limit_to_observation_end(
    points: list[TrendPoint] | list[DriftPoint],
    observation_end: datetime | None,
) -> tuple[list[TrendPoint] | list[DriftPoint], datetime | None]:
    if observation_end is None:
        return points, points[-1].timestamp if points else None
    if not isinstance(observation_end, datetime):
        raise TypeError("observation_end must be a datetime value")
    limited = [point for point in points if point.timestamp <= observation_end]
    return limited, observation_end


def _direction(change: float) -> str:
    return "up" if change > 0 else "down"


def _nonnegative_number(value: int | float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a non-negative finite number")
    numeric = float(value)
    if not isfinite(numeric) or numeric < 0:
        raise ValueError(f"{field_name} must be a non-negative finite number")
    return numeric

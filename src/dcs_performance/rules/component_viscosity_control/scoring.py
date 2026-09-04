"""Pure repeated-penalty calculations for continuous viscosity events."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from math import floor, isfinite
from typing import Any

from .config import RepeatPenaltyConfig


def calculate_penalty_units(
    duration_seconds: int | float,
    min_duration_seconds: int | float,
    repeat_penalty: RepeatPenaltyConfig | Mapping[str, Any] | None,
) -> int:
    """Return the total penalty units earned by one continuous event.

    The first unit is earned once the event reaches ``min_duration_seconds``.
    When repetition is enabled, each complete ``interval_seconds`` after the
    initial threshold adds one unit.  ``max_units`` includes the initial unit.
    """

    duration = _nonnegative_number(duration_seconds, "duration_seconds")
    minimum = _nonnegative_number(min_duration_seconds, "min_duration_seconds")
    policy = _coerce_policy(repeat_penalty)
    if duration < minimum:
        return 0

    if not policy.enabled:
        units = 1
    else:
        # _coerce_policy guarantees an interval for an enabled policy.
        assert policy.interval_seconds is not None
        units = 1 + floor(
            (duration - minimum) / policy.interval_seconds
        )
    if policy.max_units is not None:
        units = min(units, policy.max_units)
    return int(units)


def calculate_penalty_checkpoints(
    event_start: datetime,
    duration_seconds: int | float,
    min_duration_seconds: int | float,
    repeat_penalty: RepeatPenaltyConfig | Mapping[str, Any] | None,
) -> list[datetime]:
    """Return timestamped penalty checkpoints for one event.

    A checkpoint is emitted at the first threshold and at every subsequent
    repetition boundary.  The event end is included when it lands exactly on
    one of those boundaries.  The function does not inspect process data or
    split an event; it only uses the supplied duration and policy.
    """

    if not isinstance(event_start, datetime):
        raise TypeError("event_start must be a datetime")
    duration = _nonnegative_number(duration_seconds, "duration_seconds")
    minimum = _nonnegative_number(min_duration_seconds, "min_duration_seconds")
    policy = _coerce_policy(repeat_penalty)
    units = calculate_penalty_units(duration, minimum, policy)
    if units <= 0:
        return []

    if policy.enabled:
        assert policy.interval_seconds is not None
        offsets = (
            minimum + index * policy.interval_seconds
            for index in range(units)
        )
    else:
        offsets = (minimum,)
    return [event_start + timedelta(seconds=offset) for offset in offsets]


def _coerce_policy(
    value: RepeatPenaltyConfig | Mapping[str, Any] | None,
) -> RepeatPenaltyConfig:
    if value is None:
        return RepeatPenaltyConfig()
    if isinstance(value, RepeatPenaltyConfig):
        _validate_policy(value)
        return value
    if not isinstance(value, Mapping):
        raise TypeError("repeat_penalty must be a RepeatPenaltyConfig or object")

    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("repeat_penalty.enabled must be a boolean")
    interval_raw = value.get("interval_seconds")
    interval = (
        None
        if interval_raw is None
        else _positive_int(interval_raw, "repeat_penalty.interval_seconds")
    )
    max_units_raw = value.get("max_units")
    max_units = (
        None
        if max_units_raw is None
        else _positive_int(max_units_raw, "repeat_penalty.max_units")
    )
    policy = RepeatPenaltyConfig(
        enabled=enabled,
        interval_seconds=interval,
        max_units=max_units,
    )
    _validate_policy(policy)
    return policy


def _validate_policy(policy: RepeatPenaltyConfig) -> None:
    if not isinstance(policy.enabled, bool):
        raise ValueError("repeat_penalty.enabled must be a boolean")
    if policy.interval_seconds is not None:
        _positive_int(policy.interval_seconds, "repeat_penalty.interval_seconds")
    if policy.enabled and policy.interval_seconds is None:
        raise ValueError(
            "repeat_penalty.interval_seconds is required when enabled"
        )
    if policy.max_units is not None:
        _positive_int(policy.max_units, "repeat_penalty.max_units")


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    return value


def _nonnegative_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite non-negative number")
    numeric = float(value)
    if not isfinite(numeric) or numeric < 0:
        raise ValueError(f"{field_name} must be a finite non-negative number")
    return numeric


__all__ = [
    "calculate_penalty_checkpoints",
    "calculate_penalty_units",
]

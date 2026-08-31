"""Configuration for the production three-team, 12-hour rotation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


EXPECTED_CYCLIC_TEAM_IDS = frozenset({"A", "B", "C"})


class CyclicScheduleConfigError(ValueError):
    """Raised when a cyclic 12-hour schedule is not valid."""


@dataclass(frozen=True)
class CyclicShiftScheduleConfig:
    """Validated configuration for the production cyclic shift calendar.

    ``rotation[0]`` is the team assigned to ``reference_start``.  The
    rotation is applied to every 12-hour slot in both directions from that
    timestamp.  Team IDs are stable internal identifiers; ``team_names`` is
    only display metadata.
    """

    reference_start: datetime
    shift_hours: int
    rotation: tuple[str, ...]
    team_names: dict[str, str]

    def __post_init__(self) -> None:
        _validate_values(
            reference_start=self.reference_start,
            shift_hours=self.shift_hours,
            rotation=self.rotation,
            team_names=self.team_names,
        )
        object.__setattr__(self, "rotation", tuple(self.rotation))
        object.__setattr__(self, "team_names", dict(self.team_names))

    @property
    def shift_duration(self) -> timedelta:
        """The configured duration as a ``timedelta``."""

        return timedelta(hours=self.shift_hours)

    def display_name(self, team_id: str) -> str:
        """Return the configured display name for one stable team ID."""

        try:
            return self.team_names[team_id]
        except KeyError as exc:
            raise ValueError(f"unknown cyclic team id: {team_id!r}") from exc


def load_cyclic_schedule_config(path: str | Path) -> CyclicShiftScheduleConfig:
    """Load and validate a production cyclic schedule JSON file."""

    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            raw_config = json.load(
                handle,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
    except OSError as exc:
        raise CyclicScheduleConfigError(
            f"unable to read cyclic schedule config {config_path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise CyclicScheduleConfigError(
            f"invalid JSON in cyclic schedule config {config_path}: {exc.msg}"
        ) from exc
    except CyclicScheduleConfigError:
        raise

    if not isinstance(raw_config, dict):
        raise CyclicScheduleConfigError(
            "cyclic schedule config root must be a JSON object"
        )
    _validate_keys(
        raw_config,
        frozenset({"reference_start", "shift_hours", "rotation", "team_names"}),
        "cyclic schedule config",
    )

    reference_value = raw_config["reference_start"]
    if not isinstance(reference_value, str):
        raise CyclicScheduleConfigError(
            "reference_start must be an ISO datetime string"
        )
    try:
        reference_start = datetime.fromisoformat(reference_value)
    except ValueError as exc:
        raise CyclicScheduleConfigError(
            f"reference_start must be an ISO datetime string; got {reference_value!r}"
        ) from exc

    rotation_value = raw_config["rotation"]
    if not isinstance(rotation_value, list):
        raise CyclicScheduleConfigError("rotation must be a JSON array")

    team_names_value = raw_config["team_names"]
    if not isinstance(team_names_value, dict):
        raise CyclicScheduleConfigError("team_names must be a JSON object")

    try:
        return CyclicShiftScheduleConfig(
            reference_start=reference_start,
            shift_hours=raw_config["shift_hours"],
            rotation=tuple(rotation_value),
            team_names=team_names_value,
        )
    except CyclicScheduleConfigError:
        raise
    except (TypeError, ValueError) as exc:
        raise CyclicScheduleConfigError(str(exc)) from exc


def _validate_values(
    *,
    reference_start: datetime,
    shift_hours: int,
    rotation: tuple[str, ...],
    team_names: Mapping[str, str],
) -> None:
    if not isinstance(reference_start, datetime):
        raise CyclicScheduleConfigError("reference_start must be a datetime")
    if reference_start.tzinfo is not None:
        raise CyclicScheduleConfigError(
            "reference_start must be timezone-naive local time"
        )
    if isinstance(shift_hours, bool) or not isinstance(shift_hours, int):
        raise CyclicScheduleConfigError("shift_hours must be an integer")
    if shift_hours != 12:
        raise CyclicScheduleConfigError(
            f"shift_hours must be 12 for the cyclic production schedule; got {shift_hours}"
        )

    if not isinstance(rotation, (list, tuple)):
        raise CyclicScheduleConfigError("rotation must be a list or tuple")
    if any(not isinstance(item, str) for item in rotation):
        raise CyclicScheduleConfigError("rotation must contain only strings")
    rotation_ids = tuple(rotation)
    if set(rotation_ids) != EXPECTED_CYCLIC_TEAM_IDS or len(rotation_ids) != 3:
        raise CyclicScheduleConfigError(
            "rotation must contain A, B, and C exactly once"
        )

    if not isinstance(team_names, Mapping):
        raise CyclicScheduleConfigError("team_names must be a mapping")
    if set(team_names) != EXPECTED_CYCLIC_TEAM_IDS:
        raise CyclicScheduleConfigError(
            "team_names must contain A, B, and C exactly"
        )
    for team_id, name in team_names.items():
        if not isinstance(team_id, str) or not isinstance(name, str) or not name:
            raise CyclicScheduleConfigError(
                "team_names keys and values must be non-empty strings"
            )


def _validate_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    field_name: str,
) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected {', '.join(unexpected)}")
        raise CyclicScheduleConfigError(
            f"{field_name} has invalid fields ({'; '.join(details)})"
        )


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CyclicScheduleConfigError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


# A descriptive alias for callers that prefer the production schedule name.
load_performance_schedule_config = load_cyclic_schedule_config

"""Configuration model and loader for the three-team/two-shift schedule."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any


VALID_SHIFT_TYPES = frozenset({"day", "night", "off"})
EXPECTED_TEAM_IDS = frozenset({"A", "B", "C"})
_CONFIG_KEYS = frozenset({"reference_date", "day_shift", "night_shift", "teams"})
_SHIFT_KEYS = frozenset({"start", "end"})
_STANDARD_DAY_START = time(8, 0)
_STANDARD_DAY_END = time(20, 0)
_STANDARD_NIGHT_START = time(20, 0)
_STANDARD_NIGHT_END = time(8, 0)


class ScheduleConfigError(ValueError):
    """Raised when a schedule JSON file cannot describe a valid schedule."""


@dataclass(frozen=True)
class ShiftScheduleConfig:
    """Validated configuration for a repeating three-team schedule.

    The item at index zero in every ``team_patterns`` tuple belongs to
    ``reference_date``.  The calendar applies the same tuples indefinitely in
    both directions from that date.
    """

    reference_date: date
    day_start: time
    day_end: time
    night_start: time
    night_end: time
    team_patterns: dict[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        _validate_schedule_values(
            reference_date=self.reference_date,
            day_start=self.day_start,
            day_end=self.day_end,
            night_start=self.night_start,
            night_end=self.night_end,
            team_patterns=self.team_patterns,
        )

        # Keep the public model in the promised tuple/dict shape even when a
        # caller constructs it directly instead of using the JSON loader.
        normalized_patterns = {
            team_id: tuple(pattern)
            for team_id, pattern in self.team_patterns.items()
        }
        object.__setattr__(self, "team_patterns", normalized_patterns)

    @property
    def cycle_length(self) -> int:
        """Number of positions in the configured rotation cycle."""

        return len(self.team_patterns["A"])


def load_schedule_config(path: str | Path) -> ShiftScheduleConfig:
    """Load and strictly validate a schedule configuration from JSON.

    ``ScheduleConfigError`` is used for malformed JSON, missing files, wrong
    field types, and semantic schedule errors so callers get one clear error
    boundary for configuration problems.
    """

    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            raw_config = json.load(
                config_file,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
    except OSError as exc:
        raise ScheduleConfigError(
            f"unable to read schedule config {config_path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ScheduleConfigError(
            f"invalid JSON in schedule config {config_path}: {exc.msg}"
        ) from exc
    except ScheduleConfigError:
        raise

    if not isinstance(raw_config, dict):
        raise ScheduleConfigError("schedule config root must be a JSON object")

    _validate_keys(raw_config, _CONFIG_KEYS, "schedule config")

    reference_date = _parse_date(raw_config["reference_date"], "reference_date")
    day_shift = _require_object(raw_config["day_shift"], "day_shift")
    night_shift = _require_object(raw_config["night_shift"], "night_shift")
    teams = _require_object(raw_config["teams"], "teams")

    _validate_keys(day_shift, _SHIFT_KEYS, "day_shift")
    _validate_keys(night_shift, _SHIFT_KEYS, "night_shift")

    team_patterns: dict[str, tuple[str, ...]] = {}
    for team_id, raw_pattern in teams.items():
        if not isinstance(team_id, str):
            raise ScheduleConfigError(
                f"teams contains a non-string team id: {team_id!r}"
            )
        if not isinstance(raw_pattern, list):
            raise ScheduleConfigError(
                f"teams.{team_id} must be a JSON array of day/night/off states"
            )
        team_patterns[team_id] = tuple(raw_pattern)

    return ShiftScheduleConfig(
        reference_date=reference_date,
        day_start=_parse_time(day_shift["start"], "day_shift.start"),
        day_end=_parse_time(day_shift["end"], "day_shift.end"),
        night_start=_parse_time(night_shift["start"], "night_shift.start"),
        night_end=_parse_time(night_shift["end"], "night_shift.end"),
        team_patterns=team_patterns,
    )


def _validate_schedule_values(
    *,
    reference_date: date,
    day_start: time,
    day_end: time,
    night_start: time,
    night_end: time,
    team_patterns: Mapping[str, tuple[str, ...]],
) -> None:
    if not isinstance(reference_date, date) or isinstance(reference_date, datetime):
        raise ScheduleConfigError("reference_date must be a date")

    for field_name, value in (
        ("day_start", day_start),
        ("day_end", day_end),
        ("night_start", night_start),
        ("night_end", night_end),
    ):
        if not isinstance(value, time):
            raise ScheduleConfigError(f"{field_name} must be a time")
        if value.tzinfo is not None:
            raise ScheduleConfigError(
                f"{field_name} must not contain timezone information"
            )

    if not isinstance(team_patterns, Mapping):
        raise ScheduleConfigError("team_patterns must be a mapping")

    actual_team_ids = set(team_patterns)
    if actual_team_ids != EXPECTED_TEAM_IDS:
        missing = sorted(EXPECTED_TEAM_IDS - actual_team_ids, key=str)
        unexpected = sorted(actual_team_ids - EXPECTED_TEAM_IDS, key=str)
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected {', '.join(map(str, unexpected))}")
        raise ScheduleConfigError(
            "teams must contain exactly three team ids A, B, and C"
            + (f" ({'; '.join(details)})" if details else "")
        )

    cycle_length: int | None = None
    for team_id in sorted(EXPECTED_TEAM_IDS):
        pattern = team_patterns[team_id]
        if not isinstance(pattern, (list, tuple)):
            raise ScheduleConfigError(
                f"teams.{team_id} must be a list or tuple of shift states"
            )
        if not pattern:
            raise ScheduleConfigError(f"teams.{team_id} pattern must not be empty")

        if cycle_length is None:
            cycle_length = len(pattern)
        elif len(pattern) != cycle_length:
            raise ScheduleConfigError(
                "all team patterns must have the same cycle length; "
                f"teams.{team_id} has {len(pattern)}, expected {cycle_length}"
            )

        for index, state in enumerate(pattern):
            if not isinstance(state, str) or state not in VALID_SHIFT_TYPES:
                raise ScheduleConfigError(
                    f"teams.{team_id}[{index}] has unknown state {state!r}; "
                    "expected day, night, or off"
                )

    assert cycle_length is not None  # The exact-team check guarantees this.
    for index in range(cycle_length):
        states = [team_patterns[team_id][index] for team_id in sorted(EXPECTED_TEAM_IDS)]
        counts = {state: states.count(state) for state in sorted(VALID_SHIFT_TYPES)}
        if any(count != 1 for count in counts.values()):
            raise ScheduleConfigError(
                f"cycle position {index} must contain exactly one day, one night, "
                f"and one off; counts are {counts}"
            )

    if day_end <= day_start:
        raise ScheduleConfigError(
            "day_shift must run forward within one date: "
            f"{day_start.isoformat()}-{day_end.isoformat()}"
        )
    day_duration = datetime.combine(date(2000, 1, 1), day_end) - datetime.combine(
        date(2000, 1, 1), day_start
    )
    if day_duration != timedelta(hours=12):
        raise ScheduleConfigError(
            "day_shift must be a 12-hour shift; "
            f"got {day_start.isoformat()}-{day_end.isoformat()}"
        )

    night_end_date = date(2000, 1, 2) if night_end <= night_start else date(2000, 1, 1)
    night_duration = datetime.combine(night_end_date, night_end) - datetime.combine(
        date(2000, 1, 1), night_start
    )
    if night_duration != timedelta(hours=12):
        raise ScheduleConfigError(
            "night_shift must be a 12-hour shift; "
            f"got {night_start.isoformat()}-{night_end.isoformat()}"
        )

    if day_end != night_start or night_end != day_start or night_end > night_start:
        raise ScheduleConfigError(
            "day_shift and night_shift must form continuous 24-hour coverage "
            "with night crossing midnight"
        )

    if (
        day_start != _STANDARD_DAY_START
        or day_end != _STANDARD_DAY_END
        or night_start != _STANDARD_NIGHT_START
        or night_end != _STANDARD_NIGHT_END
    ):
        raise ScheduleConfigError(
            "this schedule supports only the standard shifts "
            "day 08:00-20:00 and night 20:00-08:00"
        )


def _parse_date(value: Any, field_name: str) -> date:
    if not isinstance(value, str):
        raise ScheduleConfigError(f"{field_name} must be an ISO date string YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ScheduleConfigError(
            f"{field_name} must be an ISO date string YYYY-MM-DD; got {value!r}"
        ) from exc


def _parse_time(value: Any, field_name: str) -> time:
    if not isinstance(value, str):
        raise ScheduleConfigError(f"{field_name} must be an ISO time string HH:MM")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ScheduleConfigError(
            f"{field_name} must be an ISO time string HH:MM; got {value!r}"
        ) from exc
    if parsed.tzinfo is not None:
        raise ScheduleConfigError(f"{field_name} must not contain timezone information")
    return parsed


def _require_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScheduleConfigError(f"{field_name} must be a JSON object")
    return value


def _validate_keys(
    value: Mapping[str, Any], expected: frozenset[str], field_name: str
) -> None:
    actual = set(value)
    missing = sorted(expected - actual, key=str)
    unexpected = sorted(actual - expected, key=str)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected {', '.join(map(str, unexpected))}")
        raise ScheduleConfigError(
            f"{field_name} has invalid fields ({'; '.join(details)})"
        )


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScheduleConfigError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result

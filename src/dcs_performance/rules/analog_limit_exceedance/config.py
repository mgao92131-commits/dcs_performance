"""Immutable configuration models for continuous analog limit checks."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any


DEFAULT_RULE_ID = "analog_limit_exceedance"
SUPPORTED_SMOOTHING_METHODS = frozenset({"trailing_mean"})
DEFAULT_RULE_NAME = "连续量上下限超限考核"


@dataclass(frozen=True)
class SmoothingConfig:
    """Optional time-based preprocessing for one point."""

    enabled: bool = False
    method: str = "trailing_mean"
    window_seconds: float = 1.0
    min_samples: int = 1


@dataclass(frozen=True)
class LimitSideConfig:
    """The independent threshold and persistence settings for one side."""

    enabled: bool
    limit: float
    min_duration_seconds: float
    merge_gap_seconds: float


@dataclass(frozen=True)
class PointConfig:
    """Complete configuration for one Historian TAG."""

    id: str
    history_tag: str
    enabled: bool
    smoothing: SmoothingConfig
    low: LimitSideConfig
    high: LimitSideConfig


@dataclass(frozen=True)
class AnalogLimitExceedanceConfig:
    """Validated top-level configuration for the analog limit rule."""

    id: str
    name: str
    enabled: bool
    points: tuple[PointConfig, ...]
    assessment_window: Mapping[str, int]
    scoring: Mapping[str, Any]


# Concise spelling for integrations that use the generic rule-config name.
RuleConfig = AnalogLimitExceedanceConfig


def parse_config(raw_config: Mapping[str, Any]) -> AnalogLimitExceedanceConfig:
    """Validate a JSON-like mapping and return an immutable configuration.

    Limit and duration settings deliberately live on each point and on each
    side of that point.  This prevents one TAG's operating range from
    accidentally becoming the default for another TAG.
    """

    if not isinstance(raw_config, Mapping):
        raise ValueError(f"{DEFAULT_RULE_ID} config must be an object")

    rule_id = _required_text(raw_config, "id")
    if rule_id != DEFAULT_RULE_ID:
        raise ValueError(
            f"{DEFAULT_RULE_ID} config id must be {DEFAULT_RULE_ID!r}"
        )
    name = _required_text(raw_config, "name")
    enabled = _boolean(raw_config.get("enabled", True), "enabled")
    assessment_window = _parse_assessment_window(
        raw_config.get("assessment_window", {})
    )

    parameters = raw_config.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError(f"{DEFAULT_RULE_ID} parameters must be an object")
    raw_points = parameters.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError(
            f"{DEFAULT_RULE_ID} parameters.points must contain at least one point"
        )

    points: list[PointConfig] = []
    point_ids: set[str] = set()
    history_tags: set[str] = set()
    for index, raw_point in enumerate(raw_points):
        point = _parse_point(raw_point, index)
        if point.id in point_ids:
            raise ValueError(f"duplicate {DEFAULT_RULE_ID} point id: {point.id!r}")
        if point.history_tag in history_tags:
            raise ValueError(
                f"duplicate {DEFAULT_RULE_ID} history_tag: {point.history_tag!r}"
            )
        point_ids.add(point.id)
        history_tags.add(point.history_tag)
        points.append(point)

    raw_scoring = raw_config.get("scoring", {})
    if not isinstance(raw_scoring, Mapping):
        raise ValueError(f"{DEFAULT_RULE_ID} scoring must be an object")

    return AnalogLimitExceedanceConfig(
        id=rule_id,
        name=name,
        enabled=enabled,
        points=tuple(points),
        assessment_window=MappingProxyType(
            {key: value for key, value in assessment_window.items()}
        ),
        scoring=_freeze_mapping(raw_scoring),
    )


def validate_config(
    raw_config: Mapping[str, Any],
) -> AnalogLimitExceedanceConfig:
    """Validate and parse a rule configuration.

    This explicit spelling is useful to callers that want validation without
    relying on the parser's name.
    """

    return parse_config(raw_config)


def load_config(
    source: str | Path | Mapping[str, Any],
) -> AnalogLimitExceedanceConfig:
    """Load and validate a JSON file, or validate an existing mapping."""

    if isinstance(source, Mapping):
        return parse_config(source)
    if not isinstance(source, (str, Path)):
        raise TypeError("config source must be a path or mapping")

    path = Path(source)
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw_config = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {DEFAULT_RULE_ID} config: {path}") from exc
    return parse_config(raw_config)


# Compatibility spelling used by the other analog rule.
parse_rule_config = parse_config


def _parse_point(raw_point: object, index: int) -> PointConfig:
    if not isinstance(raw_point, Mapping):
        raise ValueError(f"parameters.points[{index}] must be an object")

    prefix = f"parameters.points[{index}]"
    point_id = _required_text(raw_point, "id", prefix=prefix)
    history_tag = _required_text(raw_point, "history_tag", prefix=prefix)
    enabled = _boolean(raw_point.get("enabled", True), f"{prefix}.enabled")
    smoothing = _parse_smoothing(raw_point.get("smoothing"), prefix)

    low = _parse_side(raw_point.get("low"), f"{prefix}.low")
    high = _parse_side(raw_point.get("high"), f"{prefix}.high")
    if not low.enabled and not high.enabled:
        raise ValueError(
            f"{prefix} must enable at least one of low or high limit checks"
        )
    if low.enabled and high.enabled and not low.limit < high.limit:
        raise ValueError(
            f"{prefix}.low.limit must be less than {prefix}.high.limit"
        )

    return PointConfig(
        id=point_id,
        history_tag=history_tag,
        enabled=enabled,
        smoothing=smoothing,
        low=low,
        high=high,
    )


def _parse_smoothing(raw: object, prefix: str) -> SmoothingConfig:
    if raw is None:
        return SmoothingConfig()
    if not isinstance(raw, Mapping):
        raise ValueError(f"{prefix}.smoothing must be an object")

    enabled = _boolean(raw.get("enabled", True), f"{prefix}.smoothing.enabled")
    method = raw.get("method", "trailing_mean")
    if not isinstance(method, str) or method not in SUPPORTED_SMOOTHING_METHODS:
        allowed = ", ".join(sorted(SUPPORTED_SMOOTHING_METHODS))
        raise ValueError(f"{prefix}.smoothing.method must be one of: {allowed}")
    return SmoothingConfig(
        enabled=enabled,
        method=method,
        window_seconds=_positive_number(
            raw.get("window_seconds", 1),
            f"{prefix}.smoothing.window_seconds",
        ),
        min_samples=_positive_int(
            raw.get("min_samples", 1),
            f"{prefix}.smoothing.min_samples",
        ),
    )


def _parse_side(raw: object, prefix: str) -> LimitSideConfig:
    """Parse one side, allowing an omitted side to mean disabled.

    A disabled side does not participate in detection.  Its omitted numeric
    values receive harmless defaults, while any values supplied explicitly
    are still validated so malformed configuration is not silently accepted.
    """

    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{prefix} must be an object")

    enabled = _boolean(raw.get("enabled", False), f"{prefix}.enabled")
    limit = _finite_number(raw.get("limit", 0.0), f"{prefix}.limit")
    min_duration = _positive_number(
        raw.get("min_duration_seconds", 1.0),
        f"{prefix}.min_duration_seconds",
    )
    merge_gap = _nonnegative_number(
        raw.get("merge_gap_seconds", 0.0),
        f"{prefix}.merge_gap_seconds",
    )
    return LimitSideConfig(
        enabled=enabled,
        limit=limit,
        min_duration_seconds=min_duration,
        merge_gap_seconds=merge_gap,
    )


def _parse_assessment_window(raw: object) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        raise ValueError("assessment_window must be an object")
    return {
        "start_offset_minutes": _integer(
            raw.get("start_offset_minutes", 0),
            "assessment_window.start_offset_minutes",
        ),
        "end_offset_minutes": _integer(
            raw.get("end_offset_minutes", 0),
            "assessment_window.end_offset_minutes",
        ),
    }


def _required_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    prefix: str = "config",
) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{prefix}.{key} must be non-empty text")
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _positive_int(value: object, field_name: str) -> int:
    result = _integer(value, field_name)
    if result <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    return result


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{field_name} must be a finite number")
    return numeric


def _positive_number(value: object, field_name: str) -> float:
    numeric = _finite_number(value, field_name)
    if numeric <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    return numeric


def _nonnegative_number(value: object, field_name: str) -> float:
    numeric = _finite_number(value, field_name)
    if numeric < 0:
        raise ValueError(
            f"{field_name} must be greater than or equal to zero"
        )
    return numeric


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, Mapping):
            frozen[key] = _freeze_mapping(item)
        elif isinstance(item, list):
            frozen[key] = tuple(
                _freeze_mapping(entry) if isinstance(entry, Mapping) else entry
                for entry in item
            )
        else:
            frozen[key] = item
    return MappingProxyType(frozen)

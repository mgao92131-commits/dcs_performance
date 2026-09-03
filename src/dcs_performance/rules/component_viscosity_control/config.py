"""Validated configuration for the component-viscosity trend rule."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any


DEFAULT_RULE_ID = "component_viscosity_control"
DEFAULT_RULE_NAME = "组件粘度趋势控制"
SUPPORTED_AGGREGATIONS = frozenset({"median"})
SUPPORTED_SMOOTHING_METHODS = frozenset({"trailing_mean"})
SUPPORTED_EXCLUSION_METHODS = frozenset({"robust_deviation", "rolling_range"})


@dataclass(frozen=True)
class AggregationConfig:
    """Fixed-time bucket aggregation applied before smoothing."""

    enabled: bool = True
    method: str = "median"
    bucket_seconds: int = 60
    min_samples: int = 1


@dataclass(frozen=True)
class SmoothingConfig:
    """Trailing trend calculation applied to one-minute aggregates."""

    enabled: bool = True
    method: str = "trailing_mean"
    window_seconds: int = 600
    min_samples: int = 10


@dataclass(frozen=True)
class AssessmentConfig:
    """Target, limits, persistence and recovery-gap settings."""

    target: float
    low_limit: float
    high_limit: float
    min_duration_seconds: float
    merge_gap_seconds: float


@dataclass(frozen=True)
class ExclusionConfig:
    """Optional same-point disturbance exclusion settings."""

    enabled: bool = False
    method: str = "robust_deviation"
    baseline: float = 0.0
    deviation_threshold: float = 1.0
    window_seconds: int = 3600
    range_threshold: float = 1.0
    merge_gap_seconds: float = 600.0
    remove_after_start_seconds: float = 7200.0


@dataclass(frozen=True)
class PointConfig:
    """Complete configuration for one viscosity proxy point."""

    id: str
    history_tag: str
    enabled: bool
    aggregation: AggregationConfig
    smoothing: SmoothingConfig
    assessment: AssessmentConfig
    exclusion: ExclusionConfig


@dataclass(frozen=True)
class ComponentViscosityControlConfig:
    """Validated top-level rule configuration."""

    id: str
    name: str
    enabled: bool
    points: tuple[PointConfig, ...]
    assessment_window: Mapping[str, int]
    scoring: Mapping[str, Any]


RuleConfig = ComponentViscosityControlConfig


def parse_config(raw_config: Mapping[str, Any]) -> ComponentViscosityControlConfig:
    if not isinstance(raw_config, Mapping):
        raise ValueError(f"{DEFAULT_RULE_ID} config must be an object")

    rule_id = _required_text(raw_config, "id")
    if rule_id != DEFAULT_RULE_ID:
        raise ValueError(f"{DEFAULT_RULE_ID} config id must be {DEFAULT_RULE_ID!r}")
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

    scoring = raw_config.get("scoring", {})
    if not isinstance(scoring, Mapping):
        raise ValueError(f"{DEFAULT_RULE_ID} scoring must be an object")

    return ComponentViscosityControlConfig(
        id=rule_id,
        name=name,
        enabled=enabled,
        points=tuple(points),
        assessment_window=MappingProxyType(dict(assessment_window)),
        scoring=_freeze_mapping(scoring),
    )


def validate_config(
    raw_config: Mapping[str, Any],
) -> ComponentViscosityControlConfig:
    return parse_config(raw_config)


def load_config(
    source: str | Path | Mapping[str, Any],
) -> ComponentViscosityControlConfig:
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


def _parse_point(raw_point: object, index: int) -> PointConfig:
    if not isinstance(raw_point, Mapping):
        raise ValueError(f"parameters.points[{index}] must be an object")
    prefix = f"parameters.points[{index}]"
    point_id = _required_text(raw_point, "id", prefix=prefix)
    history_tag = _required_text(raw_point, "history_tag", prefix=prefix)
    enabled = _boolean(raw_point.get("enabled", True), f"{prefix}.enabled")
    aggregation = _parse_aggregation(raw_point.get("aggregation"), prefix)
    smoothing = _parse_smoothing(raw_point.get("smoothing"), prefix)
    if smoothing.enabled and smoothing.window_seconds % aggregation.bucket_seconds != 0:
        raise ValueError(
            f"{prefix}.smoothing.window_seconds must be a multiple of "
            f"{prefix}.aggregation.bucket_seconds"
        )
    assessment = _parse_assessment(raw_point.get("assessment"), prefix)
    exclusion = _parse_exclusion(raw_point.get("exclusion"), prefix)
    if exclusion.method == "rolling_range" and (
        exclusion.window_seconds % aggregation.bucket_seconds != 0
    ):
        raise ValueError(
            f"{prefix}.exclusion.window_seconds must be a multiple of "
            f"{prefix}.aggregation.bucket_seconds"
        )
    return PointConfig(
        id=point_id,
        history_tag=history_tag,
        enabled=enabled,
        aggregation=aggregation,
        smoothing=smoothing,
        assessment=assessment,
        exclusion=exclusion,
    )


def _parse_aggregation(raw: object, prefix: str) -> AggregationConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{prefix}.aggregation must be an object")
    enabled = _boolean(raw.get("enabled", True), f"{prefix}.aggregation.enabled")
    method = raw.get("method", "median")
    if not isinstance(method, str) or method not in SUPPORTED_AGGREGATIONS:
        allowed = ", ".join(sorted(SUPPORTED_AGGREGATIONS))
        raise ValueError(f"{prefix}.aggregation.method must be one of: {allowed}")
    return AggregationConfig(
        enabled=enabled,
        method=method,
        bucket_seconds=_positive_int(
            raw.get("bucket_seconds", 60),
            f"{prefix}.aggregation.bucket_seconds",
        ),
        min_samples=_positive_int(
            raw.get("min_samples", 1),
            f"{prefix}.aggregation.min_samples",
        ),
    )


def _parse_smoothing(raw: object, prefix: str) -> SmoothingConfig:
    if raw is None:
        raw = {}
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
        window_seconds=_positive_int(
            raw.get("window_seconds", 600),
            f"{prefix}.smoothing.window_seconds",
        ),
        min_samples=_positive_int(
            raw.get("min_samples", 10),
            f"{prefix}.smoothing.min_samples",
        ),
    )


def _parse_assessment(raw: object, prefix: str) -> AssessmentConfig:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{prefix}.assessment must be an object")
    target = _finite_number(raw.get("target"), f"{prefix}.assessment.target")
    low_limit = _finite_number(raw.get("low_limit"), f"{prefix}.assessment.low_limit")
    high_limit = _finite_number(raw.get("high_limit"), f"{prefix}.assessment.high_limit")
    if low_limit >= high_limit:
        raise ValueError(
            f"{prefix}.assessment.low_limit must be less than high_limit"
        )
    return AssessmentConfig(
        target=target,
        low_limit=low_limit,
        high_limit=high_limit,
        min_duration_seconds=_positive_number(
            raw.get("min_duration_seconds", 600),
            f"{prefix}.assessment.min_duration_seconds",
        ),
        merge_gap_seconds=_nonnegative_number(
            raw.get("merge_gap_seconds", 600),
            f"{prefix}.assessment.merge_gap_seconds",
        ),
    )


def _parse_exclusion(raw: object, prefix: str) -> ExclusionConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{prefix}.exclusion must be an object")
    enabled = _boolean(raw.get("enabled", False), f"{prefix}.exclusion.enabled")
    method = raw.get("method", "robust_deviation")
    if not isinstance(method, str) or method not in SUPPORTED_EXCLUSION_METHODS:
        allowed = ", ".join(sorted(SUPPORTED_EXCLUSION_METHODS))
        raise ValueError(f"{prefix}.exclusion.method must be one of: {allowed}")
    baseline = _finite_number(
        raw.get("baseline", 0.0),
        f"{prefix}.exclusion.baseline",
    )
    deviation_threshold = _positive_number(
        raw.get("deviation_threshold", 1.0),
        f"{prefix}.exclusion.deviation_threshold",
    )
    window_seconds = _positive_int(
        raw.get("window_seconds", 3600),
        f"{prefix}.exclusion.window_seconds",
    )
    range_threshold = _positive_number(
        raw.get("range_threshold", 1.0),
        f"{prefix}.exclusion.range_threshold",
    )
    return ExclusionConfig(
        enabled=enabled,
        method=method,
        baseline=baseline,
        deviation_threshold=deviation_threshold,
        window_seconds=window_seconds,
        range_threshold=range_threshold,
        merge_gap_seconds=_nonnegative_number(
            raw.get("merge_gap_seconds", 600),
            f"{prefix}.exclusion.merge_gap_seconds",
        ),
        remove_after_start_seconds=_positive_number(
            raw.get("remove_after_start_seconds", 7200),
            f"{prefix}.exclusion.remove_after_start_seconds",
        ),
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


def _required_text(mapping: Mapping[str, Any], key: str, *, prefix: str = "config") -> str:
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
        raise ValueError(f"{field_name} must be greater than or equal to zero")
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

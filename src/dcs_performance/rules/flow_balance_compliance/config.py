"""Configuration models for the slurry flow balance rule."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any


DEFAULT_RULE_ID = "flow_balance_compliance"


@dataclass(frozen=True)
class SmoothingConfig:
    enabled: bool = True
    window_seconds: float = 60.0
    min_samples: int = 1


@dataclass(frozen=True)
class PointConfig:
    id: str
    logic_tag: str
    sy_tags: tuple[str, str]
    enabled: bool
    smoothing: SmoothingConfig
    low_limit: float
    high_limit: float
    min_duration_seconds: float
    merge_gap_seconds: float
    max_gap_seconds: float


@dataclass(frozen=True)
class FlowBalanceConfig:
    id: str
    name: str
    enabled: bool
    points: tuple[PointConfig, ...]
    scoring: Mapping[str, Any]


def parse_config(raw_config: Mapping[str, Any]) -> FlowBalanceConfig:
    if not isinstance(raw_config, Mapping):
        raise ValueError(f"{DEFAULT_RULE_ID} config must be an object")
    rule_id = _text(raw_config.get("id"), "id")
    if rule_id != DEFAULT_RULE_ID:
        raise ValueError(f"config id must be {DEFAULT_RULE_ID!r}")
    name = _text(raw_config.get("name"), "name")
    enabled = _bool(raw_config.get("enabled", True), "enabled")
    parameters = raw_config.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError("parameters must be an object")
    raw_points = parameters.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError("parameters.points must contain at least one point")

    points: list[PointConfig] = []
    point_ids: set[str] = set()
    for index, raw_point in enumerate(raw_points):
        prefix = f"parameters.points[{index}]"
        if not isinstance(raw_point, Mapping):
            raise ValueError(f"{prefix} must be an object")
        point_id = _text(raw_point.get("id"), f"{prefix}.id")
        if point_id in point_ids:
            raise ValueError(f"duplicate point id: {point_id!r}")
        point_ids.add(point_id)
        logic_tag = _text(raw_point.get("logic_tag"), f"{prefix}.logic_tag")
        raw_sy_tags = raw_point.get("sy_tags")
        if (
            not isinstance(raw_sy_tags, list)
            or len(raw_sy_tags) != 2
            or any(not isinstance(tag, str) or not tag.strip() for tag in raw_sy_tags)
        ):
            raise ValueError(f"{prefix}.sy_tags must contain two non-empty tags")
        sy_tags = (raw_sy_tags[0], raw_sy_tags[1])
        if logic_tag in sy_tags or sy_tags[0] == sy_tags[1]:
            raise ValueError(f"{prefix} tags must be different")
        smoothing = _parse_smoothing(raw_point.get("smoothing", {}), prefix)
        low_limit = _finite(raw_point.get("low_limit"), f"{prefix}.low_limit")
        high_limit = _finite(raw_point.get("high_limit"), f"{prefix}.high_limit")
        if low_limit >= high_limit:
            raise ValueError(f"{prefix}.low_limit must be less than high_limit")
        points.append(
            PointConfig(
                id=point_id,
                logic_tag=logic_tag,
                sy_tags=sy_tags,
                enabled=_bool(raw_point.get("enabled", True), f"{prefix}.enabled"),
                smoothing=smoothing,
                low_limit=low_limit,
                high_limit=high_limit,
                min_duration_seconds=_positive(
                    raw_point.get("min_duration_seconds"),
                    f"{prefix}.min_duration_seconds",
                ),
                merge_gap_seconds=_nonnegative(
                    raw_point.get("merge_gap_seconds", 0),
                    f"{prefix}.merge_gap_seconds",
                ),
                max_gap_seconds=_positive(
                    raw_point.get("max_gap_seconds", 60),
                    f"{prefix}.max_gap_seconds",
                ),
            )
        )

    scoring = raw_config.get("scoring", {})
    if not isinstance(scoring, Mapping):
        raise ValueError("scoring must be an object")
    return FlowBalanceConfig(
        id=rule_id,
        name=name,
        enabled=enabled,
        points=tuple(points),
        scoring=scoring,
    )


def load_config(source: str | Path | Mapping[str, Any]) -> FlowBalanceConfig:
    if isinstance(source, Mapping):
        return parse_config(source)
    path = Path(source)
    with path.open("r", encoding="utf-8") as handle:
        return parse_config(json.load(handle))


def _parse_smoothing(raw: object, prefix: str) -> SmoothingConfig:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{prefix}.smoothing must be an object")
    if raw.get("method", "trailing_mean") != "trailing_mean":
        raise ValueError(f"{prefix}.smoothing.method must be 'trailing_mean'")
    return SmoothingConfig(
        enabled=_bool(raw.get("enabled", True), f"{prefix}.smoothing.enabled"),
        window_seconds=_positive(
            raw.get("window_seconds", 60),
            f"{prefix}.smoothing.window_seconds",
        ),
        min_samples=_positive_int(
            raw.get("min_samples", 1),
            f"{prefix}.smoothing.min_samples",
        ),
    )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be boolean")
    return value


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _positive(value: object, field: str) -> float:
    result = _finite(value, field)
    if result <= 0:
        raise ValueError(f"{field} must be positive")
    return result


def _nonnegative(value: object, field: str) -> float:
    result = _finite(value, field)
    if result < 0:
        raise ValueError(f"{field} must be non-negative")
    return result


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value

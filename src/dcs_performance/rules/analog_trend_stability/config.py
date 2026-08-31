"""Immutable configuration models for the analog trend stability rule.

The important boundary in this module is ``PointConfig``.  Every configured
point carries its own quality, trend, stability, and drift settings; no
analysis setting is lifted to the rule level.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any


SUPPORTED_TREND_METHODS = frozenset({"rolling_mean"})
SUPPORTED_ALIGNMENTS = frozenset({"centered", "trailing"})


@dataclass(frozen=True)
class QualityConfig:
    """Rules for deciding when raw Historian samples are continuous."""

    max_gap_seconds: float = 60.0


@dataclass(frozen=True)
class TrendConfig:
    """The time-based trend calculation for one point."""

    method: str = "rolling_mean"
    alignment: str = "centered"
    window_seconds: float = 1800.0
    min_samples: int = 30


@dataclass(frozen=True)
class StabilityConfig:
    """Short-period deviation thresholds for one point."""

    enabled: bool = False
    warning_deviation: float = 0.0
    high_deviation: float = 1.0
    min_duration_seconds: float = 1.0
    merge_gap_seconds: float = 0.0


@dataclass(frozen=True)
class DriftWindowConfig:
    """One independently configured trend-drift horizon."""

    id: str
    window_seconds: float
    warning_change: float
    high_change: float
    min_duration_seconds: float


@dataclass(frozen=True)
class DriftConfig:
    """Trend-drift settings, including any number of windows."""

    enabled: bool = False
    merge_gap_seconds: float = 0.0
    windows: tuple[DriftWindowConfig, ...] = ()

    @property
    def max_window_seconds(self) -> float:
        """Return the longest active drift window, or zero when unavailable."""

        if not self.windows:
            return 0.0
        return max(window.window_seconds for window in self.windows)


@dataclass(frozen=True)
class PointConfig:
    """A complete, independent analysis unit for one Historian TAG."""

    id: str
    history_tag: str
    enabled: bool
    quality: QualityConfig
    trend: TrendConfig
    stability: StabilityConfig
    drift: DriftConfig

    @property
    def max_drift_window_seconds(self) -> float:
        """Return the longest configured drift horizon for query planning."""

        if not self.drift.enabled:
            return 0.0
        return self.drift.max_window_seconds


@dataclass(frozen=True)
class AnalogTrendStabilityConfig:
    """Validated top-level configuration for ``analog_trend_stability``."""

    id: str
    name: str
    enabled: bool
    points: tuple[PointConfig, ...]
    assessment_window: Mapping[str, int]
    scoring: Mapping[str, Any]


# A short, discoverable name for callers that do not need the long rule name.
RuleConfig = AnalogTrendStabilityConfig


def parse_config(raw_config: Mapping[str, Any]) -> AnalogTrendStabilityConfig:
    """Validate a JSON-like mapping and return immutable configuration.

    Validation is deliberately strict for fields that affect time ranges or
    thresholds.  Disabled stability/drift sections still receive complete
    immutable objects, which lets callers avoid conditionals around optional
    sections while allowing a point to enable only one detector.
    """

    if not isinstance(raw_config, Mapping):
        raise ValueError("analog_trend_stability config must be an object")

    rule_id = _required_text(raw_config, "id")
    if rule_id != "analog_trend_stability":
        raise ValueError(
            "analog_trend_stability config id must be 'analog_trend_stability'"
        )
    name = _required_text(raw_config, "name")
    enabled = _boolean(raw_config.get("enabled", True), "enabled")

    assessment_window = _parse_assessment_window(
        raw_config.get("assessment_window", {})
    )

    parameters = raw_config.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError("analog_trend_stability parameters must be an object")
    raw_points = parameters.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError(
            "analog_trend_stability parameters.points must contain at least one point"
        )

    points: list[PointConfig] = []
    point_ids: set[str] = set()
    for index, raw_point in enumerate(raw_points):
        point = _parse_point(raw_point, index)
        if point.id in point_ids:
            raise ValueError(f"duplicate analog_trend_stability point id: {point.id!r}")
        point_ids.add(point.id)
        points.append(point)

    raw_scoring = raw_config.get("scoring", {})
    if not isinstance(raw_scoring, Mapping):
        raise ValueError("analog_trend_stability scoring must be an object")

    return AnalogTrendStabilityConfig(
        id=rule_id,
        name=name,
        enabled=enabled,
        points=tuple(points),
        assessment_window=MappingProxyType(
            {key: value for key, value in assessment_window.items()}
        ),
        scoring=_freeze_mapping(raw_scoring),
    )


def load_config(source: str | Path | Mapping[str, Any]) -> AnalogTrendStabilityConfig:
    """Load and validate a JSON file, or validate an already decoded mapping."""

    if isinstance(source, Mapping):
        return parse_config(source)
    if not isinstance(source, (str, Path)):
        raise TypeError("config source must be a path or mapping")
    path = Path(source)
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw_config = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read analog trend stability config: {path}") from exc
    return parse_config(raw_config)


# Friendly aliases used by small integrations and tests.
parse_rule_config = parse_config
validate_config = parse_config


def _parse_point(raw_point: object, index: int) -> PointConfig:
    if not isinstance(raw_point, Mapping):
        raise ValueError(f"parameters.points[{index}] must be an object")

    prefix = f"parameters.points[{index}]"
    point_id = _required_text(raw_point, "id", prefix=prefix)
    history_tag = _required_text(raw_point, "history_tag", prefix=prefix)
    enabled = _boolean(raw_point.get("enabled", True), f"{prefix}.enabled")

    quality = _parse_quality(raw_point.get("quality", {}), prefix)
    raw_trend = raw_point.get("trend")
    if not isinstance(raw_trend, Mapping):
        raise ValueError(f"{prefix}.trend must be an object")
    trend = _parse_trend(raw_trend, prefix)
    stability = _parse_stability(raw_point.get("stability", {}), prefix)
    drift = _parse_drift(raw_point.get("drift", {}), prefix)

    return PointConfig(
        id=point_id,
        history_tag=history_tag,
        enabled=enabled,
        quality=quality,
        trend=trend,
        stability=stability,
        drift=drift,
    )


def _parse_quality(raw: object, prefix: str) -> QualityConfig:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{prefix}.quality must be an object")
    return QualityConfig(
        max_gap_seconds=_positive_number(
            raw.get("max_gap_seconds", 60),
            f"{prefix}.quality.max_gap_seconds",
        )
    )


def _parse_trend(raw: Mapping[str, Any], prefix: str) -> TrendConfig:
    method = raw.get("method", "rolling_mean")
    if not isinstance(method, str) or method not in SUPPORTED_TREND_METHODS:
        allowed = ", ".join(sorted(SUPPORTED_TREND_METHODS))
        raise ValueError(
            f"{prefix}.trend.method must be one of: {allowed}"
        )

    alignment = raw.get("alignment", "centered")
    if not isinstance(alignment, str) or alignment not in SUPPORTED_ALIGNMENTS:
        raise ValueError(
            f"{prefix}.trend.alignment must be 'centered' or 'trailing'"
        )

    min_samples = _positive_int(
        raw.get("min_samples", 1),
        f"{prefix}.trend.min_samples",
    )
    return TrendConfig(
        method=method,
        alignment=alignment,
        window_seconds=_positive_number(
            raw.get("window_seconds"),
            f"{prefix}.trend.window_seconds",
        ),
        min_samples=min_samples,
    )


def _parse_stability(raw: object, prefix: str) -> StabilityConfig:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{prefix}.stability must be an object")
    enabled = _boolean(raw.get("enabled", False), f"{prefix}.stability.enabled")
    warning = _nonnegative_number(
        raw.get("warning_deviation", 0),
        f"{prefix}.stability.warning_deviation",
    )
    high = _positive_number(
        raw.get("high_deviation", max(1.0, warning + 1.0)),
        f"{prefix}.stability.high_deviation",
    )
    if high <= warning:
        raise ValueError(
            f"{prefix}.stability.high_deviation must be greater than warning_deviation"
        )
    return StabilityConfig(
        enabled=enabled,
        warning_deviation=warning,
        high_deviation=high,
        min_duration_seconds=_positive_number(
            raw.get("min_duration_seconds", 1),
            f"{prefix}.stability.min_duration_seconds",
        ),
        merge_gap_seconds=_nonnegative_number(
            raw.get("merge_gap_seconds", 0),
            f"{prefix}.stability.merge_gap_seconds",
        ),
    )


def _parse_drift(raw: object, prefix: str) -> DriftConfig:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{prefix}.drift must be an object")
    enabled = _boolean(raw.get("enabled", False), f"{prefix}.drift.enabled")
    merge_gap = _nonnegative_number(
        raw.get("merge_gap_seconds", 0),
        f"{prefix}.drift.merge_gap_seconds",
    )
    raw_windows = raw.get("windows", [])
    if not isinstance(raw_windows, list):
        raise ValueError(f"{prefix}.drift.windows must be an array")
    if enabled and not raw_windows:
        raise ValueError(f"{prefix}.drift.windows must not be empty when drift is enabled")

    windows: list[DriftWindowConfig] = []
    window_ids: set[str] = set()
    for index, raw_window in enumerate(raw_windows):
        window_prefix = f"{prefix}.drift.windows[{index}]"
        if not isinstance(raw_window, Mapping):
            raise ValueError(f"{window_prefix} must be an object")
        window_id = _required_text(raw_window, "id", prefix=window_prefix)
        if window_id in window_ids:
            raise ValueError(f"duplicate drift window id: {window_id!r}")
        window_ids.add(window_id)
        warning = _nonnegative_number(
            raw_window.get("warning_change"),
            f"{window_prefix}.warning_change",
        )
        high = _positive_number(
            raw_window.get("high_change"),
            f"{window_prefix}.high_change",
        )
        if high <= warning:
            raise ValueError(
                f"{window_prefix}.high_change must be greater than warning_change"
            )
        windows.append(
            DriftWindowConfig(
                id=window_id,
                window_seconds=_positive_number(
                    raw_window.get("window_seconds"),
                    f"{window_prefix}.window_seconds",
                ),
                warning_change=warning,
                high_change=high,
                min_duration_seconds=_positive_number(
                    raw_window.get("min_duration_seconds"),
                    f"{window_prefix}.min_duration_seconds",
                ),
            )
        )

    return DriftConfig(
        enabled=enabled,
        merge_gap_seconds=merge_gap,
        windows=tuple(windows),
    )


def _parse_assessment_window(raw: object) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        raise ValueError("assessment_window must be an object")
    start_offset = _integer(
        raw.get("start_offset_minutes", 0),
        "assessment_window.start_offset_minutes",
    )
    end_offset = _integer(
        raw.get("end_offset_minutes", 0),
        "assessment_window.end_offset_minutes",
    )
    if start_offset != 0 or end_offset != 0:
        raise ValueError(
            "analog_trend_stability assessment_window offsets must both be zero; "
            "trend preheating is planned internally"
        )
    return {
        "start_offset_minutes": start_offset,
        "end_offset_minutes": end_offset,
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


def _positive_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number greater than zero")
    result = float(value)
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{field_name} must be a finite number greater than zero")
    return result


def _nonnegative_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number greater than or equal to zero")
    result = float(value)
    if not isfinite(result) or result < 0:
        raise ValueError(
            f"{field_name} must be a finite number greater than or equal to zero"
        )
    return result


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

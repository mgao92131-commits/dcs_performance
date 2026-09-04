"""Component viscosity trend control rule."""

from .config import (
    AssessmentConfig,
    AggregationConfig,
    ComponentViscosityControlConfig,
    ExclusionConfig,
    PointConfig,
    RepeatPenaltyConfig,
    SmoothingConfig,
    load_config,
    parse_config,
    validate_config,
)
from .detector import (
    DisturbanceWindow,
    MetricPoint,
    MinuteMedian,
    aggregate_minute_medians,
    calculate_metric,
    calculate_trailing_mean,
    detect_disturbance_windows,
    exclude_disturbances,
    split_contiguous_segments,
)
from .rule import Rule
from .scoring import calculate_penalty_checkpoints, calculate_penalty_units

__all__ = [
    "AssessmentConfig",
    "AggregationConfig",
    "ComponentViscosityControlConfig",
    "DisturbanceWindow",
    "ExclusionConfig",
    "MetricPoint",
    "MinuteMedian",
    "PointConfig",
    "RepeatPenaltyConfig",
    "Rule",
    "SmoothingConfig",
    "aggregate_minute_medians",
    "calculate_metric",
    "calculate_penalty_checkpoints",
    "calculate_penalty_units",
    "calculate_trailing_mean",
    "detect_disturbance_windows",
    "exclude_disturbances",
    "load_config",
    "parse_config",
    "split_contiguous_segments",
    "validate_config",
]

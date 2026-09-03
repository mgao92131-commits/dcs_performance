"""Assessment rule for sustained esterification-level rate violations."""

from .config import LevelRateConfig, PointConfig, SmoothingConfig, load_config, parse_config
from .detector import (
    RATE_DOWN,
    RATE_UP,
    LevelRateDetector,
    LevelRateOccurrence,
    RatePoint,
    calculate_rate_points,
    detect_rate_events,
)
from .rule import Rule

__all__ = [
    "LevelRateConfig",
    "LevelRateDetector",
    "LevelRateOccurrence",
    "PointConfig",
    "RATE_DOWN",
    "RATE_UP",
    "RatePoint",
    "Rule",
    "SmoothingConfig",
    "calculate_rate_points",
    "detect_rate_events",
    "load_config",
    "parse_config",
]

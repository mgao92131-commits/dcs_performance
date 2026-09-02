"""Continuous analog upper/lower limit exceedance assessment rule."""

from .config import (
    AnalogLimitExceedanceConfig,
    LimitSideConfig,
    PointConfig,
    SmoothingConfig,
    load_config,
    parse_config,
    validate_config,
)
from .detector import (
    AnalogLimitExceedanceDetector,
    AnalogValueParseError,
    Detector,
    LimitEventType,
    LimitOccurrence,
    detect,
    detect_analog_limit_exceedance,
    detect_analog_limit_exceedances,
    detect_limit_occurrences,
    parse_analog_value,
)
from .smoothing import smooth_history_samples
from .rule import Rule

__all__ = [
    "AnalogLimitExceedanceConfig",
    "AnalogLimitExceedanceDetector",
    "AnalogValueParseError",
    "Detector",
    "LimitEventType",
    "LimitOccurrence",
    "LimitSideConfig",
    "PointConfig",
    "SmoothingConfig",
    "Rule",
    "detect",
    "detect_analog_limit_exceedance",
    "detect_analog_limit_exceedances",
    "detect_limit_occurrences",
    "load_config",
    "parse_analog_value",
    "parse_config",
    "smooth_history_samples",
    "validate_config",
]

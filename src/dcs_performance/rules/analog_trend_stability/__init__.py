"""Independent continuous-value trend stability assessment rule."""

from dcs_performance.rules.analog_trend_stability.config import (
    AnalogTrendStabilityConfig,
    DriftConfig,
    DriftWindowConfig,
    PointConfig,
    QualityConfig,
    StabilityConfig,
    TrendConfig,
    load_config,
    parse_config,
)
from dcs_performance.rules.analog_trend_stability.detector import (
    AnalogTrendStabilityDetector,
    Detector,
    DriftEvidence,
    DriftOccurrence,
    StabilityOccurrence,
    detect_drift_events,
    detect_stability_events,
)
from dcs_performance.rules.analog_trend_stability.rule import (
    QueryGroup,
    QueryPlan,
    QueryPlanner,
    Rule,
)
from dcs_performance.rules.analog_trend_stability.trend import (
    DriftPoint,
    NumericSample,
    TrendPoint,
    calculate_drift,
    calculate_trend,
    split_numeric_segments,
)

__all__ = [
    "AnalogTrendStabilityConfig",
    "AnalogTrendStabilityDetector",
    "DriftConfig",
    "Detector",
    "DriftEvidence",
    "DriftOccurrence",
    "DriftPoint",
    "DriftWindowConfig",
    "NumericSample",
    "PointConfig",
    "QualityConfig",
    "QueryGroup",
    "QueryPlan",
    "QueryPlanner",
    "Rule",
    "StabilityConfig",
    "StabilityOccurrence",
    "TrendConfig",
    "TrendPoint",
    "calculate_drift",
    "calculate_trend",
    "detect_drift_events",
    "detect_stability_events",
    "load_config",
    "parse_config",
    "split_numeric_segments",
]

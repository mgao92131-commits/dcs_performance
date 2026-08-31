"""Pump-group flow compliance rule package."""

from .detector import (
    DigitalStateParseError,
    Detector,
    FlowValueParseError,
    LOW_FLOW,
    SWITCH_TIMEOUT,
    PumpFlowComplianceDetector,
    PumpFlowDetector,
    PumpFlowEvent,
    PumpFlowOccurrence,
    PumpMode,
    parse_digital_state,
    parse_flow_value,
)

__all__ = [
    "DigitalStateParseError",
    "Detector",
    "FlowValueParseError",
    "LOW_FLOW",
    "SWITCH_TIMEOUT",
    "PumpFlowComplianceDetector",
    "PumpFlowDetector",
    "PumpFlowEvent",
    "PumpFlowOccurrence",
    "PumpMode",
    "parse_digital_state",
    "parse_flow_value",
]

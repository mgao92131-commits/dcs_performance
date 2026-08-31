"""Persistent high-alarm assessment rule."""

from .detector import (
    AlarmOccurrence,
    DigitalStateParseError,
    PersistentHighAlarmDetector,
    detect,
    detect_alarm_occurrences,
    detect_persistent_high_alarms,
    parse_digital_state,
)

__all__ = [
    "AlarmOccurrence",
    "DigitalStateParseError",
    "PersistentHighAlarmDetector",
    "detect",
    "detect_alarm_occurrences",
    "detect_persistent_high_alarms",
    "parse_digital_state",
]

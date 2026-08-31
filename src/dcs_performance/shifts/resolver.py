"""Interfaces for resolving a timestamp to a concrete shift."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from .model import Shift


@runtime_checkable
class ShiftResolver(Protocol):
    """Return the shift that owns a timestamp."""

    def resolve(self, timestamp: datetime) -> Shift:
        ...


class StaticShiftResolver:
    """Small fixed-shift resolver for tests and early experiments.

    This is intentionally not a three-team/two-shift calendar.  It resolves
    only the single ``Shift`` supplied at construction time.
    """

    def __init__(self, shift: Shift) -> None:
        self.shift = shift

    def resolve(self, timestamp: datetime) -> Shift:
        if self.shift.start_time <= timestamp < self.shift.end_time:
            return self.shift
        raise ValueError(f"timestamp {timestamp!r} is outside the static shift")

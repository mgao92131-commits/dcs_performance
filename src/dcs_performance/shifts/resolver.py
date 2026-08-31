"""Interfaces and implementations for resolving timestamps to shifts."""

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from .calendar import ShiftCalendar
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


class CalendarShiftResolver:
    """Resolve timestamps by querying an existing :class:`ShiftCalendar`.

    The calendar remains the single owner of rotation and date semantics.
    This resolver only asks it for the tiny interval beginning at the target
    timestamp, so a returned shift is necessarily the shift containing that
    timestamp under the calendar's half-open rules.
    """

    def __init__(self, calendar: ShiftCalendar) -> None:
        self.calendar = calendar

    def resolve(self, timestamp: datetime) -> Shift:
        if not isinstance(timestamp, datetime):
            raise TypeError("timestamp must be a datetime value")
        try:
            query_end = timestamp + timedelta(microseconds=1)
        except OverflowError as exc:
            raise ValueError(f"timestamp {timestamp!r} is outside the calendar range") from exc

        shifts = self.calendar.get_shifts(timestamp, query_end)
        if len(shifts) == 1:
            return shifts[0]
        if not shifts:
            raise ValueError(f"timestamp {timestamp!r} does not belong to a shift")
        raise ValueError(
            f"timestamp {timestamp!r} belongs to multiple shifts: {shifts!r}"
        )

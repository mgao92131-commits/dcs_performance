"""Calendar implementation for the production 12-hour cyclic rotation."""

from __future__ import annotations

from datetime import datetime

from .cyclic_schedule import CyclicShiftScheduleConfig
from .model import Shift


class Cyclic12HourShiftCalendar:
    """Generate one continuous 12-hour shift for every cyclic time slot.

    The team at slot zero is ``config.rotation[0]``.  Python's floor
    division is intentional here: negative slot numbers correctly walk the
    rotation backwards before the reference timestamp.
    """

    def __init__(self, config: CyclicShiftScheduleConfig) -> None:
        if not isinstance(config, CyclicShiftScheduleConfig):
            raise TypeError("config must be a CyclicShiftScheduleConfig")
        self.config = config

    def get_shifts(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Shift]:
        """Return all shifts intersecting ``[start_time, end_time)``."""

        self._validate_datetime_range(start_time, end_time)
        duration = self.config.shift_duration
        first_slot = (start_time - self.config.reference_start) // duration
        # Including the slot containing end_time is harmless and makes the
        # exact-boundary case explicit: the half-open overlap check below
        # removes a shift whose start is exactly end_time.
        last_slot = (end_time - self.config.reference_start) // duration

        shifts: list[Shift] = []
        for slot in range(first_slot, last_slot + 1):
            shift = self._shift_for_slot(slot)
            if shift.start_time < end_time and shift.end_time > start_time:
                shifts.append(shift)
        return shifts

    def shift_for_timestamp(self, timestamp: datetime) -> Shift:
        """Return the unique shift containing ``timestamp``."""

        if not isinstance(timestamp, datetime):
            raise TypeError("timestamp must be a datetime value")
        if timestamp.tzinfo is not None:
            raise ValueError("timestamp must be timezone-naive local time")
        duration = self.config.shift_duration
        slot = (timestamp - self.config.reference_start) // duration
        return self._shift_for_slot(slot)

    def _shift_for_slot(self, slot: int) -> Shift:
        start_time = self.config.reference_start + slot * self.config.shift_duration
        end_time = start_time + self.config.shift_duration
        team_id = self.config.rotation[slot % len(self.config.rotation)]
        shift_type = "day" if slot % 2 == 0 else "night"
        return Shift(
            team_id=team_id,
            start_time=start_time,
            end_time=end_time,
            shift_type=shift_type,
        )

    @staticmethod
    def _validate_datetime_range(
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
            raise TypeError("start_time and end_time must be datetime values")
        if (start_time.tzinfo is None) != (end_time.tzinfo is None):
            raise ValueError(
                "start_time and end_time must both be timezone-naive or timezone-aware"
            )
        if start_time.tzinfo is not None:
            raise ValueError(
                "timezone-aware queries are not supported by the local-time schedule"
            )
        if end_time <= start_time:
            raise ValueError("end_time must be after start_time")

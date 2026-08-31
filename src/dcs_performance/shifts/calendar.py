"""Calendar interfaces and the standard three-team/two-shift implementation."""

from datetime import date, datetime, timedelta
from typing import Protocol, runtime_checkable

from .model import Shift
from .schedule import ShiftScheduleConfig


@runtime_checkable
class ShiftCalendar(Protocol):
    """Provide resolved shifts that overlap a requested time range."""

    def get_shifts(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Shift]:
        """Return scheduled shifts overlapping ``start_time``/``end_time``."""

        ...


class ThreeTeamTwoShiftCalendar:
    """Generate the two configured 12-hour shifts that start on each date.

    A night shift is owned by the date on which it starts.  Therefore the
    night shift returned for ``2026-01-01`` runs from 20:00 on January 1 to
    08:00 on January 2.
    """

    def __init__(self, config: ShiftScheduleConfig) -> None:
        self.config = config

    def get_shifts_for_date(self, day: date) -> list[Shift]:
        """Return that date's day shift and night shift in start-time order."""

        self._validate_date(day)
        cycle_index = self._cycle_index(day)
        day_team = self._team_for_state(cycle_index, "day")
        night_team = self._team_for_state(cycle_index, "night")

        try:
            next_day = day + timedelta(days=1)
        except OverflowError as exc:
            raise ValueError(f"cannot create a night shift after {day!r}") from exc

        return [
            Shift(
                team_id=day_team,
                shift_type="day",
                start_time=datetime.combine(day, self.config.day_start),
                end_time=datetime.combine(day, self.config.day_end),
            ),
            Shift(
                team_id=night_team,
                shift_type="night",
                start_time=datetime.combine(day, self.config.night_start),
                end_time=datetime.combine(next_day, self.config.night_end),
            ),
        ]

    def get_shifts(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Shift]:
        """Return all shifts intersecting the half-open query interval.

        The previous calendar date is included because its night shift may
        continue into the query's start date.  Results use the common
        half-open overlap rule::

            shift.start_time < end_time and shift.end_time > start_time
        """

        self._validate_datetime_range(start_time, end_time)

        first_day = start_time.date()
        if first_day > date.min:
            first_day -= timedelta(days=1)
        last_day = end_time.date()

        shifts: list[Shift] = []
        current_day = first_day
        while True:
            for shift in self.get_shifts_for_date(current_day):
                if shift.start_time < end_time and shift.end_time > start_time:
                    shifts.append(shift)
            if current_day == last_day:
                break
            current_day += timedelta(days=1)

        shifts.sort(key=lambda shift: shift.start_time)
        return shifts

    def _cycle_index(self, day: date) -> int:
        """Return the configured pattern position for ``day``."""

        return (day - self.config.reference_date).days % self.config.cycle_length

    def _team_for_state(self, cycle_index: int, state: str) -> str:
        for team_id, pattern in self.config.team_patterns.items():
            if pattern[cycle_index] == state:
                return team_id
        # ShiftScheduleConfig validation guarantees this is unreachable.
        raise RuntimeError(
            f"schedule has no team in state {state!r} at cycle position {cycle_index}"
        )

    @staticmethod
    def _validate_date(day: date) -> None:
        if not isinstance(day, date) or isinstance(day, datetime):
            raise TypeError("day must be a date, not a datetime")

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

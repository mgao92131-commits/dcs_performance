from datetime import datetime
from pathlib import Path

import pytest

from dcs_performance.shifts import (
    CalendarShiftResolver,
    Cyclic12HourShiftCalendar,
    CyclicShiftScheduleConfig,
    load_cyclic_schedule_config,
)


def make_calendar() -> Cyclic12HourShiftCalendar:
    config = load_cyclic_schedule_config(
        Path("src/dcs_performance/shifts/performance_schedule.json")
    )
    return Cyclic12HourShiftCalendar(config)


@pytest.mark.parametrize(
    ("timestamp", "team_id", "shift_type"),
    [
        (datetime(2026, 8, 31, 7, 59, 59), "A", "night"),
        (datetime(2026, 8, 31, 8, 0), "B", "day"),
        (datetime(2026, 8, 31, 19, 59, 59), "B", "day"),
        (datetime(2026, 8, 31, 20, 0), "C", "night"),
        (datetime(2026, 9, 1, 7, 59, 59), "C", "night"),
        (datetime(2026, 9, 1, 8, 0), "A", "day"),
        (datetime(2026, 9, 1, 20, 0), "B", "night"),
    ],
)
def test_cyclic_calendar_resolves_production_rotation(
    timestamp: datetime,
    team_id: str,
    shift_type: str,
):
    shift = make_calendar().shift_for_timestamp(timestamp)

    assert (shift.team_id, shift.shift_type) == (team_id, shift_type)


def test_cyclic_calendar_works_with_calendar_shift_resolver():
    resolver = CalendarShiftResolver(make_calendar())

    shift = resolver.resolve(datetime(2026, 9, 1, 8, 0))

    assert shift.team_id == "A"
    assert shift.start_time == datetime(2026, 9, 1, 8, 0)
    assert shift.end_time == datetime(2026, 9, 1, 20, 0)


def test_cyclic_get_shifts_handles_cross_day_and_half_open_boundaries():
    shifts = make_calendar().get_shifts(
        datetime(2026, 8, 31, 19, 0),
        datetime(2026, 9, 2, 8, 0),
    )

    assert [(shift.team_id, shift.start_time) for shift in shifts] == [
        ("B", datetime(2026, 8, 31, 8, 0)),
        ("C", datetime(2026, 8, 31, 20, 0)),
        ("A", datetime(2026, 9, 1, 8, 0)),
        ("B", datetime(2026, 9, 1, 20, 0)),
    ]


def test_cyclic_get_shifts_supports_ranges_before_reference():
    calendar = make_calendar()
    shifts = calendar.get_shifts(
        datetime(2026, 8, 30, 7, 0),
        datetime(2026, 8, 30, 21, 0),
    )

    assert [(shift.team_id, shift.shift_type) for shift in shifts] == [
        ("B", "night"),
        ("C", "day"),
        ("A", "night"),
    ]


def test_cyclic_schedule_config_normalizes_direct_values():
    config = CyclicShiftScheduleConfig(
        reference_start=datetime(2026, 8, 31, 8, 0),
        shift_hours=12,
        rotation=["B", "C", "A"],
        team_names={"A": "甲班", "B": "乙班", "C": "丙班"},
    )

    assert config.rotation == ("B", "C", "A")
    assert config.display_name("B") == "乙班"

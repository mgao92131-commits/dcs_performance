import json
from datetime import date, datetime
from pathlib import Path

import pytest

from dcs_performance.shifts import (
    CalendarShiftResolver,
    ScheduleConfigError,
    ThreeTeamTwoShiftCalendar,
    load_schedule_config,
)


PROJECT_ROOT = Path(__file__).parents[1]
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "src" / "dcs_performance" / "shifts" / "schedule.example.json"


def make_calendar() -> ThreeTeamTwoShiftCalendar:
    return ThreeTeamTwoShiftCalendar(load_schedule_config(EXAMPLE_CONFIG_PATH))


def shift_summary(shift):
    return (shift.team_id, shift.shift_type, shift.start_time, shift.end_time)


def test_example_schedule_loads_with_a_six_day_cycle():
    config = load_schedule_config(EXAMPLE_CONFIG_PATH)

    assert config.reference_date == date(2026, 1, 1)
    assert config.cycle_length == 6
    assert config.team_patterns == {
        "A": ("day", "day", "night", "night", "off", "off"),
        "B": ("night", "night", "off", "off", "day", "day"),
        "C": ("off", "off", "day", "day", "night", "night"),
    }


def test_rotation_cycle_and_repeat_from_reference_date():
    calendar = make_calendar()
    expected = {
        date(2026, 1, 1): [("A", "day"), ("B", "night")],
        date(2026, 1, 2): [("A", "day"), ("B", "night")],
        date(2026, 1, 3): [("C", "day"), ("A", "night")],
        date(2026, 1, 4): [("C", "day"), ("A", "night")],
        date(2026, 1, 5): [("B", "day"), ("C", "night")],
        date(2026, 1, 6): [("B", "day"), ("C", "night")],
        date(2026, 1, 7): [("A", "day"), ("B", "night")],
    }

    for day, expected_shifts in expected.items():
        assert [(shift.team_id, shift.shift_type) for shift in calendar.get_shifts_for_date(day)] == expected_shifts


def test_dates_before_reference_date_use_python_negative_modulo_correctly():
    calendar = make_calendar()

    for day in (date(2025, 12, 30), date(2025, 12, 31)):
        shifts = calendar.get_shifts_for_date(day)
        assert [(shift.team_id, shift.shift_type) for shift in shifts] == [
            ("B", "day"),
            ("C", "night"),
        ]


def test_get_shifts_for_date_generates_day_and_night_only():
    calendar = make_calendar()

    shifts = calendar.get_shifts_for_date(date(2026, 1, 1))

    assert [shift_summary(shift) for shift in shifts] == [
        (
            "A",
            "day",
            datetime(2026, 1, 1, 8, 0),
            datetime(2026, 1, 1, 20, 0),
        ),
        (
            "B",
            "night",
            datetime(2026, 1, 1, 20, 0),
            datetime(2026, 1, 2, 8, 0),
        ),
    ]


def test_resolver_keeps_after_midnight_timestamp_in_previous_date_night_shift():
    resolver = CalendarShiftResolver(make_calendar())

    shift_at_start = resolver.resolve(datetime(2026, 1, 1, 20, 0))
    shift_after_midnight = resolver.resolve(datetime(2026, 1, 2, 3, 0))

    assert shift_at_start == shift_after_midnight
    assert shift_summary(shift_after_midnight) == (
        "B",
        "night",
        datetime(2026, 1, 1, 20, 0),
        datetime(2026, 1, 2, 8, 0),
    )


def test_resolver_uses_half_open_boundaries():
    resolver = CalendarShiftResolver(make_calendar())

    assert shift_summary(resolver.resolve(datetime(2026, 1, 1, 7, 59, 59))) == (
        "C",
        "night",
        datetime(2025, 12, 31, 20, 0),
        datetime(2026, 1, 1, 8, 0),
    )
    assert shift_summary(resolver.resolve(datetime(2026, 1, 1, 8, 0))) == (
        "A",
        "day",
        datetime(2026, 1, 1, 8, 0),
        datetime(2026, 1, 1, 20, 0),
    )
    assert shift_summary(resolver.resolve(datetime(2026, 1, 1, 19, 59, 59))) == (
        "A",
        "day",
        datetime(2026, 1, 1, 8, 0),
        datetime(2026, 1, 1, 20, 0),
    )
    assert shift_summary(resolver.resolve(datetime(2026, 1, 1, 20, 0))) == (
        "B",
        "night",
        datetime(2026, 1, 1, 20, 0),
        datetime(2026, 1, 2, 8, 0),
    )
    assert shift_summary(resolver.resolve(datetime(2026, 1, 2, 7, 59, 59))) == (
        "B",
        "night",
        datetime(2026, 1, 1, 20, 0),
        datetime(2026, 1, 2, 8, 0),
    )
    assert shift_summary(resolver.resolve(datetime(2026, 1, 2, 8, 0))) == (
        "A",
        "day",
        datetime(2026, 1, 2, 8, 0),
        datetime(2026, 1, 2, 20, 0),
    )


@pytest.mark.parametrize(
    ("start_time", "end_time", "expected"),
    [
        (
            datetime(2026, 1, 1, 9, 0),
            datetime(2026, 1, 1, 10, 0),
            [("A", "day", datetime(2026, 1, 1, 8, 0))],
        ),
        (
            datetime(2026, 1, 1, 21, 0),
            datetime(2026, 1, 1, 22, 0),
            [("B", "night", datetime(2026, 1, 1, 20, 0))],
        ),
        (
            datetime(2026, 1, 1, 19, 0),
            datetime(2026, 1, 1, 21, 0),
            [
                ("A", "day", datetime(2026, 1, 1, 8, 0)),
                ("B", "night", datetime(2026, 1, 1, 20, 0)),
            ],
        ),
        (
            datetime(2026, 1, 2, 6, 0),
            datetime(2026, 1, 2, 10, 0),
            [
                ("B", "night", datetime(2026, 1, 1, 20, 0)),
                ("A", "day", datetime(2026, 1, 2, 8, 0)),
            ],
        ),
        (
            datetime(2026, 1, 1, 0, 0),
            datetime(2026, 1, 7, 0, 0),
            [
                ("C", "night", datetime(2025, 12, 31, 20, 0)),
                ("A", "day", datetime(2026, 1, 1, 8, 0)),
                ("B", "night", datetime(2026, 1, 1, 20, 0)),
                ("A", "day", datetime(2026, 1, 2, 8, 0)),
                ("B", "night", datetime(2026, 1, 2, 20, 0)),
                ("C", "day", datetime(2026, 1, 3, 8, 0)),
                ("A", "night", datetime(2026, 1, 3, 20, 0)),
                ("C", "day", datetime(2026, 1, 4, 8, 0)),
                ("A", "night", datetime(2026, 1, 4, 20, 0)),
                ("B", "day", datetime(2026, 1, 5, 8, 0)),
                ("C", "night", datetime(2026, 1, 5, 20, 0)),
                ("B", "day", datetime(2026, 1, 6, 8, 0)),
                ("C", "night", datetime(2026, 1, 6, 20, 0)),
            ],
        ),
    ],
)
def test_get_shifts_returns_only_intersecting_shifts_in_start_order(
    start_time, end_time, expected
):
    calendar = make_calendar()

    actual = calendar.get_shifts(start_time, end_time)

    assert [(shift.team_id, shift.shift_type, shift.start_time) for shift in actual] == expected
    assert actual == sorted(actual, key=lambda shift: shift.start_time)
    assert len(actual) == len({shift_summary(shift) for shift in actual})


def test_get_shifts_excludes_shifts_touching_query_boundary_only():
    calendar = make_calendar()

    assert [shift.shift_type for shift in calendar.get_shifts(
        datetime(2026, 1, 1, 8, 0), datetime(2026, 1, 1, 20, 0)
    )] == ["day"]
    assert [shift.shift_type for shift in calendar.get_shifts(
        datetime(2026, 1, 1, 20, 0), datetime(2026, 1, 2, 8, 0)
    )] == ["night"]


def test_invalid_query_range_is_rejected():
    calendar = make_calendar()

    with pytest.raises(ValueError, match="end_time must be after start_time"):
        calendar.get_shifts(datetime(2026, 1, 1, 10, 0), datetime(2026, 1, 1, 10, 0))


def base_config() -> dict:
    return {
        "reference_date": "2026-01-01",
        "day_shift": {"start": "08:00", "end": "20:00"},
        "night_shift": {"start": "20:00", "end": "08:00"},
        "teams": {
            "A": ["day", "day", "night", "night", "off", "off"],
            "B": ["night", "night", "off", "off", "day", "day"],
            "C": ["off", "off", "day", "day", "night", "night"],
        },
    }


def write_config(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("description", "mutate", "message"),
    [
        (
            "two teams",
            lambda config: config["teams"].pop("C"),
            "teams",
        ),
        (
            "four teams",
            lambda config: config["teams"].update({"D": ["off"] * 6}),
            "teams",
        ),
        (
            "different pattern lengths",
            lambda config: config["teams"]["B"].pop(),
            "same cycle length",
        ),
        (
            "empty pattern",
            lambda config: config["teams"].update({"A": []}),
            "teams.A pattern must not be empty",
        ),
        (
            "unknown state",
            lambda config: config["teams"]["A"].__setitem__(0, "holiday"),
            "holiday",
        ),
        (
            "two day states",
            lambda config: config["teams"].update({
                "A": ["day", "day", "night", "night", "off", "off"],
                "B": ["day", "night", "off", "off", "day", "day"],
                "C": ["off", "off", "day", "day", "night", "night"],
            }),
            "cycle position 0",
        ),
        (
            "no night state",
            lambda config: config["teams"].update({
                "A": ["day", "day", "night", "night", "off", "off"],
                "B": ["off", "night", "off", "off", "day", "day"],
                "C": ["off", "off", "day", "day", "night", "night"],
            }),
            "cycle position 0",
        ),
        (
            "two off states",
            lambda config: config["teams"].update({
                "A": ["off", "day", "night", "night", "off", "off"],
                "B": ["off", "night", "off", "off", "day", "day"],
                "C": ["day", "off", "day", "day", "night", "night"],
            }),
            "cycle position 0",
        ),
        (
            "non twelve-hour shift",
            lambda config: config["day_shift"].update({"end": "19:00"}),
            "day_shift",
        ),
        (
            "discontinuous twelve-hour shifts",
            lambda config: config.update({
                "night_shift": {"start": "21:00", "end": "09:00"},
            }),
            "continuous 24-hour coverage",
        ),
    ],
)
def test_invalid_schedule_config_is_reported(
    tmp_path: Path, description: str, mutate, message: str
):
    config = base_config()
    mutate(config)

    with pytest.raises(ScheduleConfigError, match=message):
        load_schedule_config(write_config(tmp_path, config))


def test_invalid_json_and_missing_required_fields_are_reported(tmp_path: Path):
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")
    with pytest.raises(ScheduleConfigError, match="invalid JSON"):
        load_schedule_config(invalid_json)

    missing_field = base_config()
    missing_field.pop("reference_date")
    with pytest.raises(ScheduleConfigError, match="reference_date"):
        load_schedule_config(write_config(tmp_path, missing_field))


def test_calendar_shift_resolver_works_with_single_shift_assigner():
    from dcs_performance.core.event import AssessmentEvent
    from dcs_performance.shifts.assignment import SingleShiftAssigner

    assigner = SingleShiftAssigner(CalendarShiftResolver(make_calendar()))
    event = AssessmentEvent(
        start_time=datetime(2026, 1, 1, 9, 0),
        end_time=datetime(2026, 1, 1, 10, 0),
    )

    assigned = assigner.assign(event)

    assert len(assigned) == 1
    assert assigned[0].shift.team_id == "A"

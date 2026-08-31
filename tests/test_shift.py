from datetime import datetime

from dcs_performance.shifts.model import Shift


def test_shift_represents_team_day_shift_and_bounds():
    shift = Shift(
        team_id="A",
        shift_type="day",
        start_time=datetime(2026, 8, 31, 8, 0),
        end_time=datetime(2026, 8, 31, 20, 0),
    )

    assert shift.team_id == "A"
    assert shift.shift_type == "day"
    assert shift.start_time == datetime(2026, 8, 31, 8, 0)
    assert shift.end_time == datetime(2026, 8, 31, 20, 0)

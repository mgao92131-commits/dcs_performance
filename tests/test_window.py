from datetime import datetime

from dcs_performance.core.window import TimeRange, build_assessment_window
from dcs_performance.shifts.model import Shift


def test_build_assessment_window_applies_rule_offsets():
    shift = Shift(
        team_id="A",
        shift_type="day",
        start_time=datetime(2026, 8, 31, 8, 0),
        end_time=datetime(2026, 8, 31, 20, 0),
    )
    config = {
        "assessment_window": {
            "start_offset_minutes": 20,
            "end_offset_minutes": 0,
        }
    }

    window = build_assessment_window(shift, config)

    assert isinstance(window, TimeRange)
    assert window.start_time == datetime(2026, 8, 31, 8, 20)
    assert window.end_time == datetime(2026, 8, 31, 20, 0)

from datetime import datetime

import pytest

from dcs_performance.core.window import (
    TimeRange,
    build_assessment_window,
    build_point_assessment_window,
)
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


def test_build_assessment_window_supports_ten_minute_early_handover():
    shift = Shift(
        team_id="B",
        shift_type="day",
        start_time=datetime(2026, 8, 31, 8, 0),
        end_time=datetime(2026, 8, 31, 20, 0),
    )

    window = build_assessment_window(
        shift,
        {
            "assessment_window": {
                "start_offset_minutes": -10,
                "end_offset_minutes": 10,
            }
        },
    )

    assert window.start_time == datetime(2026, 8, 31, 7, 50)
    assert window.end_time == datetime(2026, 8, 31, 19, 50)


def test_build_assessment_window_supports_early_handover_for_night_shift():
    shift = Shift(
        team_id="C",
        shift_type="night",
        start_time=datetime(2026, 8, 31, 20, 0),
        end_time=datetime(2026, 9, 1, 8, 0),
    )

    window = build_assessment_window(
        shift,
        {
            "assessment_window": {
                "start_offset_minutes": -10,
                "end_offset_minutes": 10,
            }
        },
    )

    assert window.start_time == datetime(2026, 8, 31, 19, 50)
    assert window.end_time == datetime(2026, 9, 1, 7, 50)


def test_build_point_assessment_window_overrides_each_offset_independently():
    shift = Shift(
        team_id="A",
        shift_type="day",
        start_time=datetime(2026, 8, 31, 8, 0),
        end_time=datetime(2026, 8, 31, 20, 0),
    )
    rule_config = {
        "assessment_window": {
            "start_offset_minutes": -10,
            "end_offset_minutes": 10,
        }
    }

    window = build_point_assessment_window(
        shift,
        rule_config,
        {"id": "POINT-1", "assessment_window": {"start_offset_minutes": 20}},
    )

    assert window.start_time == datetime(2026, 8, 31, 8, 20)
    # The point only overrides the start, so the rule end offset is inherited.
    assert window.end_time == datetime(2026, 8, 31, 19, 50)


def test_build_point_assessment_window_inherits_rule_window_when_not_configured():
    shift = Shift(
        team_id="A",
        shift_type="day",
        start_time=datetime(2026, 8, 31, 8, 0),
        end_time=datetime(2026, 8, 31, 20, 0),
    )
    rule_config = {
        "assessment_window": {
            "start_offset_minutes": -10,
            "end_offset_minutes": 10,
        }
    }

    window = build_point_assessment_window(shift, rule_config, {"id": "POINT-1"})

    assert window.start_time == datetime(2026, 8, 31, 7, 50)
    assert window.end_time == datetime(2026, 8, 31, 19, 50)


def test_build_point_assessment_window_rejects_invalid_point_window():
    shift = Shift(
        team_id="A",
        shift_type="day",
        start_time=datetime(2026, 8, 31, 8, 0),
        end_time=datetime(2026, 8, 31, 20, 0),
    )

    with pytest.raises(TypeError, match="offsets must be integers"):
        build_point_assessment_window(
            shift,
            {},
            {"id": "POINT-1", "assessment_window": {"start_offset_minutes": 1.5}},
        )

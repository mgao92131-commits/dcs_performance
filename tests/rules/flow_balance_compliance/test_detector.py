from datetime import datetime, timedelta

from dcs_performance.data.models import HistorySample
from dcs_performance.rules.flow_balance_compliance.config import PointConfig, SmoothingConfig
from dcs_performance.rules.flow_balance_compliance.detector import (
    FLOW_HIGH,
    detect_flow_balance_events,
)


START = datetime(2026, 9, 1, 8, 0)
LOGIC = "LOGIC27/YK-TLFH/OUT1.CV"
SY116 = "SY-116/AI1/PV.CV"
SY216 = "SY-216/AI1/PV.CV"


def point(**overrides):
    values = {
        "id": "SLURRY_FLOW_BALANCE",
        "logic_tag": LOGIC,
        "sy_tags": (SY116, SY216),
        "enabled": True,
        "smoothing": SmoothingConfig(enabled=True, window_seconds=60, min_samples=1),
        "low_limit": -15.0,
        "high_limit": 15.0,
        "min_duration_seconds": 300,
        "merge_gap_seconds": 20,
        "max_gap_seconds": 60,
    }
    values.update(overrides)
    return PointConfig(**values)


def sample(timestamp, value, sequence_no=1, **quality):
    return HistorySample(
        timestamp=timestamp,
        value=str(value),
        data_type="Analog",
        delta_v_status=quality.get("delta_v_status", "Good"),
        archive_status=quality.get("archive_status", "HistoryDataIsValid"),
        sequence_no=sequence_no,
        is_history_hole=quality.get("is_history_hole", False),
        is_cr_hole=quality.get("is_cr_hole", False),
        is_manually_deleted=quality.get("is_manually_deleted", False),
        is_manually_inserted=quality.get("is_manually_inserted", False),
    )


def histories(logic_values):
    times = [START + timedelta(minutes=index) for index in range(len(logic_values))]
    return {
        LOGIC: [sample(time, value) for time, value in zip(times, logic_values)],
        SY116: [sample(time, 60) for time in times],
        SY216: [sample(time, 40) for time in times],
    }


def test_flow_high_event_uses_dcs_value_without_extra_correction():
    # 116 - (60 + 40) = 16. Dividing LOGIC27 by 1.0099 would remove this event.
    values = [116, 116, 116, 116, 116, 116, 100]

    events = detect_flow_balance_events(histories(values), point())

    assert len(events) == 1
    assert events[0].direction == FLOW_HIGH
    assert events[0].start_time == START
    assert events[0].end_time == START + timedelta(minutes=6)
    assert events[0].peak_difference == 16.0


def test_flow_boundary_is_not_an_exceedance():
    values = [115, 115, 115, 115, 115, 115, 100]

    assert detect_flow_balance_events(histories(values), point()) == []


def test_flow_gap_does_not_count_as_continuous_event():
    values = [100, 116, 116, 116, 116, 116]
    data = histories(values)
    data[SY216] = [sample(START, 40), sample(START + timedelta(minutes=1), 40), sample(START + timedelta(minutes=6), 40)]

    assert detect_flow_balance_events(data, point()) == []


def test_flow_quality_hole_with_non_numeric_value_breaks_the_event():
    data = histories([100, 116, 116, 116, 116, 116])
    data[SY116][3] = sample(
        START + timedelta(minutes=3),
        "not-a-number",
        is_cr_hole=True,
    )

    assert detect_flow_balance_events(data, point()) == []


def test_flow_gap_closes_at_pending_recovery_not_latest_normal_sample():
    times = [START + timedelta(seconds=60 * index) for index in range(7)]
    data = {
        LOGIC: [
            sample(times[index], 116 if index < 4 else 100)
            for index in range(7)
        ],
        SY116: [sample(times[index], 60) for index in range(7)],
        SY216: [sample(times[index], 40) for index in range(6)],
    }

    # The violation lasts four minutes, recovers at t=4, remains normal at
    # t=5, then loses one input at t=6.  Using t=5 as the close would falsely
    # qualify the exact five-minute threshold.
    assert detect_flow_balance_events(data, point()) == []

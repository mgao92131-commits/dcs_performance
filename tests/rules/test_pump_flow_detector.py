from datetime import datetime, timedelta

import pytest

from dcs_performance.rules.pump_flow_compliance.detector import (
    LOW_FLOW,
    SWITCH_TIMEOUT,
    FlowValueParseError,
    PumpFlowDetector,
    parse_digital_state,
    parse_flow_value,
)

from tests.fakes import make_history_sample


BASE = datetime(2026, 8, 31, 10, 0)
A = "A"
B = "B"
FLOW = "FLOW"


def sample(offset_seconds: float, value: str, sequence_no: int = 1):
    return make_history_sample(
        BASE + timedelta(seconds=offset_seconds),
        value,
        sequence_no=sequence_no,
    )


def detector(
    *,
    normal_min_flow: float = 125,
    switching_min_flow: float = 100,
    max_switch_duration_seconds: float = 600,
) -> PumpFlowDetector:
    return PumpFlowDetector(
        "117P01",
        A,
        B,
        FLOW,
        "1",
        normal_min_flow,
        switching_min_flow,
        max_switch_duration_seconds,
    )


def detect(histories, *, start=0, end=1800, observation_end=None):
    return detector().detect(
        histories,
        window_start=BASE + timedelta(seconds=start),
        window_end=BASE + timedelta(seconds=end),
        analysis_start=BASE,
        observation_end=BASE
        + timedelta(seconds=observation_end if observation_end is not None else end),
    )


def stable_initial(flow: str = "125"):
    return {
        A: [sample(-1, "1")],
        B: [sample(-1, "0")],
        FLOW: [sample(-1, flow)],
    }


def event_types(events):
    return [event.event_type for event in events]


def test_normal_flow_uses_strict_117p01_boundary():
    low = detect({**stable_initial(), FLOW: [sample(0, "124.999")]}, end=60)
    good = detect({**stable_initial(), FLOW: [sample(0, "125")]}, end=60)

    assert event_types(low) == [LOW_FLOW]
    assert low[0].data["minimum_flow"] == pytest.approx(124.999)
    assert good == []


def test_switching_flow_uses_switching_threshold():
    histories = stable_initial()
    histories[A].append(sample(0, "1"))
    histories[B].append(sample(0, "1"))
    histories[FLOW].append(sample(0, "100"))

    assert detect(histories, end=60) == []

    histories[FLOW] = [sample(-1, "125"), sample(0, "99.999")]
    events = detect(histories, end=60)
    assert event_types(events) == [LOW_FLOW]


def test_switching_can_be_entered_and_left_after_starting_or_stopping_first():
    histories = stable_initial()
    histories[A].extend([sample(0, "1"), sample(120, "0")])
    histories[B].extend([sample(0, "1"), sample(120, "1")])
    histories[FLOW] = [sample(-1, "125")]

    events = detect(histories, end=300)

    assert events == []

    histories = stable_initial()
    histories[A].extend([sample(0, "0"), sample(120, "0")])
    histories[B].extend([sample(0, "0"), sample(120, "1")])
    assert detect(histories, end=300) == []


def test_direct_switch_at_same_timestamp_is_not_a_switch_interval():
    histories = stable_initial()
    histories[A].append(sample(0, "0"))
    histories[B].append(sample(0, "1"))
    histories[FLOW].append(sample(0, "115"))

    events = detect(histories, end=60)

    # The final A/B state is NORMAL_B, so 115 is below the normal threshold.
    assert event_types(events) == [LOW_FLOW]
    assert events[0].start_time == BASE


def test_same_timestamp_same_tag_uses_highest_sequence_before_mode_migration():
    histories = stable_initial()
    histories[A].extend([
        sample(0, "0", sequence_no=1),
        sample(0, "1", sequence_no=2),
    ])
    histories[B].append(sample(0, "0"))
    histories[FLOW].append(sample(0, "115"))

    # Final state is NORMAL_A, not a transient 0/1 or 0/0 state.
    assert event_types(detect(histories, end=60)) == [LOW_FLOW]


def test_low_flow_remains_one_event_when_mode_threshold_changes_without_recovery():
    histories = stable_initial()
    histories[FLOW].append(sample(30, "95"))
    histories[A].append(sample(60, "1"))
    histories[B].append(sample(60, "1"))
    histories[A].append(sample(300, "0"))
    histories[B].append(sample(300, "1"))
    histories[FLOW].append(sample(300, "95"))
    histories[FLOW].append(sample(600, "125"))

    events = detect(histories, end=900)

    assert event_types(events) == [LOW_FLOW]
    assert events[0].start_time == BASE + timedelta(seconds=30)
    assert events[0].end_time == BASE + timedelta(seconds=600)
    assert events[0].data["modes_seen"] == [
        "NORMAL_A",
        "SWITCHING",
        "NORMAL_B",
    ]


def test_low_flow_ends_when_switching_threshold_makes_flow_compliant():
    histories = stable_initial()
    histories[FLOW].append(sample(30, "115"))
    histories[A].append(sample(60, "1"))
    histories[B].append(sample(60, "1"))
    histories[A].append(sample(300, "0"))
    histories[B].append(sample(300, "1"))
    histories[FLOW].append(sample(300, "125"))

    events = detect(histories, end=900)

    assert event_types(events) == [LOW_FLOW]
    assert events[0].start_time == BASE + timedelta(seconds=30)
    assert events[0].end_time == BASE + timedelta(seconds=60)


@pytest.mark.parametrize("duration", [599.999, 600.000])
def test_switch_timeout_does_not_trigger_at_or_below_boundary(duration):
    histories = stable_initial()
    histories[A].append(sample(0, "1"))
    histories[B].append(sample(0, "1"))
    histories[A].append(sample(duration, "0"))
    histories[B].append(sample(duration, "1"))

    assert detect(histories, end=900) == []


def test_switch_timeout_triggers_strictly_after_boundary():
    duration = 600.001
    histories = stable_initial()
    histories[A].append(sample(0, "1"))
    histories[B].append(sample(0, "1"))
    histories[A].append(sample(duration, "0"))
    histories[B].append(sample(duration, "1"))

    events = detect(histories, end=900)

    assert event_types(events) == [SWITCH_TIMEOUT]
    assert events[0].start_time == BASE + timedelta(seconds=600)
    assert events[0].data["switch_duration_seconds"] == pytest.approx(duration)
    assert events[0].data["overtime_seconds"] == pytest.approx(0.001)


def test_switch_timeout_and_low_flow_are_independent_events():
    histories = stable_initial()
    histories[A].append(sample(0, "1"))
    histories[B].append(sample(0, "1"))
    histories[FLOW].append(sample(0, "95"))
    histories[A].append(sample(720, "0"))
    histories[B].append(sample(720, "1"))

    events = detect(histories, end=900)

    assert event_types(events) == [LOW_FLOW, SWITCH_TIMEOUT]
    assert events[0].data["event_key"].startswith(
        "pump_flow_compliance:117P01:low_flow:"
    )
    assert events[1].data["event_key"].startswith(
        "pump_flow_compliance:117P01:switch_timeout:"
    )


def test_unknown_initial_pump_state_does_not_create_a_fake_switch_or_low_flow():
    histories = {
        A: [],
        B: [sample(0, "0")],
        FLOW: [sample(0, "0")],
    }

    assert detect(histories, end=60) == []


def test_low_flow_recovery_at_analysis_start_does_not_create_zero_length_event():
    histories = {
        A: [sample(-2, "1")],
        B: [sample(-2, "0")],
        FLOW: [sample(-2, "95"), sample(0, "125")],
    }

    assert detect(histories, end=60) == []


def test_invalid_flow_is_rejected_and_not_treated_as_zero():
    with pytest.raises(FlowValueParseError):
        parse_flow_value("NaN")
    with pytest.raises(FlowValueParseError):
        detect({**stable_initial(), FLOW: [sample(0, "BAD")]}, end=60)


def test_digital_parser_accepts_only_zero_or_one():
    assert parse_digital_state("1.0") == 1
    assert parse_digital_state("0") == 0
    assert parse_digital_state("mtr2-pv:1:Running") == 1
    assert parse_digital_state("mtr2-pv:0:Stopped") == 0
    with pytest.raises(ValueError):
        parse_digital_state("2")
    with pytest.raises(ValueError):
        parse_digital_state("mtr2-pv:2:Unknown")

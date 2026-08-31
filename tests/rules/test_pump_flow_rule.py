from datetime import datetime, timedelta

import pytest

from dcs_performance.rules.pump_flow_compliance.rule import Rule

from tests.fakes import FakeDataClient, make_history_sample


START = datetime(2026, 8, 31, 10, 0)
END = datetime(2026, 8, 31, 11, 0)


def point(
    point_id="117P01",
    *,
    a="A",
    b="B",
    flow="FLOW",
    normal=125,
    switching=100,
    maximum=600,
):
    return {
        "id": point_id,
        "pump_a_tag": a,
        "pump_b_tag": b,
        "flow_tag": flow,
        "running_value": "1",
        "normal_min_flow": normal,
        "switching_min_flow": switching,
        "max_switch_duration_seconds": maximum,
    }


def config(points=None, *, scoring=None):
    return {
        "id": "pump_flow_compliance",
        "name": "泵组流量考核",
        "enabled": True,
        "assessment_window": {
            "start_offset_minutes": -10,
            "end_offset_minutes": 10,
        },
        "parameters": {"points": points or [point()]},
        "scoring": scoring or {},
    }


def hs(tag_time, value, sequence_no=1):
    return make_history_sample(tag_time, value, sequence_no=sequence_no)


def test_rule_emits_low_flow_and_switch_timeout_for_one_point():
    client = FakeDataClient(
        {
            "A": [
                hs(START - timedelta(minutes=20), "1"),
                hs(START, "1"),
                hs(START + timedelta(minutes=12), "0"),
            ],
            "B": [
                hs(START - timedelta(minutes=20), "0"),
                hs(START, "1"),
                hs(START + timedelta(minutes=12), "1"),
            ],
            "FLOW": [
                hs(START - timedelta(minutes=20), "125"),
                hs(START, "95"),
            ],
        }
    )

    events = Rule(client, config()).evaluate(START, END)

    assert [event.data["event_type"] for event in events] == [
        "low_flow",
        "switch_timeout",
    ]
    low_flow, timeout = events
    assert low_flow.start_time == START
    assert low_flow.data["minimum_flow"] == 95.0
    assert low_flow.data["is_open"] is True
    assert timeout.start_time == START + timedelta(minutes=10)
    assert timeout.data["switch_start"] == START
    assert timeout.data["switch_end"] == START + timedelta(minutes=12)
    assert timeout.data["switch_duration_seconds"] == 720.0
    assert timeout.data["overtime_seconds"] == 120.0
    assert timeout.data["event_key"] == (
        "pump_flow_compliance:117P01:switch_timeout:2026-08-31T10:10:00"
    )


def test_rule_reads_all_nine_tags_as_independent_point_configuration():
    points = [
        point("117P01", a="A1", b="B1", flow="F1", normal=125),
        point("115P05", a="A2", b="B2", flow="F2", normal=110),
        point("115P03", a="A3", b="B3", flow="F3", normal=110),
    ]
    client = FakeDataClient(
        {
            "A1": [hs(START - timedelta(minutes=1), "1")],
            "B1": [hs(START - timedelta(minutes=1), "0")],
            "F1": [hs(START - timedelta(minutes=1), "125"), hs(START, "124.999")],
            "A2": [hs(START - timedelta(minutes=1), "1")],
            "B2": [hs(START - timedelta(minutes=1), "0")],
            "F2": [hs(START - timedelta(minutes=1), "110"), hs(START, "109.999")],
            "A3": [hs(START - timedelta(minutes=1), "1")],
            "B3": [hs(START - timedelta(minutes=1), "0")],
            "F3": [hs(START - timedelta(minutes=1), "110"), hs(START, "109.999")],
        }
    )

    events = Rule(client, config(points)).evaluate(START, END)

    assert [event.data["point_id"] for event in events] == [
        "115P03",
        "115P05",
        "117P01",
    ]
    assert {event.data["flow_tag"] for event in events} == {"F1", "F2", "F3"}


def test_timeout_before_window_is_not_repeated_in_next_window():
    client = FakeDataClient(
        {
            "A": [
                hs(START - timedelta(minutes=40), "1"),
                hs(START - timedelta(minutes=30), "1"),
            ],
            "B": [
                hs(START - timedelta(minutes=40), "0"),
                hs(START - timedelta(minutes=30), "1"),
            ],
            "FLOW": [hs(START - timedelta(minutes=40), "125")],
        }
    )

    events = Rule(client, config()).evaluate(START, END)

    assert events == []


def test_timeout_start_at_window_boundary_belongs_to_that_window():
    client = FakeDataClient(
        {
            "A": [
                hs(START - timedelta(minutes=20), "1"),
                hs(START - timedelta(minutes=10), "1"),
                hs(START + timedelta(minutes=5), "0"),
            ],
            "B": [
                hs(START - timedelta(minutes=20), "0"),
                hs(START - timedelta(minutes=10), "1"),
                hs(START + timedelta(minutes=5), "1"),
            ],
            "FLOW": [hs(START - timedelta(minutes=20), "125")],
        }
    )

    events = Rule(client, config()).evaluate(START, END)

    assert [event.data["event_type"] for event in events] == ["switch_timeout"]
    assert events[0].start_time == START
    assert events[0].data["switch_start"] == START - timedelta(minutes=10)


def test_timeout_that_started_before_window_is_not_repeated():
    client = FakeDataClient(
        {
            "A": [
                hs(START - timedelta(minutes=40), "1"),
                hs(START - timedelta(minutes=30), "1"),
                hs(START + timedelta(minutes=5), "0"),
            ],
            "B": [
                hs(START - timedelta(minutes=40), "0"),
                hs(START - timedelta(minutes=30), "1"),
                hs(START + timedelta(minutes=5), "1"),
            ],
            "FLOW": [hs(START - timedelta(minutes=40), "125")],
        }
    )

    assert Rule(client, config()).evaluate(START, END) == []


@pytest.mark.parametrize(
    "bad_point",
    [
        {"id": "117P01"},
        {**point(), "pump_a_tag": "B"},
        {**point(), "running_value": "2"},
        {**point(), "normal_min_flow": 0},
        {**point(), "switching_min_flow": 0},
        {**point(), "max_switch_duration_seconds": 0},
    ],
)
def test_rule_rejects_incomplete_or_invalid_point_configuration(bad_point):
    with pytest.raises(ValueError):
        Rule(FakeDataClient(), config([bad_point]))


def test_rule_does_not_create_low_flow_when_a_pump_state_is_unknown():
    client = FakeDataClient(
        {
            "A": [hs(START, "1")],
            "B": [],
            "FLOW": [hs(START, "0")],
        }
    )

    assert Rule(client, config()).evaluate(START, END) == []

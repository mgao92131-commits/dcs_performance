from datetime import datetime, timedelta

import pytest

from dcs_performance.engine.loader import RuleLoader, RuleLoadError
from dcs_performance.rules.analog_limit_exceedance.rule import Rule

from tests.fakes import FakeDataClient, make_history_sample


START = datetime(2026, 9, 1, 8, 0)
END = datetime(2026, 9, 1, 20, 0)


def point(
    point_id="P",
    tag="TAG-P",
    *,
    enabled=True,
    low_enabled=True,
    high_enabled=True,
    low_limit=80,
    high_limit=120,
    low_min=300,
    high_min=300,
    low_gap=20,
    high_gap=20,
):
    return {
        "id": point_id,
        "history_tag": tag,
        "enabled": enabled,
        "low": {
            "enabled": low_enabled,
            "limit": low_limit,
            "min_duration_seconds": low_min,
            "merge_gap_seconds": low_gap,
        },
        "high": {
            "enabled": high_enabled,
            "limit": high_limit,
            "min_duration_seconds": high_min,
            "merge_gap_seconds": high_gap,
        },
    }


def config(points):
    return {
        "id": "analog_limit_exceedance",
        "name": "连续量上下限超限考核",
        "enabled": True,
        "assessment_window": {
            "start_offset_minutes": 0,
            "end_offset_minutes": 0,
        },
        "parameters": {"points": points},
        "scoring": {
            "default_score_per_event": 1,
            "by_point_event_type": {
                "P": {"low_limit": 1, "high_limit": 2},
            },
        },
    }


def sample(timestamp, value, *, sequence_no=1):
    return make_history_sample(timestamp, str(value), sequence_no=sequence_no)


def test_rule_reads_history_and_returns_high_event():
    client = FakeDataClient(
        {
            "TAG-P": [
                sample(START - timedelta(minutes=1), 100),
                sample(START + timedelta(minutes=10), 121),
                sample(START + timedelta(minutes=16), 100),
            ]
        }
    )
    event = Rule(client, config([point()])).evaluate(START, END)[0]

    assert event.data["point_id"] == "P"
    assert event.data["event_type"] == "high_limit"
    assert event.data["limit"] == 120.0
    assert event.data["violation_start"] == START + timedelta(minutes=10)
    assert event.data["violation_end"] == START + timedelta(minutes=16)
    assert event.data["is_open"] is False
    assert event.message == "P 高限超限持续超过 5 分钟"


def test_rule_reads_history_and_returns_low_event():
    client = FakeDataClient(
        {
            "TAG-P": [
                sample(START - timedelta(minutes=1), 100),
                sample(START + timedelta(minutes=10), 79),
                sample(START + timedelta(minutes=16), 100),
            ]
        }
    )
    event = Rule(client, config([point()])).evaluate(START, END)[0]

    assert event.data["event_type"] == "low_limit"
    assert event.message == "P 低限超限持续超过 5 分钟"


def test_rule_respects_disabled_point():
    client = FakeDataClient({"TAG-P": [sample(START, 121)]})

    events = Rule(client, config([point(enabled=False)])).evaluate(START, END)

    assert events == []
    assert client.calls == []


def test_rule_respects_low_disabled():
    client = FakeDataClient(
        {
            "TAG-P": [
                sample(START - timedelta(minutes=1), 100),
                sample(START, 79),
                sample(START + timedelta(minutes=6), 100),
            ]
        }
    )

    events = Rule(client, config([point(low_enabled=False)])).evaluate(START, END)

    assert events == []


def test_rule_respects_high_disabled():
    client = FakeDataClient(
        {
            "TAG-P": [
                sample(START - timedelta(minutes=1), 100),
                sample(START, 121),
                sample(START + timedelta(minutes=6), 100),
            ]
        }
    )

    events = Rule(client, config([point(high_enabled=False)])).evaluate(START, END)

    assert events == []


def test_rule_queries_confirmation_tail():
    client = FakeDataClient()
    rule_config = config(
        [
            point(low_min=300, low_gap=20, high_min=600, high_gap=30),
            point(
                "Q",
                "TAG-Q",
                low_min=900,
                low_gap=100,
                high_min=1200,
                high_gap=200,
            ),
        ]
    )
    Rule(client, rule_config).evaluate(START, END)

    assert client.calls[0][1:] == (
        START,
        END + timedelta(seconds=1401),
    )


def test_rule_preheats_and_assesses_trailing_mean_curve():
    smoothed_point = point()
    smoothed_point["smoothing"] = {
        "enabled": True,
        "method": "trailing_mean",
        "window_seconds": 30,
        "min_samples": 3,
    }
    history = [
        sample(START + timedelta(seconds=seconds), 121)
        for seconds in range(0, 400, 10)
    ]
    history.extend(
        sample(START + timedelta(seconds=seconds), 100)
        for seconds in range(400, 441, 10)
    )
    history.extend(
        [
            sample(START - timedelta(seconds=30), 100),
            sample(START - timedelta(seconds=20), 100),
            sample(START - timedelta(seconds=10), 100),
        ]
    )
    client = FakeDataClient({"TAG-P": history})

    event = Rule(client, config([smoothed_point])).evaluate(START, END)[0]

    assert client.calls[0][1] == START - timedelta(seconds=30)
    assert event.start_time == START + timedelta(seconds=30)
    assert event.data["smoothing"] == {
        "enabled": True,
        "method": "trailing_mean",
        "window_seconds": 30.0,
        "min_samples": 3,
    }


def test_rule_uses_previous_history_sample():
    client = FakeDataClient(
        {
            "TAG-P": [
                sample(START - timedelta(minutes=1), 121),
                sample(START + timedelta(minutes=6), 100),
            ]
        }
    )

    events = Rule(client, config([point()])).evaluate(START, END)

    assert events == []


def test_rule_filters_event_started_before_window():
    client = FakeDataClient(
        {
            "TAG-P": [
                sample(START - timedelta(seconds=1), 121),
                sample(START + timedelta(minutes=6), 100),
            ]
        }
    )

    assert Rule(client, config([point()])).evaluate(START, END) == []


def test_rule_filters_event_start_equal_window_end():
    client = FakeDataClient(
        {
            "TAG-P": [
                sample(END - timedelta(minutes=1), 100),
                sample(END, 121),
                sample(END + timedelta(minutes=6), 100),
            ]
        }
    )

    assert Rule(client, config([point()])).evaluate(START, END) == []


def test_rule_keeps_event_start_equal_window_start():
    client = FakeDataClient(
        {
            "TAG-P": [
                sample(START - timedelta(minutes=1), 100),
                sample(START, 121),
                sample(START + timedelta(minutes=6), 100),
            ]
        }
    )

    events = Rule(client, config([point()])).evaluate(START, END)

    assert len(events) == 1
    assert events[0].start_time == START


def test_rule_generates_stable_event_key():
    histories = {
        "TAG-P": [
            sample(START - timedelta(minutes=1), 100),
            sample(START, 121),
            sample(START + timedelta(minutes=6), 100),
        ]
    }
    rule_config = config([point()])
    first = Rule(FakeDataClient(histories), rule_config).evaluate(START, END)
    second = Rule(FakeDataClient(histories), rule_config).evaluate(START, END)

    assert first[0].data["event_key"] == second[0].data["event_key"]
    assert first[0].data["event_key"] == (
        "analog_limit_exceedance:P:high_limit:2026-09-01T08:00:00"
    )


def test_open_rule_event_uses_observation_end_but_keeps_violation_end_none():
    client = FakeDataClient(
        {
            "TAG-P": [
                sample(START - timedelta(minutes=1), 100),
                sample(END - timedelta(minutes=1), 121),
            ]
        }
    )
    event = Rule(client, config([point()])).evaluate(START, END)[0]
    query_end = END + timedelta(seconds=321)

    assert event.end_time == query_end
    assert event.data["violation_end"] is None
    assert event.data["is_open"] is True


def test_rule_never_converts_dcs_error_to_empty_events():
    class FailingClient(FakeDataClient):
        def get_histories(self, tags, start_time, end_time):
            raise RuntimeError("DCS history failed")

    with pytest.raises(RuntimeError, match="DCS history failed"):
        Rule(FailingClient(), config([point()])).evaluate(START, END)


def test_rule_requires_data_client():
    with pytest.raises(ValueError, match="data client"):
        Rule(None, config([point()]))


def test_rule_requires_history_query_interface():
    with pytest.raises(TypeError, match="get_history"):
        Rule(object(), config([point()]))


def test_rule_loader_can_load_analog_limit_exceedance():
    loaded = RuleLoader(data_client=FakeDataClient()).load(
        "analog_limit_exceedance"
    )

    assert loaded.id == "analog_limit_exceedance"
    assert loaded.name == "连续量上下限超限考核"
    assert loaded.enabled is True

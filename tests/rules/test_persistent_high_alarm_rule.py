from datetime import datetime, timedelta

import pytest

from dcs_performance.data.errors import DcsServiceError
from dcs_performance.engine.loader import RuleLoader
from dcs_performance.rules.persistent_high_alarm.rule import Rule

from tests.fakes import FakeDataClient, make_history_sample


WINDOW_START = datetime(2026, 8, 31, 7, 50)
WINDOW_END = datetime(2026, 8, 31, 19, 50)


def make_config(*points: tuple[str, str], threshold_seconds: int | float = 300):
    return {
        "id": "persistent_high_alarm",
        "name": "持续高报考核",
        "enabled": True,
        "assessment_window": {
            "start_offset_minutes": -10,
            "end_offset_minutes": 10,
        },
        "parameters": {
            "active_value": "1",
            "threshold_seconds": threshold_seconds,
            "points": [
                {"id": point_id, "history_tag": history_tag}
                for point_id, history_tag in points
            ],
        },
        "scoring": {
            "default_score_per_event": 1,
            "by_point": {point_id: 1 for point_id, _ in points},
        },
    }


def test_rule_reads_history_and_emits_one_assessment_event():
    tag = "TAG-LA-115077"
    alarm_start = datetime(2026, 8, 31, 10, 12)
    alarm_end = datetime(2026, 8, 31, 10, 20)
    client = FakeDataClient({
        tag: [
            make_history_sample(datetime(2026, 8, 31, 10, 0), "0"),
            make_history_sample(alarm_start, "1"),
            make_history_sample(alarm_end, "0"),
        ]
    })
    rule = Rule(
        data_client=client,
        config=make_config(("LA-115077", tag)),
    )

    events = rule.evaluate(WINDOW_START, WINDOW_END)

    assert len(events) == 1
    event = events[0]
    assert (event.start_time, event.end_time) == (alarm_start, alarm_end)
    assert event.message == "LA-115077 高报持续超过 5 分钟"
    assert event.data == {
        "point_id": "LA-115077",
        "history_tag": tag,
        "alarm_start": alarm_start,
        "alarm_end": alarm_end,
        "duration_seconds": 480.0,
        "threshold_seconds": 300.0,
        "is_open": False,
        "event_key": "persistent_high_alarm:LA-115077:2026-08-31T10:12:00",
    }
    assert client.calls[0] == (tag, WINDOW_START, datetime(2026, 8, 31, 19, 55, 1))


def test_rule_does_not_emit_alarm_that_last_exactly_five_minutes():
    tag = "TAG-LA-115177"
    alarm_start = datetime(2026, 8, 31, 11, 3)
    client = FakeDataClient({
        tag: [
            make_history_sample(datetime(2026, 8, 31, 11, 0), "0"),
            make_history_sample(alarm_start, "1"),
            make_history_sample(alarm_start + timedelta(seconds=300), "0"),
        ]
    })

    events = Rule(
        data_client=client,
        config=make_config(("LA-115177", tag)),
    ).evaluate(WINDOW_START, WINDOW_END)

    assert events == []


def test_rule_assigns_cross_boundary_alarm_by_alarm_start_and_next_window_filters_it():
    tag = "TAG-LA-117075"
    alarm_start = datetime(2026, 8, 31, 19, 48)
    recovery = datetime(2026, 8, 31, 20, 10)
    client = FakeDataClient({
        tag: [
            make_history_sample(datetime(2026, 8, 31, 19, 40), "0"),
            make_history_sample(alarm_start, "1"),
            make_history_sample(recovery, "0"),
        ]
    })
    rule = Rule(data_client=client, config=make_config(("LA-117075", tag)))

    current_events = rule.evaluate(WINDOW_START, WINDOW_END)
    next_events = rule.evaluate(
        WINDOW_END,
        datetime(2026, 9, 1, 7, 50),
    )

    assert len(current_events) == 1
    assert current_events[0].start_time == alarm_start
    assert current_events[0].data["alarm_end"] is None
    assert current_events[0].data["is_open"] is True
    assert current_events[0].end_time == datetime(2026, 8, 31, 19, 55, 1)
    assert next_events == []


@pytest.mark.parametrize(
    ("alarm_start", "belongs_to_current"),
    [
        (datetime(2026, 8, 31, 7, 49, 59), False),
        (datetime(2026, 8, 31, 7, 50), True),
        (datetime(2026, 8, 31, 19, 49, 59), True),
        (datetime(2026, 8, 31, 19, 50), False),
    ],
)
def test_rule_uses_half_open_alarm_start_ownership(
    alarm_start: datetime,
    belongs_to_current: bool,
):
    tag = "TAG1"
    client = FakeDataClient({
        tag: [
            make_history_sample(alarm_start - timedelta(seconds=1), "0"),
            make_history_sample(alarm_start, "1"),
            make_history_sample(alarm_start + timedelta(seconds=301), "0"),
        ]
    })
    events = Rule(
        data_client=client,
        config=make_config(("LA-115077", tag)),
    ).evaluate(WINDOW_START, WINDOW_END)

    assert bool(events) is belongs_to_current


def test_rule_never_turns_data_client_error_into_empty_events():
    class FailingClient(FakeDataClient):
        def get_history(self, tag, start_time, end_time):
            raise DcsServiceError("historian unavailable", code="service_busy")

    rule = Rule(
        data_client=FailingClient(),
        config=make_config(("LA-115077", "TAG1")),
    )

    with pytest.raises(DcsServiceError, match="historian unavailable"):
        rule.evaluate(WINDOW_START, WINDOW_END)


@pytest.mark.parametrize(
    "bad_config",
    [
        {"parameters": {"active_value": "1", "threshold_seconds": 300, "points": []}},
        {"parameters": {"active_value": "1", "threshold_seconds": 0, "points": [{"id": "P", "history_tag": "T"}]}},
        {"parameters": {"active_value": "ON", "threshold_seconds": 300, "points": [{"id": "P", "history_tag": "T"}]}},
    ],
)
def test_rule_rejects_invalid_static_configuration(bad_config):
    with pytest.raises((ValueError, TypeError)):
        Rule(data_client=FakeDataClient(), config=bad_config)


def test_rule_rejects_missing_data_client():
    with pytest.raises(ValueError, match="data client"):
        Rule(data_client=None, config=make_config(("P", "T")))


def test_rule_rejects_unknown_history_value_at_runtime():
    tag = "TAG1"
    client = FakeDataClient({tag: [make_history_sample(WINDOW_START, "BAD")]})
    rule = Rule(data_client=client, config=make_config(("P", tag)))

    with pytest.raises(ValueError, match="digital state"):
        rule.evaluate(WINDOW_START, WINDOW_END)


def test_persistent_rule_can_be_loaded_by_directory_rule_loader():
    loaded = RuleLoader(data_client=FakeDataClient()).load("persistent_high_alarm")

    assert loaded.id == "persistent_high_alarm"
    assert loaded.name == "持续高报考核"
    assert loaded.enabled is True

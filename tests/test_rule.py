from datetime import datetime
from typing import get_type_hints

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.core.rule import AssessmentRule
from dcs_performance.rules.example_rule.rule import Rule


class FakeDataClient:
    def get_history(self, tag, start_time, end_time):
        return []

    def get_events(self, start_time, end_time):
        return []


def test_example_rule_accepts_time_range_and_returns_event_list():
    rule = Rule(data_client=FakeDataClient(), config={})
    start_time = datetime(2026, 8, 31, 8, 20)
    end_time = datetime(2026, 8, 31, 20, 0)

    events = rule.evaluate(start_time, end_time)

    assert isinstance(rule, AssessmentRule)
    assert isinstance(events, list)
    assert events == []
    assert get_type_hints(Rule.evaluate)["return"] == list[AssessmentEvent]

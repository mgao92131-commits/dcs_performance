from datetime import datetime, timedelta

from dcs_performance.core.evaluation import EvaluatedAssessmentEvent
from dcs_performance.engine.engine import AssessmentEngine
from dcs_performance.engine.loader import LoadedRule
from dcs_performance.results.scorer import AssessmentScorer
from dcs_performance.results.summary import build_shift_summary
from dcs_performance.rules.analog_limit_exceedance.rule import Rule
from dcs_performance.shifts.model import Shift

from tests.fakes import FakeDataClient, make_history_sample


class OneRuleLoader:
    def __init__(self, loaded_rule):
        self.loaded_rule = loaded_rule

    def load_enabled(self):
        return [self.loaded_rule]


def point(point_id="TI-013008", tag="TI-TAG"):
    return {
        "id": point_id,
        "history_tag": tag,
        "enabled": True,
        "low": {
            "enabled": True,
            "limit": 80,
            "min_duration_seconds": 300,
            "merge_gap_seconds": 20,
        },
        "high": {
            "enabled": True,
            "limit": 120,
            "min_duration_seconds": 300,
            "merge_gap_seconds": 20,
        },
    }


def pipeline_config():
    return {
        "id": "analog_limit_exceedance",
        "name": "连续量上下限超限考核",
        "enabled": True,
        "assessment_window": {
            "start_offset_minutes": 0,
            "end_offset_minutes": 0,
        },
        "parameters": {"points": [point()]},
        "scoring": {
            "default_score_per_event": 1,
            "by_point_event_type": {
                "TI-013008": {"low_limit": 1, "high_limit": 2}
            },
        },
    }


def sample(timestamp, value, sequence_no=1):
    return make_history_sample(timestamp, str(value), sequence_no=sequence_no)


def test_shift_rule_engine_scorer_summary_pipeline():
    shift = Shift(
        team_id="B",
        shift_type="day",
        start_time=datetime(2026, 9, 1, 8, 0),
        end_time=datetime(2026, 9, 1, 20, 0),
    )
    config = pipeline_config()
    client = FakeDataClient(
        {
            "TI-TAG": [
                sample(shift.start_time - timedelta(minutes=1), 100),
                sample(shift.start_time + timedelta(minutes=10), 121),
                sample(shift.start_time + timedelta(minutes=16), 100),
            ]
        }
    )
    rule = Rule(client, config)
    engine = AssessmentEngine(
        loader=OneRuleLoader(LoadedRule(rule=rule, config=config))
    )

    evaluated = engine.run_detailed(shift)

    assert len(evaluated) == 1
    assert isinstance(evaluated[0], EvaluatedAssessmentEvent)
    item = evaluated[0]
    assert item.rule_id == "analog_limit_exceedance"
    assert item.rule_name == "连续量上下限超限考核"
    assert item.event.data["point_id"] == "TI-013008"
    assert item.event.data["event_type"] == "high_limit"
    assert item.event.start_time == shift.start_time + timedelta(minutes=10)
    assert item.event.end_time == shift.start_time + timedelta(minutes=16)

    assigned = AssessmentScorer().score(item)

    assert assigned.rule_id == "analog_limit_exceedance"
    assert assigned.team_id == "B"
    assert assigned.shift_start == shift.start_time
    assert assigned.shift_end == shift.end_time
    assert assigned.event_start == item.event.start_time
    assert assigned.event_end == item.event.end_time
    assert assigned.score == 2
    assert assigned.data["event_type"] == "high_limit"

    summary = build_shift_summary(
        [assigned],
        points=config["parameters"]["points"],
    )

    assert summary.team_id == "B"
    assert summary.shift_start == shift.start_time
    assert summary.shift_end == shift.end_time
    assert summary.event_count == 1
    assert summary.total_score == 2
    assert summary.by_point["TI-013008"].event_count == 1
    assert summary.by_point["TI-013008"].score == 2


def test_cross_window_event_is_owned_by_its_start_window_only():
    window_a = Shift(
        team_id="A",
        shift_type="day",
        start_time=datetime(2026, 9, 1, 8, 0),
        end_time=datetime(2026, 9, 1, 20, 0),
    )
    window_b = Shift(
        team_id="B",
        shift_type="night",
        start_time=datetime(2026, 9, 1, 20, 0),
        end_time=datetime(2026, 9, 2, 8, 0),
    )
    config = pipeline_config()
    # A larger merge gap makes the shared confirmation tail long enough to
    # observe the actual recovery at 20:10 while keeping one event.
    config["parameters"]["points"][0]["low"]["merge_gap_seconds"] = 600
    config["parameters"]["points"][0]["high"]["merge_gap_seconds"] = 600
    histories = {
        "TI-TAG": [
            sample(datetime(2026, 9, 1, 19, 0), 100),
            sample(datetime(2026, 9, 1, 19, 58), 121),
            sample(datetime(2026, 9, 1, 20, 10), 100),
        ]
    }
    rule = Rule(FakeDataClient(histories), config)
    engine = AssessmentEngine(
        loader=OneRuleLoader(LoadedRule(rule=rule, config=config))
    )

    first = engine.run_detailed(window_a)
    second = engine.run_detailed(window_b)

    assert len(first) == 1
    assert first[0].event.start_time == datetime(2026, 9, 1, 19, 58)
    assert first[0].event.end_time == datetime(2026, 9, 1, 20, 10)
    assert second == []

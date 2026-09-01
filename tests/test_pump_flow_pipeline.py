from datetime import datetime, timedelta
from pathlib import Path

from dcs_performance.core.window import build_assessment_window
from dcs_performance.engine.engine import AssessmentEngine
from dcs_performance.engine.loader import LoadedRule, RuleLoader
from dcs_performance.results.scorer import AssessmentScorer
from dcs_performance.results.summary import build_shift_summary
from dcs_performance.rules.pump_flow_compliance.rule import Rule
from dcs_performance.shifts import (
    CalendarShiftResolver,
    Cyclic12HourShiftCalendar,
    load_cyclic_schedule_config,
)

from tests.fakes import FakeDataClient, make_history_sample


class OneRuleLoader:
    def __init__(self, loaded_rule):
        self.loaded_rule = loaded_rule

    def load_enabled(self):
        return [self.loaded_rule]


def pipeline_config():
    return {
        "id": "pump_flow_compliance",
        "name": "泵组流量考核",
        "enabled": True,
        "assessment_window": {
            "start_offset_minutes": -10,
            "end_offset_minutes": 10,
        },
        "parameters": {
            "points": [
                {
                    "id": "117P01",
                    "pump_a_tag": "A",
                    "pump_b_tag": "B",
                    "flow_tag": "FLOW",
                    "running_value": "1",
                    "normal_min_flow": 125,
                    "switching_min_flow": 100,
                    "max_switch_duration_seconds": 600,
                }
            ]
        },
        "scoring": {
            "default_score_per_event": 1,
            "by_event_type": {"low_flow": 1, "switch_timeout": 2},
        },
    }


def test_pipeline_uses_common_engine_runner_scorer_and_summary():
    calendar = Cyclic12HourShiftCalendar(
        load_cyclic_schedule_config(
            Path("src/dcs_performance/shifts/performance_schedule.json")
        )
    )
    shift = CalendarShiftResolver(calendar).resolve(datetime(2026, 8, 31, 10, 0))
    window = build_assessment_window(shift, pipeline_config())

    switch_start = datetime(2026, 8, 31, 10, 0)
    client = FakeDataClient(
        {
            "A": [
                make_history_sample(datetime(2026, 8, 31, 9, 0), "1"),
                make_history_sample(switch_start, "1"),
                make_history_sample(switch_start + timedelta(minutes=12), "0"),
            ],
            "B": [
                make_history_sample(datetime(2026, 8, 31, 9, 0), "0"),
                make_history_sample(switch_start, "1"),
                make_history_sample(switch_start + timedelta(minutes=12), "1"),
            ],
            "FLOW": [
                make_history_sample(datetime(2026, 8, 31, 9, 0), "125"),
                make_history_sample(switch_start, "95"),
            ],
        }
    )
    config = pipeline_config()
    rule = Rule(client, config)
    loaded = LoadedRule(rule=rule, config=config)
    engine = AssessmentEngine(loader=OneRuleLoader(loaded))

    evaluated = engine.run_detailed(shift)
    assigned = [AssessmentScorer().score(item) for item in evaluated]
    summary = build_shift_summary(
        assigned,
        points=config["parameters"]["points"],
    )

    assert shift.team_id == "B"
    assert (window.start_time, window.end_time) == (
        datetime(2026, 8, 31, 7, 50),
        datetime(2026, 8, 31, 19, 50),
    )
    assert [item.event.data["event_type"] for item in evaluated] == [
        "low_flow",
        "switch_timeout",
    ]
    assert [item.score for item in assigned] == [1, 2]
    assert summary.event_count == 2
    assert summary.total_score == 3
    assert summary.by_point["117P01"].event_count == 2
    assert summary.by_point["117P01"].score == 3


def test_loader_discovers_pump_rule_without_engine_specific_branch():
    rule_ids = [path.name for path in RuleLoader(data_client=None).discover()]
    assert "pump_flow_compliance" in rule_ids

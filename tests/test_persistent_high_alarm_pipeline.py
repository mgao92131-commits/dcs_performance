from datetime import datetime
from pathlib import Path

from dcs_performance.core.window import build_assessment_window
from dcs_performance.engine.engine import AssessmentEngine
from dcs_performance.engine.loader import LoadedRule
from dcs_performance.results.scorer import AssessmentScorer
from dcs_performance.results.summary import build_shift_summary
from dcs_performance.rules.persistent_high_alarm.rule import Rule
from dcs_performance.shifts import (
    CalendarShiftResolver,
    Cyclic12HourShiftCalendar,
    load_cyclic_schedule_config,
)

from tests.fakes import FakeDataClient, make_history_sample


class OneRuleLoader:
    def __init__(self, loaded_rule: LoadedRule) -> None:
        self.loaded_rule = loaded_rule

    def load_enabled(self):
        return [self.loaded_rule]


def pipeline_config():
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
            "threshold_seconds": 300,
            "recovery_search_hours": 48,
            "points": [
                {"id": "LA-115077", "history_tag": "TAG-115077"},
                {"id": "LA-115177", "history_tag": "TAG-115177"},
                {"id": "LA-117075", "history_tag": "TAG-117075"},
                {"id": "LA-215077", "history_tag": "TAG-215077"},
                {"id": "LA-215177", "history_tag": "TAG-215177"},
                {"id": "LA-217075", "history_tag": "TAG-217075"},
            ],
        },
        "scoring": {
            "default_score_per_event": 1,
            "by_point": {
                "LA-115077": 1,
                "LA-115177": 1,
                "LA-117075": 1,
                "LA-215077": 1,
                "LA-215177": 1,
                "LA-217075": 1,
            },
        },
    }


def test_pipeline_runs_from_cyclic_shift_to_summary():
    calendar = Cyclic12HourShiftCalendar(
        load_cyclic_schedule_config(
            Path("src/dcs_performance/shifts/performance_schedule.json")
        )
    )
    shift = CalendarShiftResolver(calendar).resolve(datetime(2026, 8, 31, 10, 0))
    config = pipeline_config()
    client = FakeDataClient({
        "TAG-115077": [
            make_history_sample(datetime(2026, 8, 31, 7, 40), "0"),
            make_history_sample(datetime(2026, 8, 31, 10, 12), "1"),
            make_history_sample(datetime(2026, 8, 31, 10, 20, 30), "0"),
        ],
        "TAG-115177": [
            make_history_sample(datetime(2026, 8, 31, 7, 30), "0"),
            make_history_sample(datetime(2026, 8, 31, 11, 3), "1"),
            make_history_sample(datetime(2026, 8, 31, 11, 6), "0"),
        ],
        "TAG-117075": [
            make_history_sample(datetime(2026, 8, 31, 7, 30), "0"),
            make_history_sample(datetime(2026, 8, 31, 19, 48), "1"),
            make_history_sample(datetime(2026, 8, 31, 20, 10), "0"),
        ],
        "TAG-215077": [
            make_history_sample(datetime(2026, 8, 31, 7, 30), "0"),
        ],
        "TAG-215177": [
            make_history_sample(datetime(2026, 8, 31, 7, 30), "0"),
            make_history_sample(datetime(2026, 8, 31, 12, 0), "1"),
            make_history_sample(datetime(2026, 8, 31, 12, 6, 1), "0"),
        ],
        "TAG-217075": [
            make_history_sample(datetime(2026, 8, 31, 7, 30), "0"),
        ],
    })
    rule = Rule(data_client=client, config=config)
    engine = AssessmentEngine(
        loader=OneRuleLoader(LoadedRule(rule=rule, config=config))
    )

    evaluated = engine.run_detailed(shift)
    assigned = [AssessmentScorer().score(item) for item in evaluated]
    summary = build_shift_summary(assigned, points=config["parameters"]["points"])

    assert shift.team_id == "B"
    assert build_assessment_window(shift, config).start_time == datetime(
        2026, 8, 31, 7, 50
    )
    assert build_assessment_window(shift, config).end_time == datetime(
        2026, 8, 31, 19, 50
    )
    assert summary.event_count == 3
    assert summary.total_score == 3
    assert summary.by_point["LA-115077"].event_count == 1
    assert summary.by_point["LA-115177"].event_count == 0
    assert summary.by_point["LA-117075"].event_count == 1
    assert summary.by_point["LA-215077"].event_count == 0
    assert summary.by_point["LA-215177"].event_count == 1
    assert summary.by_point["LA-217075"].event_count == 0

    next_shift = CalendarShiftResolver(calendar).resolve(datetime(2026, 8, 31, 21, 0))
    next_evaluated = engine.run_detailed(next_shift)
    assert [
        item.event.data["point_id"] for item in next_evaluated
    ] == []

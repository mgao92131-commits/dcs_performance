from datetime import datetime, timedelta

from dcs_performance.core.evaluation import EvaluatedAssessmentEvent
from dcs_performance.engine.engine import AssessmentEngine
from dcs_performance.engine.loader import LoadedRule
from dcs_performance.results.scorer import AssessmentScorer
from dcs_performance.rules.analog_trend_stability.rule import Rule
from dcs_performance.shifts.model import Shift

from tests.fakes import FakeDataClient, make_history_sample


class OneRuleLoader:
    def __init__(self, loaded_rule):
        self.loaded_rule = loaded_rule

    def load_enabled(self):
        return [self.loaded_rule]


def pipeline_config():
    return {
        "id": "analog_trend_stability",
        "name": "连续量趋势稳定性考核",
        "enabled": True,
        "assessment_window": {
            "start_offset_minutes": 0,
            "end_offset_minutes": 0,
        },
        "parameters": {
            "points": [
                {
                    "id": "LICA-012019",
                    "history_tag": "LICA-TAG",
                    "quality": {"max_gap_seconds": 600},
                    "trend": {
                        "method": "rolling_mean",
                        "alignment": "trailing",
                        "window_seconds": 600,
                        "min_samples": 1,
                    },
                    "stability": {
                        "enabled": True,
                        "warning_deviation": 0.5,
                        "high_deviation": 1.0,
                        "min_duration_seconds": 60,
                        "merge_gap_seconds": 0,
                    },
                    "drift": {"enabled": False, "windows": []},
                }
            ]
        },
        "scoring": {
            "default_score_per_event": 1,
            "by_point": {
                "LICA-012019": {
                    "stability_deviation": {"warning": 1, "high": 2},
                    "trend_drift": {"warning": 1, "high": 2},
                }
            },
        },
    }


def test_engine_rule_scorer_pipeline_preserves_context_and_score_key():
    shift = Shift(
        team_id="B",
        shift_type="day",
        start_time=datetime(2026, 8, 31, 8, 0),
        end_time=datetime(2026, 8, 31, 16, 0),
    )
    start = datetime(2026, 8, 31, 7, 40)
    values = [0, 0, 0, 4, 4, 4, 4, 0, 0, 0]
    histories = {
        "LICA-TAG": [
            make_history_sample(
                start + timedelta(minutes=index * 5),
                str(value),
                sequence_no=index + 1,
            )
            for index, value in enumerate(values)
        ]
    }
    config = pipeline_config()
    rule = Rule(FakeDataClient(histories), config)
    engine = AssessmentEngine(
        loader=OneRuleLoader(LoadedRule(rule=rule, config=config))
    )

    evaluated = engine.run_detailed(shift)

    assert evaluated
    assert all(isinstance(item, EvaluatedAssessmentEvent) for item in evaluated)
    assert evaluated[0].event.data["point_id"] == "LICA-012019"
    assert evaluated[0].event.data["event_type"] == "stability_deviation"
    assert evaluated[0].event.data["severity"] == "high"
    assert evaluated[0].event.data["score_key"] == "stability_deviation.high"
    assert evaluated[0].event.start_time >= shift.start_time
    assert evaluated[0].event.end_time <= shift.end_time

    assigned = [AssessmentScorer().score(item) for item in evaluated]

    assert assigned[0].score == 2
    assert assigned[0].team_id == "B"
    assert assigned[0].shift_start == shift.start_time
    assert assigned[0].shift_end == shift.end_time
    assert assigned[0].event_start == evaluated[0].event.start_time
    assert assigned[0].event_end == evaluated[0].event.end_time

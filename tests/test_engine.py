from datetime import datetime

from dcs_performance.engine.engine import AssessmentEngine
from dcs_performance.engine.loader import RuleLoader
from dcs_performance.core.evaluation import EvaluatedAssessmentEvent
from dcs_performance.shifts.model import Shift


class FakeDataClient:
    def get_history(self, tag, start_time, end_time):
        raise AssertionError("example rule must not access DCS history")

    def get_events(self, start_time, end_time):
        raise AssertionError("example rule must not access DCS events")


class OneRuleLoader:
    def __init__(self, loaded_rule):
        self.loaded_rule = loaded_rule

    def load_enabled(self):
        return [self.loaded_rule]


def test_engine_loads_example_rule_builds_window_and_collects_events():
    data_client = FakeDataClient()
    loaded_rule = RuleLoader(data_client=data_client).load("example_rule")
    received = {}

    def record_evaluate(start_time, end_time):
        received["start_time"] = start_time
        received["end_time"] = end_time
        return []

    # Keep the real loaded ExampleRule, replacing only its empty method with a
    # recorder so the test can observe the window passed by Runner.
    loaded_rule.rule.evaluate = record_evaluate
    engine = AssessmentEngine(loader=OneRuleLoader(loaded_rule))
    shift = Shift(
        team_id="A",
        shift_type="day",
        start_time=datetime(2026, 8, 31, 8, 0),
        end_time=datetime(2026, 8, 31, 20, 0),
    )

    events = engine.run(shift)

    assert events == []
    assert received == {
        "start_time": datetime(2026, 8, 31, 8, 20),
        "end_time": datetime(2026, 8, 31, 20, 0),
    }


def test_engine_run_detailed_preserves_rule_shift_window_and_config():
    loaded_rule = RuleLoader(data_client=FakeDataClient()).load("example_rule")
    shift = Shift(
        team_id="A",
        shift_type="day",
        start_time=datetime(2026, 8, 31, 8, 0),
        end_time=datetime(2026, 8, 31, 20, 0),
    )
    engine = AssessmentEngine(loader=OneRuleLoader(loaded_rule))

    detailed = engine.run_detailed(shift)

    assert detailed == []
    assert isinstance(engine.runner.run_detailed(shift, loaded_rule), list)

    executions = engine.run_executions(shift)
    assert len(executions) == 1
    assert executions[0].rule_id == "example_rule"
    assert executions[0].events == ()
    assert executions[0].window.start_time == datetime(2026, 8, 31, 8, 20)

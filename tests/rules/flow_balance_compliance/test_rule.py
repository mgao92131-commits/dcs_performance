from datetime import datetime, timedelta

from dcs_performance.engine.loader import RuleLoader
from dcs_performance.rules.flow_balance_compliance.rule import Rule

from tests.fakes import FakeDataClient, make_history_sample


START = datetime(2026, 9, 1, 8, 0)
END = START + timedelta(hours=1)
LOGIC = "LOGIC27/YK-TLFH/OUT1.CV"
SY116 = "SY-116/AI1/PV.CV"
SY216 = "SY-216/AI1/PV.CV"


def config():
    return {
        "id": "flow_balance_compliance",
        "name": "浆料进料量平衡考核",
        "enabled": True,
        "parameters": {
            "points": [
                {
                    "id": "SLURRY_FLOW_BALANCE",
                    "logic_tag": LOGIC,
                    "sy_tags": [SY116, SY216],
                    "enabled": True,
                    "smoothing": {
                        "enabled": True,
                        "method": "trailing_mean",
                        "window_seconds": 60,
                        "min_samples": 1,
                    },
                    "low_limit": -15,
                    "high_limit": 15,
                    "min_duration_seconds": 300,
                    "merge_gap_seconds": 20,
                    "max_gap_seconds": 60,
                }
            ]
        },
        "scoring": {},
    }


def sample(timestamp, value):
    return make_history_sample(timestamp, str(value))


def test_rule_queries_three_tags_and_emits_balance_event():
    times = [START + timedelta(minutes=index) for index in range(7)]
    client = FakeDataClient(
        {
            LOGIC: [sample(time, 116 if 0 <= index <= 5 else 100) for index, time in enumerate(times)],
            SY116: [sample(time, 60) for time in times],
            SY216: [sample(time, 40) for time in times],
        }
    )

    events = Rule(client, config()).evaluate(START, END)

    assert len(events) == 1
    assert events[0].data["peak_difference"] == 16.0
    assert set(client.calls) == {
        (LOGIC, START - timedelta(seconds=120), END + timedelta(seconds=320)),
        (SY116, START - timedelta(seconds=120), END + timedelta(seconds=320)),
        (SY216, START - timedelta(seconds=120), END + timedelta(seconds=320)),
    }


def test_rule_loader_discovers_flow_balance_rule():
    loaded = RuleLoader(data_client=FakeDataClient()).load("flow_balance_compliance")

    assert loaded.id == "flow_balance_compliance"
    assert loaded.enabled is True

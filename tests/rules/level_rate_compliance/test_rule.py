from datetime import datetime, timedelta

from dcs_performance.rules.level_rate_compliance.detector import (
    RATE_DOWN,
    LevelRateOccurrence,
)
from dcs_performance.rules.level_rate_compliance.rule import Rule

from tests.fakes import FakeDataClient


START = datetime(2026, 9, 1, 8, 0)
END = START + timedelta(hours=1)


def config():
    return {
        "id": "level_rate_compliance",
        "name": "level rate",
        "enabled": True,
        "parameters": {
            "points": [
                {
                    "id": "LICA-012019",
                    "history_tag": "LICA-012019/PID1/PV.CV",
                    "enabled": True,
                    "smoothing": {
                        "enabled": False,
                        "method": "trailing_mean",
                        "window_seconds": 60,
                        "min_samples": 1,
                    },
                    "rate_window_seconds": 120,
                    "lower_rate": -0.14,
                    "upper_rate": 0.14,
                    "persistence_seconds": 300,
                    "max_gap_seconds": 60,
                    "merge_gap_seconds": 0,
                }
            ]
        },
        "scoring": {
            "default_score_per_event": 1,
            "by_point": {"LICA-012019": {"rate_down": 1, "rate_up": 2}},
        },
    }


class StubDetector:
    def detect(self, samples, point, *, observation_end=None):
        return [
            LevelRateOccurrence(
                start_time=START,
                end_time=START + timedelta(minutes=10),
                direction=RATE_DOWN,
                confirmation_time=START + timedelta(minutes=5),
                peak_rate=-0.2,
                mean_rate=-0.2,
                segment_id=0,
                is_open=False,
            )
        ]


def test_rule_emits_direction_as_score_key():
    rule = Rule(FakeDataClient(), config())
    rule.detector = StubDetector()

    events = rule.evaluate(START, END)

    assert len(events) == 1
    assert events[0].data["event_type"] == "level_rate"
    assert events[0].data["direction"] == RATE_DOWN
    assert events[0].data["score_key"] == RATE_DOWN

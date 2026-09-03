from datetime import datetime, timedelta

from dcs_performance.rules.component_viscosity_control.rule import Rule

from tests.fakes import FakeDataClient, make_history_sample


START = datetime(2026, 9, 1, 8, 0)
END = datetime(2026, 9, 1, 12, 0)


def config(*, exclusion_enabled=False):
    return {
        "id": "component_viscosity_control",
        "name": "组件粘度趋势控制",
        "enabled": True,
        "assessment_window": {"start_offset_minutes": 0, "end_offset_minutes": 0},
        "parameters": {
            "points": [
                {
                    "id": "PI-2311001",
                    "history_tag": "PI-2311001/AI1/PV.CV",
                    "enabled": True,
                    "aggregation": {
                        "enabled": True,
                        "method": "median",
                        "bucket_seconds": 60,
                        "min_samples": 1,
                    },
                    "smoothing": {
                        "enabled": True,
                        "method": "trailing_mean",
                        "window_seconds": 600,
                        "min_samples": 10,
                    },
                    "assessment": {
                        "target": 16.05,
                        "low_limit": 15.95,
                        "high_limit": 16.25,
                        "min_duration_seconds": 600,
                        "merge_gap_seconds": 600,
                    },
                    "exclusion": {
                        "enabled": exclusion_enabled,
                        "method": "robust_deviation",
                        "baseline": 16.075405,
                        "deviation_threshold": 0.285786,
                        "merge_gap_seconds": 600,
                        "remove_after_start_seconds": 7200,
                    },
                }
            ]
        },
        "scoring": {"default_score_per_event": 1},
    }


def build_history():
    samples = []
    history_start = START - timedelta(hours=2, minutes=20)
    for index in range(0, 5 * 60 + 30):
        timestamp = history_start + timedelta(minutes=index)
        value = 15.90 if START + timedelta(minutes=30) <= timestamp < START + timedelta(minutes=65) else 16.05
        samples.append(make_history_sample(timestamp, str(value)))
    return samples


def test_rule_emits_low_viscosity_event_from_clean_trailing_mean():
    rule = Rule(
        FakeDataClient({"PI-2311001/AI1/PV.CV": build_history()}),
        config(),
    )

    events = rule.evaluate(START, END)

    assert len(events) == 1
    event = events[0]
    assert event.data["event_type"] == "viscosity_low"
    assert event.data["history_tag"] == "PI-2311001/AI1/PV.CV"
    assert event.data["smoothing"]["window_seconds"] == 600
    assert event.data["aggregation"]["bucket_seconds"] == 60
    assert event.data["merge_gap_seconds"] == 600
    assert event.start_time >= START


def test_rule_does_not_use_disturbance_values_for_a_viscosity_event():
    history = build_history()
    disturbance_start = START + timedelta(minutes=90)
    history.extend(
        make_history_sample(
            disturbance_start + timedelta(minutes=index),
            "1.0" if index < 3 else "16.75",
        )
        for index in range(20)
    )
    rule = Rule(
        FakeDataClient({"PI-2311001/AI1/PV.CV": history}),
        config(exclusion_enabled=True),
    )

    events = rule.evaluate(START, END)

    assert all(event.data["event_type"] != "viscosity_high" for event in events)

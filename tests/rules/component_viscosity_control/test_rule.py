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
    assert event.data["penalty"]["enabled"] is False
    assert event.data["penalty"]["units"] == 1
    assert event.data["score_multiplier"] == 1
    assert event.start_time >= START


def test_long_event_stays_one_event_and_adds_repeated_penalty_checkpoints():
    raw_config = config()
    point = raw_config["parameters"]["points"][0]
    point["smoothing"]["enabled"] = False
    point["assessment"]["repeat_penalty"] = {
        "enabled": True,
        "interval_seconds": 1800,
        "max_units": None,
    }

    history = [make_history_sample(START - timedelta(minutes=1), "16.05")]
    history.extend(
        make_history_sample(START + timedelta(minutes=index), "15.85")
        for index in range(120)
    )
    history.append(make_history_sample(START + timedelta(minutes=120), "16.05"))

    events = Rule(
        FakeDataClient({"PI-2311001/AI1/PV.CV": history}),
        raw_config,
    ).evaluate(START, END)

    assert len(events) == 1
    event = events[0]
    assert event.data["event_type"] == "viscosity_low"
    assert event.data["duration_seconds"] == 120 * 60
    assert event.data["penalty"]["units"] == 4
    assert event.data["score_multiplier"] == 4
    assert event.data["penalty"]["checkpoints"] == [
        START + timedelta(seconds=600 + index * 1800)
        for index in range(4)
    ]


def test_short_recovery_does_not_reset_repeated_penalty_clock():
    raw_config = config()
    point = raw_config["parameters"]["points"][0]
    point["smoothing"]["enabled"] = False
    point["assessment"]["repeat_penalty"] = {
        "enabled": True,
        "interval_seconds": 1800,
        "max_units": None,
    }

    history = [make_history_sample(START - timedelta(minutes=1), "16.05")]
    history.extend(
        make_history_sample(START + timedelta(minutes=index), "15.85")
        for index in range(20)
    )
    history.extend(
        make_history_sample(START + timedelta(minutes=index), "16.05")
        for index in range(20, 25)
    )
    history.extend(
        make_history_sample(START + timedelta(minutes=index), "15.85")
        for index in range(25, 120)
    )
    history.append(make_history_sample(START + timedelta(minutes=120), "16.05"))

    events = Rule(
        FakeDataClient({"PI-2311001/AI1/PV.CV": history}),
        raw_config,
    ).evaluate(START, END)

    assert len(events) == 1
    assert events[0].data["penalty"]["units"] == 4


def test_recovery_longer_than_merge_gap_starts_a_new_penalty_clock():
    raw_config = config()
    point = raw_config["parameters"]["points"][0]
    point["smoothing"]["enabled"] = False
    point["assessment"]["repeat_penalty"] = {
        "enabled": True,
        "interval_seconds": 1800,
        "max_units": None,
    }

    history = [make_history_sample(START - timedelta(minutes=1), "16.05")]
    history.extend(
        make_history_sample(START + timedelta(minutes=index), "15.85")
        for index in range(20)
    )
    history.extend(
        make_history_sample(START + timedelta(minutes=index), "16.05")
        for index in range(20, 32)
    )
    history.extend(
        make_history_sample(START + timedelta(minutes=index), "15.85")
        for index in range(32, 62)
    )
    history.append(make_history_sample(START + timedelta(minutes=62), "16.05"))

    events = Rule(
        FakeDataClient({"PI-2311001/AI1/PV.CV": history}),
        raw_config,
    ).evaluate(START, END)

    assert len(events) == 2
    assert [event.data["penalty"]["units"] for event in events] == [1, 1]


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


def test_rule_starts_low_event_at_first_metric_after_disturbance_window():
    history = []
    history_start = START - timedelta(hours=3)
    disturbance_start = START + timedelta(minutes=30)
    exclusion_end = disturbance_start + timedelta(hours=2)
    low_end = exclusion_end + timedelta(minutes=20)

    for index in range(0, 7 * 60):
        timestamp = history_start + timedelta(minutes=index)
        if timestamp == disturbance_start:
            value = "16.75"
        elif exclusion_end <= timestamp < low_end:
            value = "15.85"
        else:
            value = "16.05"
        history.append(make_history_sample(timestamp, value))

    raw_config = config(exclusion_enabled=True)
    raw_config["parameters"]["points"][0]["smoothing"]["enabled"] = False
    raw_config["parameters"]["points"][0]["exclusion"].update(
        {
            "method": "robust_deviation",
            "baseline": 16.075405,
            "deviation_threshold": 0.285786,
        }
    )

    events = Rule(
        FakeDataClient({"PI-2311001/AI1/PV.CV": history}),
        raw_config,
    ).evaluate(START, END)

    low_events = [
        event for event in events if event.data["event_type"] == "viscosity_low"
    ]
    assert len(low_events) == 1
    assert low_events[0].start_time == exclusion_end


def test_rule_starts_at_delayed_first_metric_after_disturbance_window():
    history = []
    history_start = START - timedelta(hours=3)
    disturbance_start = START + timedelta(minutes=30)
    exclusion_end = disturbance_start + timedelta(hours=2)
    first_clean = exclusion_end + timedelta(minutes=5)
    low_end = first_clean + timedelta(minutes=20)

    for index in range(0, 7 * 60):
        timestamp = history_start + timedelta(minutes=index)
        if timestamp == disturbance_start:
            value = "16.75"
        elif exclusion_end <= timestamp < first_clean:
            continue
        elif first_clean <= timestamp < low_end:
            value = "15.85"
        else:
            value = "16.05"
        history.append(make_history_sample(timestamp, value))

    raw_config = config(exclusion_enabled=True)
    raw_config["parameters"]["points"][0]["smoothing"]["enabled"] = False

    events = Rule(
        FakeDataClient({"PI-2311001/AI1/PV.CV": history}),
        raw_config,
    ).evaluate(START, END)

    low_events = [
        event for event in events if event.data["event_type"] == "viscosity_low"
    ]
    assert len(low_events) == 1
    assert low_events[0].start_time == first_clean


def test_rule_can_start_post_disturbance_event_across_shift_boundary():
    history = []
    history_start = START - timedelta(hours=3)
    disturbance_start = START - timedelta(minutes=30)
    exclusion_end = disturbance_start + timedelta(hours=2)
    low_end = exclusion_end + timedelta(minutes=20)

    for index in range(0, 7 * 60):
        timestamp = history_start + timedelta(minutes=index)
        if timestamp == disturbance_start:
            value = "16.75"
        elif exclusion_end <= timestamp < low_end:
            value = "15.85"
        else:
            value = "16.05"
        history.append(make_history_sample(timestamp, value))

    raw_config = config(exclusion_enabled=True)
    raw_config["parameters"]["points"][0]["smoothing"]["enabled"] = False

    events = Rule(
        FakeDataClient({"PI-2311001/AI1/PV.CV": history}),
        raw_config,
    ).evaluate(START, END)

    low_events = [
        event for event in events if event.data["event_type"] == "viscosity_low"
    ]
    assert len(low_events) == 1
    assert low_events[0].start_time == exclusion_end
    assert low_events[0].start_time >= START


def test_rule_keeps_unknown_semantics_after_an_ordinary_data_gap():
    history = []
    for index in range(0, 35):
        timestamp = START + timedelta(minutes=index)
        if index == 1:
            continue
        value = "15.85" if 2 <= index < 22 else "16.05"
        history.append(make_history_sample(timestamp, value))

    raw_config = config()
    raw_config["parameters"]["points"][0]["smoothing"]["enabled"] = False

    events = Rule(
        FakeDataClient({"PI-2311001/AI1/PV.CV": history}),
        raw_config,
    ).evaluate(START, END)

    assert events == []

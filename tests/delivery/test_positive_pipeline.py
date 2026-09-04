import json
from datetime import datetime, timedelta
from pathlib import Path

from dcs_performance.delivery.manager import DeliveryManager
from dcs_performance.engine.loader import LoadedRule
from dcs_performance.rules.analog_limit_exceedance.rule import Rule as AnalogRule
from dcs_performance.rules.component_viscosity_control.rule import Rule as ViscosityRule
from dcs_performance.rules.persistent_high_alarm.rule import Rule as AlarmRule
from dcs_performance.rules.pump_flow_compliance.rule import Rule as PumpRule
from dcs_performance.shifts.model import Shift
from dcs_performance.visualization.loader import VisualizationLoader
from tests.fakes import FakeDataClient, make_history_sample


RULES_DIR = Path(__file__).resolve().parents[2] / "src" / "dcs_performance" / "rules"
SHIFT = Shift(
    "A",
    datetime(2026, 9, 3, 8),
    datetime(2026, 9, 3, 20),
    "day",
)


class SingleRuleLoader:
    def __init__(self, loaded_rule, data_client):
        self.loaded_rule = loaded_rule
        self.data_client = data_client

    def load_enabled(self):
        return [self.loaded_rule]


class RecordingVisualizationLoader:
    def __init__(self):
        self.delegate = VisualizationLoader(RULES_DIR)
        self.contexts = []

    def load(self, rule_id):
        delegate = self.delegate.load(rule_id)
        contexts = self.contexts

        class RecordingVisualizer:
            def render_point(self, context, output_path):
                contexts.append(context)
                return delegate.render_point(context, output_path)

        return RecordingVisualizer()


def _sample(timestamp, value, sequence_no=1):
    return make_history_sample(timestamp, str(value), sequence_no)


def _deliver(tmp_path, rule_class, config, histories):
    client = FakeDataClient(histories)
    loaded = LoadedRule(rule_class(client, config), config)
    visualizers = RecordingVisualizationLoader()
    result = DeliveryManager(
        loader=SingleRuleLoader(loaded, client),
        data_client=client,
        visualization_loader=visualizers,
    ).deliver(SHIFT, tmp_path)
    document = json.loads(result.result_json_path.read_text(encoding="utf-8"))
    return result, document, visualizers.contexts


def _pump_config():
    return {
        "id": "pump_flow_compliance",
        "name": "Pump flow",
        "enabled": True,
        "assessment_window": {"start_offset_minutes": 0, "end_offset_minutes": 0},
        "parameters": {"points": [{
            "id": "PUMP",
            "pump_a_tag": "A",
            "pump_b_tag": "B",
            "flow_tag": "FLOW",
            "running_value": "1",
            "normal_min_flow": 125,
            "switching_min_flow": 100,
            "max_switch_duration_seconds": 600,
        }]},
        "scoring": {"by_event_type": {"low_flow": 2}},
    }


def _assert_positive_result(result, document, contexts, event_type, score, start, end):
    point = document["rules"][0]["points"][0]
    assert point["status"] == "violation"
    assert point["data_status"] == "ok"
    assert point["event_count"] == 1
    assert point["score"] == score
    assert len(contexts) == 1
    assert len(contexts[0].events) == 1

    serialized = point["events"][0]
    assigned = contexts[0].events[0]
    assert serialized["event_type"] == event_type
    assert serialized["start"] == assigned.event_start.isoformat() == start.isoformat()
    assert serialized["end"] == assigned.event_end.isoformat() == end.isoformat()
    assert serialized["score"] == assigned.score == score
    assert serialized["data"]["point_id"] == assigned.data["point_id"] == point["point_id"]
    image = result.package_path / point["image"]
    assert image.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_analog_violation_flows_from_real_detector_to_json_and_png(tmp_path):
    tag = "ANALOG"
    event_start = SHIFT.start_time + timedelta(minutes=10)
    event_end = SHIFT.start_time + timedelta(minutes=16)
    config = {
        "id": "analog_limit_exceedance",
        "name": "Analog limit",
        "enabled": True,
        "assessment_window": {"start_offset_minutes": 0, "end_offset_minutes": 0},
        "parameters": {"points": [{
            "id": "P",
            "history_tag": tag,
            "low": {"enabled": True, "limit": 80, "min_duration_seconds": 300, "merge_gap_seconds": 20},
            "high": {"enabled": True, "limit": 120, "min_duration_seconds": 300, "merge_gap_seconds": 20},
        }]},
        "scoring": {"by_point_event_type": {"P": {"high_limit": 2}}},
    }
    result, document, contexts = _deliver(
        tmp_path,
        AnalogRule,
        config,
        {tag: [
            _sample(SHIFT.start_time - timedelta(minutes=1), 100),
            _sample(event_start, 121),
            _sample(event_end, 100),
        ]},
    )
    _assert_positive_result(
        result, document, contexts, "high_limit", 2.0, event_start, event_end
    )


def test_viscosity_repeated_penalties_flow_to_json_score_and_visual_metadata(tmp_path):
    tag = "VISCOSITY"
    event_start = SHIFT.start_time + timedelta(hours=1)
    event_end = event_start + timedelta(hours=2)
    config = {
        "id": "component_viscosity_control",
        "name": "Component viscosity",
        "enabled": True,
        "assessment_window": {"start_offset_minutes": 0, "end_offset_minutes": 0},
        "parameters": {
            "points": [{
                "id": "PI-2311001",
                "history_tag": tag,
                "aggregation": {"method": "median", "bucket_seconds": 60, "min_samples": 1},
                "smoothing": {"enabled": False, "method": "trailing_mean", "window_seconds": 600, "min_samples": 10},
                "assessment": {
                    "target": 16.05,
                    "low_limit": 15.95,
                    "high_limit": 16.25,
                    "min_duration_seconds": 600,
                    "merge_gap_seconds": 600,
                    "repeat_penalty": {
                        "enabled": True,
                        "interval_seconds": 1800,
                        "max_units": None,
                    },
                },
                "exclusion": {"enabled": False},
            }]
        },
        "scoring": {
            "by_point": {
                "PI-2311001": {"viscosity_low": 2, "viscosity_high": 2}
            }
        },
    }
    history = [
        _sample(SHIFT.start_time - timedelta(minutes=1), 16.05),
        *(_sample(SHIFT.start_time + timedelta(minutes=index), 16.05)
          for index in range(1, 60)),
        *(_sample(event_start + timedelta(minutes=index), 15.85)
          for index in range(120)),
        _sample(event_end, 16.05),
    ]

    result, document, contexts = _deliver(
        tmp_path,
        ViscosityRule,
        config,
        {tag: history},
    )

    _assert_positive_result(
        result, document, contexts, "viscosity_low", 8.0, event_start, event_end
    )
    point = document["rules"][0]["points"][0]
    assert point["events"][0]["data"]["penalty"]["units"] == 4
    assert point["events"][0]["data"]["score_multiplier"] == 4
    assert point["events"][0]["data"]["base_score"] == 2
    assert point["metadata"]["penalty_unit_count"] == 4
    assert point["metadata"]["penalty_checkpoint_count"] == 4


def test_persistent_alarm_flows_from_real_detector_to_json_and_png(tmp_path):
    tag = "ALARM"
    event_start = SHIFT.start_time + timedelta(hours=1)
    event_end = event_start + timedelta(minutes=6)
    config = {
        "id": "persistent_high_alarm",
        "name": "Persistent alarm",
        "enabled": True,
        "assessment_window": {"start_offset_minutes": 0, "end_offset_minutes": 0},
        "parameters": {
            "active_value": "1",
            "threshold_seconds": 300,
            "recovery_search_hours": 1,
            "points": [{"id": "LA", "history_tag": tag}],
        },
        "scoring": {"by_point": {"LA": 3}},
    }
    result, document, contexts = _deliver(
        tmp_path,
        AlarmRule,
        config,
        {tag: [
            _sample(SHIFT.start_time - timedelta(minutes=1), 0),
            _sample(event_start, 1),
            _sample(event_end, 0),
        ]},
    )
    _assert_positive_result(
        result,
        document,
        contexts,
        "persistent_high_alarm",
        3.0,
        event_start,
        event_end,
    )


def test_pump_violation_flows_from_real_detector_to_json_and_png(tmp_path):
    event_start = SHIFT.start_time
    event_end = event_start + timedelta(minutes=5)
    previous = SHIFT.start_time - timedelta(minutes=20)
    result, document, contexts = _deliver(
        tmp_path,
        PumpRule,
        _pump_config(),
        {
            "A": [_sample(previous, 1)],
            "B": [_sample(previous, 0)],
            "FLOW": [
                _sample(previous, 130),
                _sample(event_start, 90),
                _sample(event_end, 130),
            ],
        },
    )
    _assert_positive_result(
        result, document, contexts, "low_flow", 2.0, event_start, event_end
    )


def test_partial_pump_history_is_explicit_in_delivered_json(tmp_path):
    result, document, contexts = _deliver(
        tmp_path,
        PumpRule,
        _pump_config(),
        {"FLOW": [_sample(SHIFT.start_time, 130)]},
    )

    point = document["rules"][0]["points"][0]
    assert point["status"] == "normal"
    assert point["data_status"] == "partial"
    assert point["event_count"] == 0
    assert set(point["metadata"]["missing_tags"]) == {"A", "B"}
    assert contexts[0].events == ()
    assert (result.package_path / point["image"]).is_file()

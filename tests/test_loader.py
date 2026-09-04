import json

import pytest

from dcs_performance.engine.loader import RuleLoadError, RuleLoader

from tests.fakes import FakeDataClient


def test_list_metadata_does_not_require_a_data_client():
    metadata = RuleLoader(data_client=None).list_metadata()

    assert [(item.id, item.enabled) for item in metadata] == [
        ("analog_limit_exceedance", True),
        ("analog_trend_stability", True),
        ("component_viscosity_control", True),
        ("example_rule", True),
        ("flow_balance_compliance", True),
        ("level_rate_compliance", True),
        ("persistent_high_alarm", True),
        ("pump_flow_compliance", True),
    ]
    assert next(
        item for item in metadata if item.id == "persistent_high_alarm"
    ).name == "持续高报考核"


def test_list_metadata_does_not_import_or_construct_rule(tmp_path):
    rules_dir = tmp_path / "rules"
    rule_dir = rules_dir / "metadata_only"
    rule_dir.mkdir(parents=True)
    (rule_dir / "config.json").write_text(
        json.dumps(
            {
                "id": "metadata_only",
                "name": "Metadata only",
                "enabled": True,
            }
        ),
        encoding="utf-8",
    )
    (rule_dir / "rule.py").write_text(
        "raise RuntimeError('metadata listing must not import rule.py')\n",
        encoding="utf-8",
    )

    metadata = RuleLoader(rules_dir=rules_dir).list_metadata()

    assert len(metadata) == 1
    assert metadata[0].id == "metadata_only"
    assert metadata[0].directory == rule_dir


def test_list_metadata_validates_basic_config_fields(tmp_path):
    rules_dir = tmp_path / "rules"
    rule_dir = rules_dir / "bad_rule"
    rule_dir.mkdir(parents=True)
    (rule_dir / "config.json").write_text(
        json.dumps(
            {
                "id": "bad_rule",
                "name": "Bad rule",
                "enabled": "yes",
            }
        ),
        encoding="utf-8",
    )
    (rule_dir / "rule.py").write_text("class Rule: pass\n", encoding="utf-8")

    with pytest.raises(RuleLoadError, match="enabled must be a boolean"):
        RuleLoader(rules_dir=rules_dir).list_metadata()


def test_loading_a_real_rule_still_requires_a_data_client():
    with pytest.raises(RuleLoadError, match="persistent_high_alarm"):
        RuleLoader(data_client=None).load("persistent_high_alarm")

    loaded = RuleLoader(data_client=FakeDataClient()).load("persistent_high_alarm")
    assert loaded.id == "persistent_high_alarm"


def test_loading_a_rule_with_relative_imports():
    loaded = RuleLoader(data_client=FakeDataClient()).load(
        "component_viscosity_control"
    )

    assert loaded.id == "component_viscosity_control"
    assert loaded.enabled is True


def test_production_pump_flow_config_is_scorable():
    loaded = RuleLoader(data_client=FakeDataClient()).load("pump_flow_compliance")

    assert loaded.config["enabled"] is True
    points = {
        point["id"]: point for point in loaded.config["parameters"]["points"]
    }
    assert points == {
        "117P01": {
            "id": "117P01",
            "pump_a_tag": "EU-117P01A/DC1/PV_D.CV",
            "pump_b_tag": "EU-117P01B/DC1/PV_D.CV",
            "flow_tag": "FIA-117079/AI1/PV.CV",
            "running_value": "1",
            "normal_min_flow": 125,
            "switching_min_flow": 100,
            "max_switch_duration_seconds": 600,
        },
        "115P05": {
            "id": "115P05",
            "pump_a_tag": "EU-115P05A/DC1/PV_D.CV",
            "pump_b_tag": "EU-115P05B/DC1/PV_D.CV",
            "flow_tag": "FIA-115174/AI1/PV.CV",
            "running_value": "1",
            "normal_min_flow": 110,
            "switching_min_flow": 100,
            "max_switch_duration_seconds": 600,
        },
        "115P03": {
            "id": "115P03",
            "pump_a_tag": "EU-115P03A/DC1/PV_D.CV",
            "pump_b_tag": "EU-115P03B/DC1/PV_D.CV",
            "flow_tag": "FIA-115074/AI1/PV.CV",
            "running_value": "1",
            "normal_min_flow": 110,
            "switching_min_flow": 100,
            "max_switch_duration_seconds": 600,
        },
        "217P01": {
            "id": "217P01",
            "pump_a_tag": "EU-217P01A/DC1/PV_D.CV",
            "pump_b_tag": "EU-217P01B/DC1/PV_D.CV",
            "flow_tag": "FIA-217079/AI1/PV.CV",
            "running_value": "1",
            "normal_min_flow": 110,
            "switching_min_flow": 100,
            "max_switch_duration_seconds": 600,
        },
        "215P05": {
            "id": "215P05",
            "pump_a_tag": "EU-215P05A/DC1/PV_D.CV",
            "pump_b_tag": "EU-215P05B/DC1/PV_D.CV",
            "flow_tag": "FIA-215174/AI1/PV.CV",
            "running_value": "1",
            "normal_min_flow": 110,
            "switching_min_flow": 100,
            "max_switch_duration_seconds": 600,
        },
        "215P03": {
            "id": "215P03",
            "pump_a_tag": "EU-215P03A/DC1/PV_D.CV",
            "pump_b_tag": "EU-215P03B/DC1/PV_D.CV",
            "flow_tag": "FIA-215074/AI1/PV.CV",
            "running_value": "1",
            "normal_min_flow": 110,
            "switching_min_flow": 100,
            "max_switch_duration_seconds": 600,
        },
    }
    assert loaded.config["scoring"] == {
        "default_score_per_event": 1,
        "by_event_type": {"low_flow": 1, "switch_timeout": 2},
    }


def test_directional_rules_use_point_score_keys():
    expected = {
        "flow_balance_compliance": {
            "SLURRY_FLOW_BALANCE": {"flow_low": 2, "flow_high": 2}
        },
        "level_rate_compliance": {
            "LICA-012019": {"rate_down": 2, "rate_up": 2}
        },
    }

    for rule_id, point_scores in expected.items():
        loaded = RuleLoader(data_client=FakeDataClient()).load(rule_id)

        assert loaded.config["scoring"]["by_point"] == point_scores
        assert "by_point_event_type" not in loaded.config["scoring"]


def test_production_point_ranges_scores_and_component_window_match_process_request():
    loader = RuleLoader(data_client=FakeDataClient())
    analog = loader.load("analog_limit_exceedance").config
    points = {
        point["id"]: point for point in analog["parameters"]["points"]
    }
    scores = analog["scoring"]["by_point_event_type"]

    assert len(points) == 20
    assert (points["LICA-012019"]["low"]["limit"], points["LICA-012019"]["high"]["limit"]) == (71.0, 73.0)
    assert (points["LIC-017149"]["low"]["limit"], points["LIC-017149"]["high"]["limit"]) == (36.0, 44.0)
    assert scores["LICA-012019"] == {"low_limit": 3, "high_limit": 3}
    assert scores["LIC-013107"] == {"low_limit": 3, "high_limit": 3}
    assert scores["LIC-017149"] == {"low_limit": 3, "high_limit": 3}
    for point_id in ("LIC-117016", "LIC-217016", "TIC-013060", "LIC-013065"):
        assert scores[point_id] == {"low_limit": 2, "high_limit": 2}
    for point_id in ("TIC-012022", "TIC-015009", "TIC-117001", "TIC-117117", "TIC-217001"):
        assert scores[point_id] == {"low_limit": 1, "high_limit": 1}
    for point_id in ("TI-011003", "VIT-118020"):
        assert scores[point_id] == {"low_limit": 3, "high_limit": 3}
    for point_id, tag, low, high in (
        ("EU-II-217R011", "EU-II-217R011/AI1/PV.CV", 82.5, 85.5),
        ("EU-II-117R011", "EU-II-117R011/AI1/PV.CV", 81.5, 84.5),
    ):
        assert points[point_id]["history_tag"] == tag
        assert (points[point_id]["low"]["limit"], points[point_id]["high"]["limit"]) == (
            low,
            high,
        )
        assert points[point_id]["assessment_window"] == {
            "start_offset_minutes": 240,
            "end_offset_minutes": 0,
        }
        assert points[point_id]["max_events_per_window"] == 1
        assert scores[point_id] == {"low_limit": 2, "high_limit": 2}

    assert points["WIC-011006"]["history_tag"] == "WIC-011006/PID1/PV.CV"
    assert (
        points["WIC-011006"]["low"]["limit"],
        points["WIC-011006"]["high"]["limit"],
    ) == (69.4, 69.6)
    assert points["WIC-011006"]["low"]["min_duration_seconds"] == 300
    assert points["WIC-011006"]["high"]["min_duration_seconds"] == 300
    assert scores["WIC-011006"] == {"low_limit": 2, "high_limit": 2}
    assert points["LIC-011007"]["history_tag"] == "LICA-011007/PID1/PV.CV"
    assert (
        points["LIC-011007"]["low"]["limit"],
        points["LIC-011007"]["high"]["limit"],
    ) == (83.0, 85.0)
    assert points["LIC-011007"]["low"]["min_duration_seconds"] == 300
    assert points["LIC-011007"]["high"]["min_duration_seconds"] == 300
    assert scores["LIC-011007"] == {"low_limit": 2, "high_limit": 2}

    component = loader.load("component_viscosity_control").config
    component_point = component["parameters"]["points"][0]
    assert component_point["assessment_window"] == {
        "start_offset_minutes": 60,
        "end_offset_minutes": 60,
    }
    assert component["scoring"]["by_point"]["PI-2311001"] == {
        "viscosity_low": 2,
        "viscosity_high": 2,
    }
    assert loader.load("flow_balance_compliance").config["scoring"]["by_point"][
        "SLURRY_FLOW_BALANCE"
    ] == {"flow_low": 2, "flow_high": 2}
    assert loader.load("level_rate_compliance").config["scoring"]["by_point"][
        "LICA-012019"
    ] == {"rate_down": 2, "rate_up": 2}

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
    assert loaded.config["scoring"] == {
        "default_score_per_event": 1,
        "by_event_type": {"low_flow": 1, "switch_timeout": 2},
    }


def test_directional_rules_use_point_score_keys():
    expected = {
        "flow_balance_compliance": {
            "SLURRY_FLOW_BALANCE": {"flow_low": 1, "flow_high": 1}
        },
        "level_rate_compliance": {
            "LICA-012019": {"rate_down": 1, "rate_up": 1}
        },
    }

    for rule_id, point_scores in expected.items():
        loaded = RuleLoader(data_client=FakeDataClient()).load(rule_id)

        assert loaded.config["scoring"]["by_point"] == point_scores
        assert "by_point_event_type" not in loaded.config["scoring"]

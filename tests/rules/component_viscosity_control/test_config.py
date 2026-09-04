import json
from pathlib import Path

import pytest

from dcs_performance.rules.component_viscosity_control.config import parse_config


CONFIG_PATH = Path("src/dcs_performance/rules/component_viscosity_control/config.json")


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_parse_component_viscosity_config():
    parsed = parse_config(load_config())

    assert parsed.id == "component_viscosity_control"
    assert parsed.enabled is True
    point = parsed.points[0]
    assert point.history_tag == "PI-2311001/AI1/PV.CV"
    assert point.aggregation.bucket_seconds == 60
    assert not hasattr(point.aggregation, "enabled")
    assert point.smoothing.window_seconds == 600
    assert point.smoothing.min_samples == 10
    assert point.assessment.target == 16.05
    assert point.assessment.low_limit == 15.95
    assert point.assessment.high_limit == 16.25
    assert point.assessment.min_duration_seconds == 600
    assert point.assessment.merge_gap_seconds == 600
    assert point.assessment.repeat_penalty.enabled is True
    assert point.assessment.repeat_penalty.interval_seconds == 1800
    assert point.assessment.repeat_penalty.max_units is None
    assert point.exclusion.method == "rolling_range"
    assert point.exclusion.window_seconds == 3600
    assert point.exclusion.range_threshold == 1.0
    assert point.exclusion.remove_after_start_seconds == 7200


def test_legacy_aggregation_enabled_flag_is_not_a_pipeline_switch():
    raw = load_config()
    raw["parameters"]["points"][0]["aggregation"]["enabled"] = False

    parsed = parse_config(raw)

    assert not hasattr(parsed.points[0].aggregation, "enabled")


def test_missing_repeat_penalty_keeps_legacy_single_unit_behavior():
    raw = load_config()
    raw["parameters"]["points"][0]["assessment"].pop("repeat_penalty")

    parsed = parse_config(raw)

    assert parsed.points[0].assessment.repeat_penalty.enabled is False
    assert parsed.points[0].assessment.repeat_penalty.interval_seconds is None
    assert parsed.points[0].assessment.repeat_penalty.max_units is None


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"enabled": True}, "interval_seconds is required"),
        ({"enabled": True, "interval_seconds": 0}, "interval_seconds"),
        ({"enabled": True, "interval_seconds": -1800}, "interval_seconds"),
        ({"enabled": True, "interval_seconds": True}, "interval_seconds"),
        ({"enabled": False, "interval_seconds": 1800.0}, "interval_seconds"),
        ({"enabled": False, "max_units": 0}, "max_units"),
        ({"enabled": False, "max_units": 2.5}, "max_units"),
        ({"enabled": "yes", "interval_seconds": 1800}, "enabled"),
    ],
)
def test_repeat_penalty_rejects_invalid_values(value, message):
    raw = load_config()
    raw["parameters"]["points"][0]["assessment"]["repeat_penalty"] = value

    with pytest.raises(ValueError, match=message):
        parse_config(raw)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("id", "wrong", "config id"),
        ("name", "", "name"),
        ("enabled", "yes", "enabled must be a boolean"),
    ],
)
def test_parse_rejects_invalid_top_level_values(field, value, message):
    raw = load_config()
    raw[field] = value

    with pytest.raises(ValueError, match=message):
        parse_config(raw)


def test_parse_rejects_non_bucket_aligned_smoothing_window():
    raw = load_config()
    raw["parameters"]["points"][0]["smoothing"]["window_seconds"] = 601

    with pytest.raises(ValueError, match="positive bucket multiple|multiple"):
        parse_config(raw)

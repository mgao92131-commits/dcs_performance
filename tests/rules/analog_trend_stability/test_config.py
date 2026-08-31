from copy import deepcopy

import pytest

from dcs_performance.engine.loader import RuleLoadError
from dcs_performance.rules.analog_trend_stability.config import (
    DriftConfig,
    PointConfig,
    TrendConfig,
    parse_config,
)
from dcs_performance.rules.analog_trend_stability.rule import Rule

from tests.fakes import FakeDataClient


def point(
    point_id="A",
    tag="TAG-A",
    *,
    trend_window=1800,
    alignment="centered",
    min_samples=3,
    stability=None,
    drift=None,
):
    value = {
        "id": point_id,
        "history_tag": tag,
        "enabled": True,
        "quality": {"max_gap_seconds": 60},
        "trend": {
            "method": "rolling_mean",
            "alignment": alignment,
            "window_seconds": trend_window,
            "min_samples": min_samples,
        },
    }
    if stability is not None:
        value["stability"] = stability
    if drift is not None:
        value["drift"] = drift
    return value


def config(points):
    return {
        "id": "analog_trend_stability",
        "name": "连续量趋势稳定性考核",
        "enabled": True,
        "assessment_window": {
            "start_offset_minutes": 0,
            "end_offset_minutes": 0,
        },
        "parameters": {"points": points},
        "scoring": {"default_score_per_event": 1},
    }


def enabled_stability():
    return {
        "enabled": True,
        "warning_deviation": 0.08,
        "high_deviation": 0.10,
        "min_duration_seconds": 60,
        "merge_gap_seconds": 20,
    }


def enabled_drift():
    return {
        "enabled": True,
        "merge_gap_seconds": 60,
        "windows": [
            {
                "id": "short",
                "window_seconds": 1800,
                "warning_change": 0.15,
                "high_change": 0.20,
                "min_duration_seconds": 300,
            }
        ],
    }


def test_each_point_has_independent_trend_configuration():
    parsed = parse_config(
        config(
            [
                point("A", trend_window=1800),
                point("B", tag="TAG-B", trend_window=600, min_samples=2),
            ]
        )
    )

    assert [item.trend.window_seconds for item in parsed.points] == [1800, 600]
    assert [item.trend.min_samples for item in parsed.points] == [3, 2]
    assert isinstance(parsed.points[0], PointConfig)
    assert isinstance(parsed.points[0].trend, TrendConfig)
    with pytest.raises(Exception):
        parsed.points[0].trend.window_seconds = 10


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["parameters"]["points"].append(point("A")),
        lambda value: value["parameters"]["points"].__setitem__(0, {"id": "A", "history_tag": ""}),
        lambda value: value["parameters"]["points"][0]["trend"].__setitem__("window_seconds", 0),
        lambda value: value["parameters"]["points"][0]["trend"].__setitem__("alignment", "bad"),
        lambda value: value["parameters"]["points"][0]["stability"].__setitem__("high_deviation", 0.08),
        lambda value: value["parameters"]["points"][0]["drift"]["windows"].append(
            {
                "id": "short",
                "window_seconds": 3600,
                "warning_change": 0.2,
                "high_change": 0.3,
                "min_duration_seconds": 60,
            }
        ),
    ],
)
def test_invalid_configuration_fails_fast_as_rule_load_error(mutate):
    raw = config(
        [
            point(
                stability=enabled_stability(),
                drift=enabled_drift(),
            )
        ]
    )
    if mutate is not None:
        if "points" not in raw["parameters"]:
            raise AssertionError("test setup error")
        if mutate.__name__ == "<lambda>":
            pass
    mutate(raw)

    with pytest.raises(RuleLoadError):
        Rule(FakeDataClient(), raw)


def test_disabled_stability_and_drift_are_valid_and_default_to_no_analysis():
    parsed = parse_config(
        config(
            [
                point(
                    stability={"enabled": False},
                    drift={"enabled": False, "windows": []},
                )
            ]
        )
    )
    assert parsed.points[0].stability.enabled is False
    assert parsed.points[0].drift == DriftConfig()


def test_a_point_can_have_only_stability_or_only_drift():
    only_stability = parse_config(
        config([point(stability=enabled_stability())])
    ).points[0]
    only_drift = parse_config(config([point(drift=enabled_drift())])).points[0]

    assert only_stability.stability.enabled is True
    assert only_stability.drift.enabled is False
    assert only_drift.stability.enabled is False
    assert only_drift.drift.enabled is True


def test_assessment_window_cannot_be_used_as_trend_preheat():
    raw = config([point()])
    raw["assessment_window"]["start_offset_minutes"] = -60

    with pytest.raises(RuleLoadError, match="offsets"):
        Rule(FakeDataClient(), raw)

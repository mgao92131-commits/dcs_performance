from copy import deepcopy
from math import inf, nan

import pytest

from dcs_performance.rules.analog_limit_exceedance.config import (
    AnalogLimitExceedanceConfig,
    LimitSideConfig,
    PointConfig,
    parse_config,
)


def point(
    point_id="A",
    tag="TAG-A",
    *,
    enabled=True,
    low=None,
    high=None,
):
    return {
        "id": point_id,
        "history_tag": tag,
        "enabled": enabled,
        "low": low
        or {
            "enabled": True,
            "limit": 80.0,
            "min_duration_seconds": 300,
            "merge_gap_seconds": 20,
        },
        "high": high
        or {
            "enabled": True,
            "limit": 120.0,
            "min_duration_seconds": 300,
            "merge_gap_seconds": 20,
        },
    }


def config(points):
    return {
        "id": "analog_limit_exceedance",
        "name": "连续量上下限超限考核",
        "enabled": True,
        "assessment_window": {
            "start_offset_minutes": 0,
            "end_offset_minutes": 0,
        },
        "parameters": {"points": points},
        "scoring": {
            "default_score_per_event": 1,
            "by_point_event_type": {"A": {"low_limit": 1, "high_limit": 2}},
        },
    }


def test_parse_valid_config():
    parsed = parse_config(config([point()]))

    assert isinstance(parsed, AnalogLimitExceedanceConfig)
    assert isinstance(parsed.points[0], PointConfig)
    assert isinstance(parsed.points[0].low, LimitSideConfig)
    assert parsed.id == "analog_limit_exceedance"
    assert parsed.points[0].low.limit == 80.0
    assert parsed.points[0].high.min_duration_seconds == 300.0


def test_each_point_keeps_independent_limits():
    parsed = parse_config(
        config(
            [
                point("A", "TAG-A"),
                point(
                    "B",
                    "TAG-B",
                    low={
                        "enabled": True,
                        "limit": 10,
                        "min_duration_seconds": 60,
                        "merge_gap_seconds": 5,
                    },
                    high={
                        "enabled": True,
                        "limit": 20,
                        "min_duration_seconds": 90,
                        "merge_gap_seconds": 10,
                    },
                ),
            ]
        )
    )

    assert parsed.points[0].low.limit == 80.0
    assert parsed.points[1].low.limit == 10.0
    assert parsed.points[1].high.min_duration_seconds == 90.0
    with pytest.raises(Exception):
        parsed.points[0].low = parsed.points[0].low


def test_point_can_enable_low_only():
    parsed = parse_config(
        config(
            [
                point(
                    low={
                        "enabled": True,
                        "limit": 80,
                        "min_duration_seconds": 300,
                        "merge_gap_seconds": 20,
                    },
                    high={"enabled": False},
                )
            ]
        )
    )

    assert parsed.points[0].low.enabled is True
    assert parsed.points[0].high.enabled is False


def test_point_can_enable_high_only():
    parsed = parse_config(
        config(
            [
                point(
                    low={"enabled": False},
                    high={
                        "enabled": True,
                        "limit": 120,
                        "min_duration_seconds": 300,
                        "merge_gap_seconds": 20,
                    },
                )
            ]
        )
    )

    assert parsed.points[0].low.enabled is False
    assert parsed.points[0].high.enabled is True


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda value: value.update(id="wrong"), "config id"),
        (lambda value: value["parameters"].update(points=[]), "points"),
        (
            lambda value: value["parameters"]["points"].append(point("A")),
            "duplicate .*point id",
        ),
        (lambda value: value["parameters"]["points"][1].update(history_tag="TAG-A"), "history_tag"),
        (
            lambda value: value["parameters"]["points"][0].update(
                low={"enabled": False}, high={"enabled": False}
            ),
            "at least one",
        ),
        (
            lambda value: value["parameters"]["points"][0]["low"].update(limit=120),
            "less than",
        ),
        (
            lambda value: value["parameters"]["points"][0]["low"].update(
                min_duration_seconds=0
            ),
            "greater than zero",
        ),
        (
            lambda value: value["parameters"]["points"][0]["low"].update(
                min_duration_seconds=-1
            ),
            "greater than zero",
        ),
        (
            lambda value: value["parameters"]["points"][0]["low"].update(
                merge_gap_seconds=-1
            ),
            "greater than or equal",
        ),
        (
            lambda value: value["parameters"]["points"][0]["low"].update(
                limit=nan
            ),
            "finite",
        ),
        (
            lambda value: value["parameters"]["points"][0]["low"].update(
                limit=inf
            ),
            "finite",
        ),
        (
            lambda value: value["parameters"]["points"][0]["low"].update(
                limit=True
            ),
            "finite",
        ),
    ],
)
def test_rejects_invalid_configuration(mutate, message):
    raw = config([point(), point("B", "TAG-B")])
    mutate(raw)

    with pytest.raises(ValueError, match=message):
        parse_config(raw)


def test_rejects_empty_point_id_and_history_tag():
    raw = config([point()])
    raw["parameters"]["points"][0]["id"] = ""
    with pytest.raises(ValueError, match="id"):
        parse_config(raw)

    raw = config([point()])
    raw["parameters"]["points"][0]["history_tag"] = ""
    with pytest.raises(ValueError, match="history_tag"):
        parse_config(raw)


def test_rejects_non_boolean_point_enabled():
    raw = config([point()])
    raw["parameters"]["points"][0]["enabled"] = "yes"

    with pytest.raises(ValueError, match="enabled"):
        parse_config(raw)


def test_validate_config_is_immutable_at_nested_mapping_boundary():
    raw = config([point()])
    parsed = parse_config(raw)

    with pytest.raises(TypeError):
        parsed.scoring["default_score_per_event"] = 2
    assert raw["scoring"]["default_score_per_event"] == 1


def test_parse_config_does_not_mutate_input():
    raw = config([point()])
    original = deepcopy(raw)

    parse_config(raw)

    assert raw == original

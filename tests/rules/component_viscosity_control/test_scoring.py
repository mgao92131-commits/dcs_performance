from datetime import datetime, timedelta

import pytest

from dcs_performance.rules.component_viscosity_control.config import RepeatPenaltyConfig
from dcs_performance.rules.component_viscosity_control.scoring import (
    calculate_penalty_checkpoints,
    calculate_penalty_units,
)


START = datetime(2026, 9, 3, 22)
POLICY = RepeatPenaltyConfig(enabled=True, interval_seconds=1800, max_units=None)


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (599, 0),
        (600, 1),
        (2399, 1),
        (2400, 2),
        (4199, 2),
        (4200, 3),
    ],
)
def test_penalty_units_use_complete_threshold_boundaries(duration, expected):
    assert calculate_penalty_units(duration, 600, POLICY) == expected


def test_disabled_policy_preserves_one_unit_after_initial_threshold():
    disabled = RepeatPenaltyConfig()

    assert calculate_penalty_units(599, 600, disabled) == 0
    assert calculate_penalty_units(4 * 3600, 600, disabled) == 1


def test_max_units_includes_the_initial_penalty_unit():
    capped = RepeatPenaltyConfig(enabled=True, interval_seconds=1800, max_units=2)

    assert calculate_penalty_units(4 * 3600, 600, capped) == 2


def test_checkpoints_are_timestamped_and_include_exact_event_end():
    checkpoints = calculate_penalty_checkpoints(START, 15000, 600, POLICY)

    assert len(checkpoints) == 9
    assert checkpoints[0] == START + timedelta(minutes=10)
    assert checkpoints[-1] == START + timedelta(seconds=15000)
    assert checkpoints == [START + timedelta(seconds=600 + index * 1800) for index in range(9)]


def test_disabled_policy_has_only_the_initial_checkpoint():
    checkpoints = calculate_penalty_checkpoints(START, 3600, 600, None)

    assert checkpoints == [START + timedelta(seconds=600)]


@pytest.mark.parametrize(
    ("duration", "minimum", "policy"),
    [
        (-1, 600, POLICY),
        (600, -1, POLICY),
        (float("nan"), 600, POLICY),
        (600, 600, {"enabled": True}),
    ],
)
def test_penalty_calculation_rejects_invalid_inputs(duration, minimum, policy):
    with pytest.raises((ValueError, TypeError)):
        calculate_penalty_units(duration, minimum, policy)

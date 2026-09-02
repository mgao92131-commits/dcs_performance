from datetime import datetime, timedelta

import pytest

from dcs_performance.rules.analog_limit_exceedance.config import SmoothingConfig
from dcs_performance.rules.analog_limit_exceedance.smoothing import (
    smooth_history_samples,
)
from tests.fakes import make_history_sample


BASE = datetime(2026, 9, 1, 8, 0)


def sample(seconds, value, sequence_no=1):
    return make_history_sample(
        BASE + timedelta(seconds=seconds),
        str(value),
        sequence_no=sequence_no,
    )


def test_trailing_mean_uses_only_samples_in_backward_window():
    result = smooth_history_samples(
        [sample(30, 30), sample(0, 0), sample(10, 10), sample(20, 20)],
        SmoothingConfig(
            enabled=True,
            method="trailing_mean",
            window_seconds=20,
            min_samples=1,
        ),
    )

    assert [float(item.value) for item in result] == pytest.approx([0, 5, 10, 20])


def test_trailing_mean_waits_for_minimum_sample_count():
    result = smooth_history_samples(
        [sample(0, 10), sample(10, 20), sample(20, 30)],
        SmoothingConfig(
            enabled=True,
            method="trailing_mean",
            window_seconds=30,
            min_samples=3,
        ),
    )

    assert len(result) == 1
    assert result[0].timestamp == BASE + timedelta(seconds=20)
    assert float(result[0].value) == pytest.approx(20)


def test_disabled_smoothing_preserves_original_values():
    original = [sample(10, 20), sample(0, 10)]

    result = smooth_history_samples(original, SmoothingConfig())

    assert [item.timestamp for item in result] == [BASE, BASE + timedelta(seconds=10)]
    assert [item.value for item in result] == ["10", "20"]

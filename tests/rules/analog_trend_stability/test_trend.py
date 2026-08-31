from datetime import datetime, timedelta

import pytest

from dcs_performance.rules.analog_trend_stability.config import (
    DriftWindowConfig,
    TrendConfig,
)
from dcs_performance.rules.analog_trend_stability.trend import (
    NumericSample,
    calculate_drift,
    calculate_trend,
    rolling_mean,
    split_numeric_segments,
)

from tests.fakes import make_history_sample


BASE = datetime(2026, 8, 31, 10, 0)


def numeric(*values, step_seconds=10):
    return [
        NumericSample(
            BASE + timedelta(seconds=index * step_seconds),
            float(value),
            index + 1,
        )
        for index, value in enumerate(values)
    ]


def test_centered_rolling_mean_uses_real_time_window():
    result = rolling_mean(
        numeric(0, 10, 20),
        window_seconds=20,
        min_samples=3,
        alignment="centered",
    )

    assert len(result) == 1
    assert result[0].timestamp == BASE + timedelta(seconds=10)
    assert result[0].trend == pytest.approx(10)
    assert result[0].deviation == pytest.approx(0)


def test_trailing_rolling_mean_does_not_use_future_samples():
    result = rolling_mean(
        numeric(0, 10, 20),
        window_seconds=20,
        min_samples=3,
        alignment="trailing",
    )

    assert len(result) == 1
    assert result[0].timestamp == BASE + timedelta(seconds=20)
    assert result[0].trend == pytest.approx(10)


def test_trend_config_dispatches_rolling_mean_and_keeps_segment_id():
    result = calculate_trend(
        numeric(1, 1, 1),
        TrendConfig(
            method="rolling_mean",
            alignment="trailing",
            window_seconds=20,
            min_samples=1,
        ),
        segment_id=7,
    )

    assert result[-1].segment_id == 7
    assert result[-1].trend == pytest.approx(1)


def test_drift_interpolates_t_minus_window_without_array_index_assumption():
    trends = calculate_trend(
        numeric(0, 10, 20, 30),
        TrendConfig("rolling_mean", "trailing", 10, 1),
    )
    drift = calculate_drift(
        trends,
        [
            DriftWindowConfig(
                id="15s",
                window_seconds=15,
                warning_change=0,
                high_change=1,
                min_duration_seconds=1,
            )
        ],
    )

    target = next(item for item in drift if item.timestamp == BASE + timedelta(seconds=30))
    assert target.change == pytest.approx(15)


def test_invalid_values_holes_reverse_time_and_long_gaps_form_new_segments():
    samples = [
        make_history_sample(BASE, "1", sequence_no=1),
        make_history_sample(BASE + timedelta(seconds=10), "2", sequence_no=2),
        make_history_sample(BASE + timedelta(seconds=20), "NaN", sequence_no=3),
        make_history_sample(BASE + timedelta(seconds=30), "3", sequence_no=4),
        make_history_sample(BASE + timedelta(seconds=40), "4", sequence_no=5),
        make_history_sample(
            BASE + timedelta(seconds=50),
            "5",
            sequence_no=6,
        ),
        make_history_sample(BASE + timedelta(seconds=40), "6", sequence_no=7),
        make_history_sample(BASE + timedelta(seconds=50), "7", sequence_no=8),
        make_history_sample(BASE + timedelta(minutes=10), "8", sequence_no=9),
    ]
    samples[5] = make_history_sample(
        BASE + timedelta(seconds=50),
        "5",
        sequence_no=6,
    )
    # Mark the fifth sample as a Historian hole; it must not bridge to the
    # next valid sample.
    samples[4] = make_history_sample(BASE + timedelta(seconds=40), "4", sequence_no=5)
    samples[4] = samples[4].__class__(
        **{
            **samples[4].__dict__,
            "is_history_hole": True,
        }
    )

    segments = split_numeric_segments(samples, max_gap_seconds=60)

    assert [[item.value for item in segment] for segment in segments] == [
        [1.0, 2.0],
        [3.0],
        [5.0],
        [6.0, 7.0],
        [8.0],
    ]


def test_trend_window_is_time_based_for_irregular_sampling():
    samples = [
        NumericSample(BASE, 0, 1),
        NumericSample(BASE + timedelta(seconds=5), 10, 2),
        NumericSample(BASE + timedelta(seconds=30), 20, 3),
    ]
    result = rolling_mean(
        samples,
        window_seconds=20,
        min_samples=2,
        alignment="trailing",
    )

    # The two samples at 0 and 5 seconds are outside the 20-second window at
    # t=30; a fixed-point window would incorrectly reuse one of them.
    assert len(result) == 1
    assert result[-1].trend == pytest.approx(5)

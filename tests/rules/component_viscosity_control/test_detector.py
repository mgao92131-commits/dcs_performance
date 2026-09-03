from datetime import datetime, timedelta

from dcs_performance.data.models import HistorySample
from dcs_performance.rules.component_viscosity_control.config import ExclusionConfig
from dcs_performance.rules.component_viscosity_control.detector import (
    MetricPoint,
    aggregate_minute_medians,
    calculate_trailing_mean,
    detect_disturbance_windows,
    split_contiguous_segments,
)


START = datetime(2026, 9, 1, 8, 0)


def sample(timestamp, value, sequence_no=1, **quality):
    return HistorySample(
        timestamp=timestamp,
        value=str(value),
        data_type="Analog",
        delta_v_status=quality.get("delta_v_status", "Good"),
        archive_status=quality.get("archive_status", "HistoryDataIsValid"),
        sequence_no=sequence_no,
        is_history_hole=quality.get("is_history_hole", False),
        is_cr_hole=quality.get("is_cr_hole", False),
        is_manually_deleted=quality.get("is_manually_deleted", False),
        is_manually_inserted=quality.get("is_manually_inserted", False),
    )


def test_aggregate_minute_medians_uses_one_median_per_bucket():
    values = aggregate_minute_medians(
        [
            sample(START + timedelta(seconds=1), 10),
            sample(START + timedelta(seconds=20), 12),
            sample(START + timedelta(seconds=50), 14),
        ]
    )

    assert len(values) == 1
    assert values[0].timestamp == START
    assert values[0].value == 12
    assert values[0].sample_count == 3


def test_quality_flags_exclude_bad_values_without_parsing_them():
    values = aggregate_minute_medians(
        [
            sample(START + timedelta(seconds=1), 10),
            sample(
                START + timedelta(seconds=20),
                "not-a-number",
                is_history_hole=True,
            ),
            sample(START + timedelta(seconds=50), 14),
        ]
    )

    assert [item.value for item in values] == [10, 14]
    assert all(item.sample_count == 1 for item in values)


def test_trailing_mean_requires_complete_consecutive_window():
    minute_values = [
        type("Minute", (), {"timestamp": START + timedelta(minutes=index), "value": float(index), "sample_count": 1})()
        for index in range(10)
    ]

    result = calculate_trailing_mean(
        minute_values,
        bucket_seconds=60,
        window_seconds=600,
        min_samples=10,
    )

    assert len(result) == 1
    assert result[0].timestamp == START + timedelta(minutes=9)
    assert result[0].value == 4.5


def test_trailing_mean_breaks_at_missing_bucket():
    minute_values = [
        type("Minute", (), {"timestamp": START + timedelta(minutes=index), "value": 16.0, "sample_count": 1})()
        for index in list(range(5)) + list(range(6, 16))
    ]

    result = calculate_trailing_mean(
        minute_values,
        bucket_seconds=60,
        window_seconds=600,
        min_samples=10,
    )

    assert [item.timestamp for item in result] == [START + timedelta(minutes=15)]


def test_disturbance_window_merges_short_gap_and_extends_from_start():
    metric = [
        MetricPoint(START + timedelta(minutes=index), 16.5 if index in {2, 3, 5} else 16.0)
        for index in range(7)
    ]
    config = ExclusionConfig(
        enabled=True,
        method="robust_deviation",
        baseline=16.0,
        deviation_threshold=0.2,
        merge_gap_seconds=600,
        remove_after_start_seconds=7200,
    )

    windows = detect_disturbance_windows(metric, config, bucket_seconds=60)

    assert len(windows) == 1
    assert windows[0].core_start == START + timedelta(minutes=2)
    assert windows[0].core_end == START + timedelta(minutes=6)
    assert windows[0].remove_end == START + timedelta(minutes=120 + 2)


def test_rolling_range_detects_one_hour_change_and_removes_two_hours():
    metric = [
        MetricPoint(START + timedelta(minutes=index), 16.0)
        for index in range(59)
    ] + [MetricPoint(START + timedelta(minutes=59), 17.1)]
    config = ExclusionConfig(
        enabled=True,
        method="rolling_range",
        window_seconds=3600,
        range_threshold=1.0,
        merge_gap_seconds=600,
        remove_after_start_seconds=7200,
    )

    windows = detect_disturbance_windows(metric, config, bucket_seconds=60)

    assert len(windows) == 1
    assert windows[0].core_start == START + timedelta(minutes=59)
    assert windows[0].remove_end == START + timedelta(minutes=179)


def test_split_contiguous_segments_does_not_bridge_a_gap():
    metric = [
        MetricPoint(START, 1.0),
        MetricPoint(START + timedelta(minutes=1), 1.0),
        MetricPoint(START + timedelta(minutes=3), 1.0),
    ]

    segments = split_contiguous_segments(metric, bucket_seconds=60)

    assert [[point.timestamp for point in segment] for segment in segments] == [
        [START, START + timedelta(minutes=1)],
        [START + timedelta(minutes=3)],
    ]

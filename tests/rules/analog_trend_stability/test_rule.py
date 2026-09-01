from datetime import datetime, timedelta

import pytest

from dcs_performance.data.errors import DcsHistoryQueryTooLargeError
from dcs_performance.engine.loader import RuleLoader, RuleLoadError
from dcs_performance.rules.analog_trend_stability.rule import QueryPlanner, Rule
from dcs_performance.rules.analog_trend_stability.trend import DriftPoint, TrendPoint

from tests.fakes import FakeDataClient, make_history_sample


START = datetime(2026, 8, 31, 8, 0)
END = datetime(2026, 8, 31, 16, 0)


def point(
    point_id,
    tag,
    *,
    window_seconds=1800,
    alignment="centered",
    warning=0.08,
    high=0.10,
    min_duration=60,
    merge_gap=20,
    stability=True,
    drift=None,
    max_gap_seconds=60,
):
    return {
        "id": point_id,
        "history_tag": tag,
        "enabled": True,
        "quality": {"max_gap_seconds": max_gap_seconds},
        "trend": {
            "method": "rolling_mean",
            "alignment": alignment,
            "window_seconds": window_seconds,
            "min_samples": 1,
        },
        "stability": {
            "enabled": stability,
            "warning_deviation": warning,
            "high_deviation": high,
            "min_duration_seconds": min_duration,
            "merge_gap_seconds": merge_gap,
        },
        "drift": drift or {"enabled": False, "windows": []},
    }


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


def history(start, values, *, step_seconds=10):
    return [
        make_history_sample(
            start + timedelta(seconds=index * step_seconds),
            str(value),
            sequence_no=index + 1,
        )
        for index, value in enumerate(values)
    ]


def test_query_planner_uses_each_point_padding_and_batches_equal_ranges():
    parsed_rule = Rule(
        FakeDataClient(),
        config(
            [
                point("A", "TAG-A", window_seconds=1800),
                point("B", "TAG-B", window_seconds=1800),
                point("C", "TAG-C", window_seconds=600, alignment="trailing"),
            ]
        ),
    )

    groups = QueryPlanner.plan(parsed_rule.points, START, END)

    assert len(groups) == 2
    assert groups[0].tags == ("TAG-A", "TAG-B")
    assert (groups[0].query_start, groups[0].query_end) == (
        START - timedelta(minutes=15),
        END + timedelta(minutes=15),
    )
    assert groups[1].tags == ("TAG-C",)
    assert (groups[1].query_start, groups[1].query_end) == (
        START - timedelta(minutes=10),
        END,
    )


def test_rule_keeps_point_parameters_independent():
    client = FakeDataClient(
        {
            "TAG-A": history(
                START - timedelta(seconds=30),
                [0, 0, 0, 1, 1, 1, 0, 0, 0, 0],
            ),
            "TAG-B": history(
                START - timedelta(seconds=20),
                [0, 0, 5, 5, 0, 0, 0],
            ),
        }
    )
    events = Rule(
        client,
        config(
            [
                point("A", "TAG-A", window_seconds=30, warning=0.08, high=0.5, min_duration=20),
                point("B", "TAG-B", alignment="trailing", window_seconds=10, warning=1.5, high=4, min_duration=5),
            ]
        ),
    ).evaluate(START, START + timedelta(seconds=60))

    assert events
    assert {event.data["point_id"] for event in events} == {"A", "B"}
    assert all(event.data["event_type"] == "stability_deviation" for event in events)
    assert {event.data["trend_window_seconds"] for event in events} == {30.0, 10.0}


def test_rule_splits_events_at_assessment_boundaries_and_keeps_stable_keys():
    data_start = START - timedelta(minutes=40)
    values = [0] * 8 + [4] * 8 + [0] * 8
    client = FakeDataClient(
        {
            "TAG-A": history(data_start, values, step_seconds=300),
        }
    )
    rule = Rule(
        client,
        config(
            [
                point(
                    "A",
                    "TAG-A",
                    window_seconds=600,
                    alignment="trailing",
                    warning=0.5,
                    high=1,
                    min_duration=300,
                    merge_gap=0,
                    max_gap_seconds=600,
                )
            ]
        ),
    )

    current = rule.evaluate(START, END)
    next_window = rule.evaluate(END, END + timedelta(minutes=40))

    assert current
    assert all(event.start_time >= START and event.end_time <= END for event in current)
    assert all(
        event.start_time >= END
        and event.end_time <= END + timedelta(minutes=40)
        for event in next_window
    )
    assert current[0].data["event_key"].startswith(
        "analog_trend_stability:A:stability_deviation:none:"
    )


def test_rule_recomputes_stability_metrics_inside_each_shift_slice():
    shift_boundary = datetime(2026, 8, 31, 16, 0)
    metric_start = shift_boundary - timedelta(minutes=2)
    rule = Rule(
        FakeDataClient(),
        config(
            [
                point(
                    "A",
                    "TAG-A",
                    window_seconds=60,
                    warning=0.08,
                    high=0.10,
                    min_duration=60,
                    drift={"enabled": False, "windows": []},
                )
            ]
        ),
    )
    metric_points = [
        TrendPoint(
            timestamp=metric_start + timedelta(minutes=index),
            pv=0.09 if index < 2 else 0.20,
            trend=0.0,
            deviation=0.09 if index < 2 else 0.20,
            segment_id=0,
        )
        for index in range(5)
    ]

    current_events = rule._stability_events(
        rule.points[0],
        metric_points,
        metric_start,
        shift_boundary,
    )
    next_events = rule._stability_events(
        rule.points[0],
        metric_points,
        shift_boundary,
        shift_boundary + timedelta(minutes=2),
    )

    assert len(current_events) == 1
    assert current_events[0].data["severity"] == "warning"
    assert current_events[0].data["score_key"] == "stability_deviation.warning"
    assert current_events[0].data["max_abs_deviation"] == 0.09
    assert current_events[0].data["mean_abs_deviation"] == 0.09
    assert current_events[0].data["duration_seconds"] == 120.0

    assert len(next_events) == 1
    assert next_events[0].data["severity"] == "high"
    assert next_events[0].data["score_key"] == "stability_deviation.high"
    assert next_events[0].data["max_abs_deviation"] == 0.20
    assert next_events[0].data["mean_abs_deviation"] == 0.20
    assert next_events[0].data["duration_seconds"] == 120.0


def test_rule_recomputes_drift_evidence_inside_each_shift_slice():
    shift_boundary = datetime(2026, 8, 31, 16, 0)
    metric_start = shift_boundary - timedelta(minutes=2)
    rule = Rule(
        FakeDataClient(),
        config(
            [
                point(
                    "A",
                    "TAG-A",
                    stability=False,
                    drift={
                        "enabled": True,
                        "merge_gap_seconds": 0,
                        "windows": [
                            {
                                "id": "short",
                                "window_seconds": 30,
                                "warning_change": 0.15,
                                "high_change": 0.20,
                                "min_duration_seconds": 60,
                            },
                            {
                                "id": "long",
                                "window_seconds": 60,
                                "warning_change": 0.23,
                                "high_change": 0.30,
                                "min_duration_seconds": 60,
                            },
                        ],
                    },
                )
            ]
        ),
    )
    drift_points = [
        drift_point
        for index in range(5)
        for drift_point in (
            DriftPoint(
                timestamp=metric_start + timedelta(minutes=index),
                window_id="short",
                window_seconds=30,
                change=0.16 if index < 2 else 0.25,
                segment_id=0,
            ),
            DriftPoint(
                timestamp=metric_start + timedelta(minutes=index),
                window_id="long",
                window_seconds=60,
                change=0.24 if index < 2 else 0.35,
                segment_id=0,
            ),
        )
    ]

    current_events = rule._drift_events(
        rule.points[0],
        drift_points,
        metric_start,
        shift_boundary,
    )
    next_events = rule._drift_events(
        rule.points[0],
        drift_points,
        shift_boundary,
        shift_boundary + timedelta(minutes=2),
    )

    assert len(current_events) == 1
    assert current_events[0].data["severity"] == "warning"
    assert current_events[0].data["score_key"] == "trend_drift.warning"
    assert current_events[0].data["duration_seconds"] == 120.0
    assert current_events[0].data["evidence"] == [
        {"window_id": "short", "window_seconds": 30.0, "peak_change": 0.16},
        {"window_id": "long", "window_seconds": 60.0, "peak_change": 0.24},
    ]

    assert len(next_events) == 1
    assert next_events[0].data["severity"] == "high"
    assert next_events[0].data["score_key"] == "trend_drift.high"
    assert next_events[0].data["duration_seconds"] == 120.0
    assert next_events[0].data["evidence"] == [
        {"window_id": "short", "window_seconds": 30.0, "peak_change": 0.25},
        {"window_id": "long", "window_seconds": 60.0, "peak_change": 0.35},
    ]


def test_rule_retries_long_history_as_batched_time_slices():
    class TooLargeOnceClient(FakeDataClient):
        def __init__(self, histories):
            super().__init__(histories)
            self.history_batch_calls = 0

        def get_histories(self, tags, start_time, end_time):
            self.history_batch_calls += 1
            if self.history_batch_calls == 1:
                raise DcsHistoryQueryTooLargeError(
                    "History span exceeds MaxHistorySpanHours=24.",
                    code="history_query_too_large",
                )
            return super().get_histories(tags, start_time, end_time)

    client = TooLargeOnceClient(
        {
            "TAG-A": history(START, [0] * 10, step_seconds=10),
        }
    )
    events = Rule(
        client,
        config(
            [
                point(
                    "A",
                    "TAG-A",
                    window_seconds=30,
                    stability=False,
                    drift={"enabled": False, "windows": []},
                )
            ]
        ),
    ).evaluate(START, START + timedelta(days=2))

    assert events == []
    assert client.history_batch_calls == 6


def test_rule_loader_constructs_the_directory_rule():
    loaded = RuleLoader(data_client=FakeDataClient()).load("analog_trend_stability")

    assert loaded.id == "analog_trend_stability"
    assert loaded.name == "连续量趋势稳定性考核"
    assert loaded.enabled is True


def test_rule_loader_surfaces_invalid_configuration_as_rule_load_error(tmp_path):
    # The direct rule constructor is the fail-fast boundary.  This also keeps
    # the test independent of the installed package config.json.
    bad = config([point("A", "TAG-A", window_seconds=0)])
    with pytest.raises(RuleLoadError):
        Rule(FakeDataClient(), bad)

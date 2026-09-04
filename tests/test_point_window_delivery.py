"""Regression tests for point-aware execution and Result Package semantics."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.core.evaluation import (
    EvaluatedAssessmentEvent,
    RuleExecutionResult,
)
from dcs_performance.core.window import TimeRange
from dcs_performance.delivery.manager import DeliveryError, DeliveryManager
from dcs_performance.delivery.models import PointAssessmentResult
from dcs_performance.engine.loader import LoadedRule
from dcs_performance.engine.runner import RuleRunner
from dcs_performance.shifts.model import Shift
from dcs_performance.visualization.models import VisualizationArtifact
from tests.fakes import FakeDataClient


SHIFT = Shift(
    team_id="A",
    shift_type="day",
    start_time=datetime(2026, 9, 3, 8),
    end_time=datetime(2026, 9, 3, 20),
)
PNG = b"\x89PNG\r\n\x1a\npoint-window-test"


class PointAwareRule:
    id = "point_aware_rule"
    name = "Point aware rule"

    def __init__(self) -> None:
        self.calls: list[tuple[datetime, datetime, list[str] | None]] = []

    def evaluate(
        self,
        start_time: datetime,
        end_time: datetime,
        *,
        point_ids: list[str] | None = None,
    ) -> list[AssessmentEvent]:
        selected = None if point_ids is None else list(point_ids)
        self.calls.append((start_time, end_time, selected))
        return [
            AssessmentEvent(
                start_time,
                min(end_time, start_time.replace(hour=start_time.hour + 1)),
                data={"point_id": point_id, "event_type": "test"},
            )
            for point_id in selected or []
        ]


def _point_rule_config(points: list[dict[str, object]]) -> dict[str, object]:
    return {
        "id": "point_aware_rule",
        "name": "Point aware rule",
        "enabled": True,
        "assessment_window": {
            "start_offset_minutes": 0,
            "end_offset_minutes": 0,
        },
        "parameters": {"points": points},
        "scoring": {"default_score_per_event": 1},
    }


def test_runner_merges_points_with_the_same_effective_window():
    rule = PointAwareRule()
    config = _point_rule_config(
        [
            {"id": "A", "assessment_window": {"start_offset_minutes": 30}},
            {"id": "B", "assessment_window": {"start_offset_minutes": 30}},
        ]
    )

    execution = RuleRunner().run_execution(SHIFT, LoadedRule(rule, config))

    assert rule.calls == [
        (datetime(2026, 9, 3, 8, 30), datetime(2026, 9, 3, 20), ["A", "B"]),
    ]
    assert [item.event.data["point_id"] for item in execution.events] == ["A", "B"]
    assert all(
        item.window == execution.window_for_point(item.event.data["point_id"])
        for item in execution.events
    )


def test_runner_groups_different_windows_and_passes_only_group_points():
    rule = PointAwareRule()
    config = _point_rule_config(
        [
            {"id": "A", "assessment_window": {"start_offset_minutes": -10}},
            {
                "id": "B",
                "assessment_window": {
                    "start_offset_minutes": 30,
                    "end_offset_minutes": 10,
                },
            },
            {"id": "C", "assessment_window": {"start_offset_minutes": -10}},
        ]
    )

    execution = RuleRunner().run_execution(SHIFT, LoadedRule(rule, config))

    assert rule.calls == [
        (datetime(2026, 9, 3, 7, 50), datetime(2026, 9, 3, 20), ["A", "C"]),
        (datetime(2026, 9, 3, 8, 30), datetime(2026, 9, 3, 19, 50), ["B"]),
    ]
    assert [item.event.data["point_id"] for item in execution.events] == [
        "A",
        "C",
        "B",
    ]


def _analog_point(point_id: str, tag: str, *, enabled: bool = True) -> dict[str, object]:
    return {
        "id": point_id,
        "history_tag": tag,
        "enabled": enabled,
        "low": {
            "enabled": True,
            "limit": 80,
            "min_duration_seconds": 300,
            "merge_gap_seconds": 20,
        },
        "high": {
            "enabled": True,
            "limit": 120,
            "min_duration_seconds": 300,
            "merge_gap_seconds": 20,
        },
    }


def _analog_config(points: list[dict[str, object]]) -> dict[str, object]:
    return {
        "id": "analog_limit_exceedance",
        "name": "Analog limits",
        "enabled": True,
        "assessment_window": {"start_offset_minutes": 0},
        "parameters": {"points": points},
        "scoring": {"default_score_per_event": 1},
    }


def test_point_subset_reads_only_the_selected_point_tag():
    from dcs_performance.rules.analog_limit_exceedance.rule import Rule

    client = FakeDataClient()
    rule = Rule(
        client,
        _analog_config(
            [
                _analog_point("A", "TAG-A"),
                _analog_point("B", "TAG-B"),
                _analog_point("C", "TAG-C"),
            ]
        ),
    )

    assert rule.evaluate(SHIFT.start_time, SHIFT.end_time, point_ids=[]) == []
    assert client.calls == []

    rule.evaluate(SHIFT.start_time, SHIFT.end_time, point_ids=["B"])

    assert client.calls
    assert {call[0] for call in client.calls} == {"TAG-B"}


def test_point_subset_rejects_unknown_and_disabled_ids():
    from dcs_performance.rules.analog_limit_exceedance.rule import Rule

    rule = Rule(
        FakeDataClient(),
        _analog_config(
            [
                _analog_point("A", "TAG-A"),
                _analog_point("OFF", "TAG-OFF", enabled=False),
            ]
        ),
    )

    with pytest.raises(ValueError, match="unknown point_id 'UNKNOWN'.*analog_limit_exceedance"):
        rule.evaluate(SHIFT.start_time, SHIFT.end_time, point_ids=["UNKNOWN"])
    with pytest.raises(ValueError, match="disabled point_id 'OFF'.*analog_limit_exceedance"):
        rule.evaluate(SHIFT.start_time, SHIFT.end_time, point_ids=["OFF"])


class _Loader:
    def __init__(self, loaded: list[LoadedRule], data_client: FakeDataClient) -> None:
        self.loaded = loaded
        self.data_client = data_client

    def load_enabled(self) -> list[LoadedRule]:
        return self.loaded


class _Visualizer:
    def __init__(self, statuses: dict[str, str] | None = None) -> None:
        self.statuses = statuses or {}

    def render_point(self, context, output_path):
        output_path.write_bytes(PNG)
        return VisualizationArtifact(
            f"images/{output_path.name}",
            self.statuses.get(context.point_id, "ok"),
        )


class _Visualizers:
    def __init__(self, visualizer: _Visualizer) -> None:
        self.visualizer = visualizer

    def load(self, rule_id: str) -> _Visualizer:
        return self.visualizer


class _EmptyRule:
    id = "quality_rule"
    name = "Quality rule"

    def evaluate(self, start_time: datetime, end_time: datetime):
        return []


def _quality_config(point_ids: list[str]) -> dict[str, object]:
    return {
        "id": "quality_rule",
        "name": "Quality rule",
        "enabled": True,
        "parameters": {"points": [{"id": point_id} for point_id in point_ids]},
        "scoring": {"default_score_per_event": 1},
    }


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ({"A": "ok", "B": "ok"}, (2, 0, 0, True)),
        ({"A": "ok", "B": "partial"}, (1, 1, 0, False)),
        ({"A": "ok", "B": "no_data"}, (1, 0, 1, False)),
        ({"A": "partial", "B": "no_data"}, (0, 1, 1, False)),
    ],
)
def test_result_package_reports_data_quality_separately_from_status(
    tmp_path,
    statuses: dict[str, str],
    expected: tuple[int, int, int, bool],
):
    client = FakeDataClient()
    loaded = [LoadedRule(_EmptyRule(), _quality_config(["A", "B"]))]
    manager = DeliveryManager(
        loader=_Loader(loaded, client),
        data_client=client,
        visualization_loader=_Visualizers(_Visualizer(statuses)),
    )

    result = manager.deliver(SHIFT, tmp_path)
    document = json.loads(result.result_json_path.read_text(encoding="utf-8"))

    quality = document["summary"]["data_quality"]
    assert (
        quality["ok_points"],
        quality["partial_points"],
        quality["no_data_points"],
        quality["assessment_complete"],
    ) == expected
    assert all(point["status"] == "normal" for point in document["rules"][0]["points"])


def test_delivery_rejects_an_event_with_the_wrong_point_window(tmp_path):
    client = FakeDataClient()
    config = _quality_config(["A"])
    config["assessment_window"] = {"start_offset_minutes": 0}
    expected_window = TimeRange(datetime(2026, 9, 3, 8), datetime(2026, 9, 3, 20))
    wrong_window = TimeRange(datetime(2026, 9, 3, 8, 30), datetime(2026, 9, 3, 20))
    event = AssessmentEvent(
        datetime(2026, 9, 3, 9),
        datetime(2026, 9, 3, 10),
        data={"point_id": "A", "event_type": "test"},
    )
    evaluated = EvaluatedAssessmentEvent(
        rule_id="quality_rule",
        rule_name="Quality rule",
        shift=SHIFT,
        window=wrong_window,
        event=event,
        config=config,
    )
    execution = RuleExecutionResult(
        rule_id="quality_rule",
        rule_name="Quality rule",
        shift=SHIFT,
        window=expected_window,
        config=config,
        events=(evaluated,),
        point_windows={"A": expected_window},
    )

    class WrongWindowRunner:
        def run_execution(self, shift, loaded_rule):
            return execution

    manager = DeliveryManager(
        loader=_Loader([LoadedRule(_EmptyRule(), config)], client),
        runner=WrongWindowRunner(),
        data_client=client,
        visualization_loader=_Visualizers(_Visualizer()),
    )

    with pytest.raises(DeliveryError, match="assessment window"):
        manager.deliver(SHIFT, tmp_path)
    assert not (tmp_path / "20260903T080000_20260903T200000_A").exists()


def test_point_assessment_window_is_required_by_the_delivery_model():
    with pytest.raises(TypeError, match="window"):
        PointAssessmentResult(
            "rule",
            "Rule",
            "point",
            0,
            0.0,
            "normal",
            "ok",
            "images/point.png",
            (),
        )

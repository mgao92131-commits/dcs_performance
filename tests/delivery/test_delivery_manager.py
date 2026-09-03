import json
from datetime import datetime

import pytest

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.delivery.manager import DeliveryError, DeliveryManager
from dcs_performance.data.errors import DcsServiceError
from dcs_performance.engine.loader import LoadedRule
from dcs_performance.shifts.model import Shift
from dcs_performance.visualization.models import VisualizationArtifact
from tests.fakes import FakeDataClient


PNG = b"\x89PNG\r\n\x1a\nresult-package-test"


class Rule:
    def __init__(self, rule_id, events):
        self.id = rule_id
        self.name = f"Name {rule_id}"
        self.events = events

    def evaluate(self, start_time, end_time):
        return list(self.events)


class Loader:
    def __init__(self, loaded):
        self.loaded = loaded
        self.data_client = FakeDataClient()

    def load_enabled(self):
        return self.loaded


class Visualizer:
    def __init__(self, received=None, fail=False, client_call=False):
        self.received = received
        self.fail = fail
        self.client_call = client_call

    def render_point(self, context, output_path):
        if self.fail:
            raise RuntimeError("render failed")
        if self.client_call:
            context.data_client.get_history("TAG", context.window.start_time, context.window.end_time)
        if self.received is not None:
            self.received.append(context)
        output_path.write_bytes(PNG)
        return VisualizationArtifact(f"images/{output_path.name}", "no_data")


class Visualizers:
    def __init__(self, visualizers):
        self.visualizers = visualizers

    def load(self, rule_id):
        return self.visualizers[rule_id]


def _config(rule_id, points):
    return {
        "id": rule_id,
        "name": f"Name {rule_id}",
        "enabled": True,
        "parameters": {"points": points},
        "scoring": {"default_score_per_event": 1},
    }


def _event(point_id="POINT-1", data=None):
    payload = {"point_id": point_id, "event_type": "test"}
    if data:
        payload.update(data)
    return AssessmentEvent(
        datetime(2026, 9, 3, 9), datetime(2026, 9, 3, 10), "中文消息", payload
    )


@pytest.fixture
def shift():
    return Shift("A", datetime(2026, 9, 3, 8), datetime(2026, 9, 3, 20), "day")


def _manager(loaded, visualizers):
    loader = Loader(loaded)
    return DeliveryManager(
        loader=loader,
        data_client=loader.data_client,
        visualization_loader=Visualizers(visualizers),
    )


def test_zero_event_and_disabled_points_are_delivered(tmp_path, shift):
    config = _config("rule_a", [{"id": "ZERO"}, {"id": "OFF", "enabled": False}])
    loaded = [LoadedRule(Rule("rule_a", []), config)]
    result = _manager(loaded, {"rule_a": Visualizer()}).deliver(shift, tmp_path)
    document = json.loads(result.result_json_path.read_text(encoding="utf-8"))
    assert document["schema_version"] == "1.0"
    assert document["summary"] == {
        "rule_count": 1, "point_count": 1, "event_count": 0, "total_score": 0
    }
    assert [point["point_id"] for point in document["rules"][0]["points"]] == ["ZERO"]
    point = document["rules"][0]["points"][0]
    assert (point["status"], point["data_status"], point["events"]) == ("normal", "no_data", [])
    image = result.package_path / point["image"]
    assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert not any("OFF" in path.name for path in result.images_path.iterdir())


def test_duplicate_point_ids_across_rules_stay_separate_and_events_match_visualizer(tmp_path, shift):
    received = []
    loaded = [
        LoadedRule(Rule("rule_a", [_event()]), _config("rule_a", [{"id": "POINT-1"}])),
        LoadedRule(Rule("rule_b", [_event()]), _config("rule_b", [{"id": "POINT-1"}])),
    ]
    visualizer = Visualizer(received)
    result = _manager(loaded, {"rule_a": visualizer, "rule_b": visualizer}).deliver(shift, tmp_path)
    document = json.loads(result.result_json_path.read_text(encoding="utf-8"))
    assert document["summary"]["point_count"] == 2
    assert {path.name for path in result.images_path.iterdir()} == {
        "rule_a__POINT-1.png", "rule_b__POINT-1.png"
    }
    assert [context.events[0].event_start.isoformat() for context in received] == [
        rule["points"][0]["events"][0]["start"] for rule in document["rules"]
    ]
    first_event = document["rules"][0]["points"][0]["events"][0]
    assert first_event["duration_seconds"] == 3600.0
    assert first_event["score"] == 1.0
    assert first_event["message"] == "中文消息"
    assert document["shift"]["start"] == "2026-09-03T08:00:00"
    assert all("\\" not in rule["points"][0]["image"] for rule in document["rules"])


def test_events_are_grouped_by_rule_and_point(tmp_path, shift):
    received = []
    loaded = [LoadedRule(
        Rule("rule_a", [_event("POINT-A"), _event("POINT-B")]),
        _config("rule_a", [{"id": "POINT-A"}, {"id": "POINT-B"}]),
    )]
    result = _manager(loaded, {"rule_a": Visualizer(received)}).deliver(shift, tmp_path)
    document = json.loads(result.result_json_path.read_text(encoding="utf-8"))
    assert [[event.data["point_id"] for event in context.events] for context in received] == [
        ["POINT-A"], ["POINT-B"]
    ]
    assert [point["events"][0]["data"]["point_id"] for point in document["rules"][0]["points"]] == [
        "POINT-A", "POINT-B"
    ]


def test_point_windows_reach_visualizer_and_json_for_mixed_rule_windows(
    tmp_path,
    shift,
):
    received = []
    config = _config(
        "rule_a",
        [
            {"id": "POINT-A"},
            {
                "id": "POINT-B",
                "assessment_window": {"start_offset_minutes": 30},
            },
        ],
    )
    config["assessment_window"] = {
        "start_offset_minutes": -10,
        "end_offset_minutes": 10,
    }
    loaded = [LoadedRule(
        Rule("rule_a", [_event("POINT-A"), _event("POINT-B")]),
        config,
    )]

    result = _manager(loaded, {"rule_a": Visualizer(received)}).deliver(shift, tmp_path)
    document = json.loads(result.result_json_path.read_text(encoding="utf-8"))
    points = document["rules"][0]["points"]

    assert [context.point_id for context in received] == ["POINT-A", "POINT-B"]
    assert [context.window.start_time for context in received] == [
        datetime(2026, 9, 3, 7, 50),
        datetime(2026, 9, 3, 8, 30),
    ]
    assert [point["assessment_window"] for point in points] == [
        {
            "start": "2026-09-03T07:50:00",
            "end": "2026-09-03T19:50:00",
        },
        {
            "start": "2026-09-03T08:30:00",
            "end": "2026-09-03T19:50:00",
        },
    ]


def test_result_event_type_uses_existing_direction_metadata(tmp_path, shift):
    event = _event("POINT-1", {"event_type": "level_rate", "direction": "rate_down"})
    loaded = [LoadedRule(Rule("rule_a", [event]), _config("rule_a", [{"id": "POINT-1"}]))]
    result = _manager(loaded, {"rule_a": Visualizer()}).deliver(shift, tmp_path)
    document = json.loads(result.result_json_path.read_text(encoding="utf-8"))
    serialized = document["rules"][0]["points"][0]["events"][0]
    assert serialized["event_type"] == "rate_down"
    assert serialized["data"]["event_type"] == "level_rate"


def test_result_event_type_falls_back_to_rule_identity(tmp_path, shift):
    event = AssessmentEvent(
        datetime(2026, 9, 3, 9), datetime(2026, 9, 3, 10),
        data={"point_id": "POINT-1"},
    )
    loaded = [LoadedRule(Rule("persistent_high_alarm", [event]), _config(
        "persistent_high_alarm", [{"id": "POINT-1"}]
    ))]
    result = _manager(
        loaded, {"persistent_high_alarm": Visualizer()}
    ).deliver(shift, tmp_path)
    document = json.loads(result.result_json_path.read_text(encoding="utf-8"))
    serialized = document["rules"][0]["points"][0]["events"][0]
    assert serialized["event_type"] == "persistent_high_alarm"


def test_sanitized_filename_collision_is_disambiguated(tmp_path, shift):
    loaded = [LoadedRule(
        Rule("rule_a", []),
        _config("rule_a", [{"id": "A/B"}, {"id": "A:B"}]),
    )]
    result = _manager(loaded, {"rule_a": Visualizer()}).deliver(shift, tmp_path)
    document = json.loads(result.result_json_path.read_text(encoding="utf-8"))
    images = [point["image"] for point in document["rules"][0]["points"]]
    assert len(set(images)) == 2
    assert all((result.package_path / image).exists() for image in images)


@pytest.mark.parametrize("point_id", [None, "UNKNOWN"])
def test_missing_or_unknown_point_id_fails_without_publication(tmp_path, shift, point_id):
    event = _event() if point_id is None else _event(point_id)
    if point_id is None:
        event = AssessmentEvent(event.start_time, event.end_time, data={"event_type": "test"})
    loaded = [LoadedRule(Rule("rule_a", [event]), _config("rule_a", [{"id": "POINT-1"}]))]
    with pytest.raises(DeliveryError):
        _manager(loaded, {"rule_a": Visualizer()}).deliver(shift, tmp_path)
    assert not (tmp_path / "20260903T080000_20260903T200000_A").exists()


def test_render_and_json_failures_do_not_publish(tmp_path, shift):
    config = _config("rule_a", [{"id": "POINT-1"}])
    for visualizer, events in (
        (Visualizer(fail=True), []),
        (Visualizer(), [_event(data={"bad": object()})]),
    ):
        loaded = [LoadedRule(Rule("rule_a", events), config)]
        with pytest.raises(DeliveryError):
            _manager(loaded, {"rule_a": visualizer}).deliver(shift, tmp_path)
        assert not (tmp_path / "20260903T080000_20260903T200000_A").exists()
        assert not list(tmp_path.glob(".tmp-*"))


def test_visualizer_load_failure_does_not_publish(tmp_path, shift):
    class BrokenVisualizers:
        def load(self, rule_id):
            raise ImportError(f"cannot import {rule_id}")

    config = _config("rule_a", [{"id": "POINT-1"}])
    loaded = [LoadedRule(Rule("rule_a", []), config)]
    loader = Loader(loaded)
    manager = DeliveryManager(
        loader=loader,
        data_client=loader.data_client,
        visualization_loader=BrokenVisualizers(),
    )
    with pytest.raises(DeliveryError, match="cannot import rule_a"):
        manager.deliver(shift, tmp_path)
    assert not (tmp_path / "20260903T080000_20260903T200000_A").exists()
    assert not list(tmp_path.glob(".tmp-*"))


def test_rule_execution_failure_does_not_publish(tmp_path, shift):
    class BrokenRule(Rule):
        def evaluate(self, start_time, end_time):
            raise RuntimeError("rule exploded")

    config = _config("rule_a", [{"id": "POINT-1"}])
    loaded = [LoadedRule(BrokenRule("rule_a", []), config)]
    with pytest.raises(DeliveryError, match="rule exploded"):
        _manager(loaded, {"rule_a": Visualizer()}).deliver(shift, tmp_path)
    assert not (tmp_path / "20260903T080000_20260903T200000_A").exists()


def test_scoring_failure_does_not_publish(tmp_path, shift):
    config = _config("rule_a", [{"id": "POINT-1"}])
    config["scoring"] = {}
    loaded = [LoadedRule(Rule("rule_a", [_event()]), config)]
    with pytest.raises(DeliveryError, match="scoring"):
        _manager(loaded, {"rule_a": Visualizer()}).deliver(shift, tmp_path)
    assert not (tmp_path / "20260903T080000_20260903T200000_A").exists()


def test_invalid_png_does_not_publish(tmp_path, shift):
    class BadPngVisualizer(Visualizer):
        def render_point(self, context, output_path):
            output_path.write_bytes(b"not a real png file")
            return VisualizationArtifact(f"images/{output_path.name}", "ok")

    config = _config("rule_a", [{"id": "POINT-1"}])
    loaded = [LoadedRule(Rule("rule_a", []), config)]
    with pytest.raises(DeliveryError, match="not a PNG"):
        _manager(loaded, {"rule_a": BadPngVisualizer()}).deliver(shift, tmp_path)
    assert not (tmp_path / "20260903T080000_20260903T200000_A").exists()


def test_overwrite_is_explicit_and_failed_overwrite_preserves_old_package(tmp_path, shift):
    config = _config("rule_a", [{"id": "POINT-1"}])
    loaded = [LoadedRule(Rule("rule_a", []), config)]
    manager = _manager(loaded, {"rule_a": Visualizer()})
    first = manager.deliver(shift, tmp_path)
    original = first.result_json_path.read_bytes()
    with pytest.raises(DeliveryError, match="already exists"):
        manager.deliver(shift, tmp_path)
    failing = _manager(loaded, {"rule_a": Visualizer(fail=True)})
    with pytest.raises(DeliveryError):
        failing.deliver(shift, tmp_path, overwrite=True)
    assert first.result_json_path.read_bytes() == original
    replaced = manager.deliver(shift, tmp_path, overwrite=True)
    assert replaced.result_json_path.exists()
    assert not list(tmp_path.glob(".tmp-*"))
    assert not list(tmp_path.glob(".backup-*"))


def test_interrupted_overwrite_restores_unique_backup_before_next_run(tmp_path, shift):
    run_id = "20260903T080000_20260903T200000_A"
    backup = tmp_path / f".backup-{run_id}-deadbeef"
    backup.mkdir()
    (backup / "result.json").write_bytes(b"previous-success")
    stale_temporary = tmp_path / f".tmp-{run_id}-incomplete"
    stale_temporary.mkdir()
    (stale_temporary / "partial.png").write_bytes(b"incomplete")

    config = _config("rule_a", [{"id": "POINT-1"}])
    manager = _manager(
        [LoadedRule(Rule("rule_a", []), config)],
        {"rule_a": Visualizer()},
    )

    with pytest.raises(DeliveryError, match="already exists"):
        manager.deliver(shift, tmp_path)

    target = tmp_path / run_id
    assert (target / "result.json").read_bytes() == b"previous-success"
    assert not backup.exists()
    assert not stale_temporary.exists()


def test_interrupted_overwrite_with_multiple_backups_fails_without_guessing(tmp_path, shift):
    run_id = "20260903T080000_20260903T200000_A"
    backups = [tmp_path / f".backup-{run_id}-{suffix}" for suffix in ("one", "two")]
    for backup in backups:
        backup.mkdir()
        (backup / "result.json").write_text(backup.name, encoding="utf-8")

    config = _config("rule_a", [{"id": "POINT-1"}])
    manager = _manager(
        [LoadedRule(Rule("rule_a", []), config)],
        {"rule_a": Visualizer()},
    )

    with pytest.raises(DeliveryError, match="multiple backups"):
        manager.deliver(shift, tmp_path, overwrite=True)

    assert not (tmp_path / run_id).exists()
    assert all(backup.exists() for backup in backups)


def test_data_service_exception_is_not_treated_as_no_data(tmp_path, shift):
    class BrokenClient(FakeDataClient):
        def get_history(self, *args):
            raise DcsServiceError("service unavailable")

    config = _config("rule_a", [{"id": "POINT-1"}])
    loaded = [LoadedRule(Rule("rule_a", []), config)]
    loader = Loader(loaded)
    broken = BrokenClient()
    manager = DeliveryManager(
        loader=loader, data_client=broken,
        visualization_loader=Visualizers({"rule_a": Visualizer(client_call=True)}),
    )
    with pytest.raises(DeliveryError, match="service unavailable"):
        manager.deliver(shift, tmp_path)

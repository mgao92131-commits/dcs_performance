import json
from datetime import datetime
from pathlib import Path

from dcs_performance.delivery.manager import DeliveryManager
from dcs_performance.shifts.model import Shift
from tests.fakes import FakeDataClient


def test_real_pipeline_delivers_every_enabled_point_with_no_data(tmp_path):
    rules_dir = Path(__file__).resolve().parents[2] / "src" / "dcs_performance" / "rules"
    shift = Shift("A", datetime(2026, 9, 3, 8), datetime(2026, 9, 3, 20), "day")
    result = DeliveryManager(
        rules_dir=rules_dir,
        data_client=FakeDataClient(),
    ).deliver(shift, tmp_path)

    document = json.loads(result.result_json_path.read_text(encoding="utf-8"))
    points = [point for rule in document["rules"] for point in rule["points"]]
    assert len(points) == document["summary"]["point_count"]
    assert document["summary"]["rule_count"] == len(document["rules"])
    assert all(point["data_status"] == "no_data" for point in points)
    assert all((result.package_path / point["image"]).is_file() for point in points)
    assert len(list(result.images_path.glob("*.png"))) == len(points)

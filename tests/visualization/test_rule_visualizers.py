import json
from datetime import datetime
from pathlib import Path

import pytest

from dcs_performance.core.window import TimeRange
from dcs_performance.shifts.model import Shift
from dcs_performance.visualization.loader import VisualizationLoader
from dcs_performance.visualization.models import PointVisualizationContext
from tests.fakes import FakeDataClient, make_history_sample


RULES = (
    "analog_limit_exceedance",
    "persistent_high_alarm",
    "pump_flow_compliance",
    "level_rate_compliance",
    "flow_balance_compliance",
    "component_viscosity_control",
    "analog_trend_stability",
)


@pytest.mark.parametrize("rule_id", RULES)
def test_rule_visualizer_no_data_smoke(rule_id, tmp_path):
    rules_dir = Path(__file__).resolve().parents[2] / "src" / "dcs_performance" / "rules"
    config = json.loads((rules_dir / rule_id / "config.json").read_text(encoding="utf-8"))
    point = config["parameters"]["points"][0]
    shift = Shift("A", datetime(2026, 9, 3, 8), datetime(2026, 9, 3, 20), "day")
    context = PointVisualizationContext(
        rule_id=rule_id,
        rule_name=config["name"],
        point_id=point["id"],
        point_config=point,
        rule_config=config,
        shift=shift,
        window=TimeRange(shift.start_time, shift.end_time),
        events=(),
        data_client=FakeDataClient(),
    )
    output = tmp_path / f"{rule_id}.png"
    artifact = VisualizationLoader(rules_dir).load(rule_id).render_point(context, output)
    assert artifact.data_status == "no_data"
    assert output.stat().st_size > 8
    assert output.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("rule_id", RULES)
def test_rule_visualizer_valid_data_smoke(rule_id, tmp_path):
    rules_dir = Path(__file__).resolve().parents[2] / "src" / "dcs_performance" / "rules"
    config = json.loads((rules_dir / rule_id / "config.json").read_text(encoding="utf-8"))
    point = config["parameters"]["points"][0]
    start = datetime(2026, 9, 3, 8)
    tags = []
    for key in ("history_tag", "pump_a_tag", "pump_b_tag", "flow_tag", "logic_tag"):
        if key in point:
            tags.append(point[key])
    tags.extend(point.get("sy_tags", []))
    samples = [
        make_history_sample(start.replace(minute=minute), "1", minute + 1)
        for minute in range(3)
    ]
    client = FakeDataClient({tag: samples for tag in tags})
    shift = Shift("A", start, datetime(2026, 9, 3, 20), "day")
    context = PointVisualizationContext(
        rule_id=rule_id, rule_name=config["name"], point_id=point["id"],
        point_config=point, rule_config=config, shift=shift,
        window=TimeRange(shift.start_time, shift.end_time), events=(), data_client=client,
    )
    output = tmp_path / f"{rule_id}-data.png"
    artifact = VisualizationLoader(rules_dir).load(rule_id).render_point(context, output)
    assert artifact.data_status == "ok"
    assert output.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

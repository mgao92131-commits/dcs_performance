from pathlib import Path

import pytest

from dcs_performance.visualization.loader import VisualizationLoadError, VisualizationLoader


def test_loader_supports_rule_local_relative_imports(tmp_path):
    rule = tmp_path / "rule_a"
    rule.mkdir()
    (rule / "helper.py").write_text("VALUE = 7\n", encoding="utf-8")
    (rule / "visualization.py").write_text(
        "from .helper import VALUE\n"
        "class Visualizer:\n"
        "    marker = VALUE\n"
        "    def render_point(self, context, output_path): pass\n",
        encoding="utf-8",
    )
    assert VisualizationLoader(tmp_path).load("rule_a").marker == 7


def test_loader_reports_missing_optional_extension(tmp_path):
    (tmp_path / "rule_a").mkdir()
    with pytest.raises(VisualizationLoadError, match="no visualization.py"):
        VisualizationLoader(tmp_path).load("rule_a")

"""Dynamic optional loading of rule-local ``visualization.py`` modules."""

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


class VisualizationLoadError(RuntimeError):
    pass


class VisualizationLoader:
    def __init__(self, rules_dir: str | Path) -> None:
        self.rules_dir = Path(rules_dir)

    def load(self, rule_id: str):
        rule_dir = self.rules_dir / rule_id
        path = rule_dir / "visualization.py"
        if not path.is_file():
            raise VisualizationLoadError(
                f"enabled rule {rule_id!r} has points but no visualization.py"
            )
        digest = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:12]
        package_name = f"dcs_performance_visualizer_{rule_dir.name}_{digest}"
        module_name = f"{package_name}.visualization"
        package = ModuleType(package_name)
        package.__path__ = [str(rule_dir)]  # type: ignore[attr-defined]
        package.__package__ = package_name
        sys.modules[package_name] = package
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise VisualizationLoadError(f"could not create import spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            visualizer_class = getattr(module, "Visualizer")
            visualizer = visualizer_class()
        except Exception as exc:
            raise VisualizationLoadError(
                f"could not load visualizer for rule {rule_id!r}"
            ) from exc
        if not callable(getattr(visualizer, "render_point", None)):
            raise VisualizationLoadError(
                f"visualizer for rule {rule_id!r} must define render_point()"
            )
        return visualizer

"""Transparent directory-based rule discovery and loading."""

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dcs_performance.core.rule import AssessmentRule
from dcs_performance.data.client import DcsDataClient


class RuleLoadError(RuntimeError):
    """Raised when a rule directory cannot be loaded."""


@dataclass(frozen=True)
class LoadedRule:
    """A constructed rule together with its local configuration."""

    rule: AssessmentRule
    config: dict[str, Any]

    @property
    def id(self) -> str:
        return self.rule.id

    @property
    def name(self) -> str:
        return self.rule.name

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))


class RuleLoader:
    """Load ``Rule`` classes and adjacent JSON config files from directories."""

    def __init__(
        self,
        rules_dir: str | Path | None = None,
        data_client: DcsDataClient | None = None,
    ) -> None:
        default_dir = Path(__file__).resolve().parents[1] / "rules"
        self.rules_dir = Path(rules_dir) if rules_dir is not None else default_dir
        self.data_client = data_client

    def discover(self) -> list[Path]:
        """Return valid rule directories in deterministic order."""

        if not self.rules_dir.is_dir():
            raise RuleLoadError(f"rules directory does not exist: {self.rules_dir}")

        return [
            path
            for path in sorted(self.rules_dir.iterdir(), key=lambda item: item.name)
            if path.is_dir()
            and not path.name.startswith((".", "__"))
            and (path / "rule.py").is_file()
            and (path / "config.json").is_file()
        ]

    def load(self, rule_id: str) -> LoadedRule:
        """Load one rule directory by its directory name."""

        rule_dir = self.rules_dir / rule_id
        if not rule_dir.is_dir():
            raise RuleLoadError(f"rule directory does not exist: {rule_dir}")
        return self._load_directory(rule_dir)

    def load_all(self) -> list[LoadedRule]:
        """Load every valid rule directory, including disabled rules."""

        return [self._load_directory(path) for path in self.discover()]

    def load_enabled(self) -> list[LoadedRule]:
        """Load only rules whose local config has ``enabled: true``."""

        return [loaded for loaded in self.load_all() if loaded.enabled]

    def _load_directory(self, rule_dir: Path) -> LoadedRule:
        config_path = rule_dir / "config.json"
        rule_path = rule_dir / "rule.py"
        if not config_path.is_file() or not rule_path.is_file():
            raise RuleLoadError(
                f"rule {rule_dir.name!r} must contain rule.py and config.json"
            )

        try:
            with config_path.open("r", encoding="utf-8") as handle:
                config = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuleLoadError(f"could not read config for {rule_dir.name!r}") from exc

        if not isinstance(config, dict):
            raise RuleLoadError(f"config for {rule_dir.name!r} must be a JSON object")

        module = self._load_module(rule_dir)
        rule_class = getattr(module, "Rule", None)
        if rule_class is None:
            raise RuleLoadError(f"{rule_path} must define a class named Rule")

        try:
            rule = rule_class(data_client=self.data_client, config=config)
        except Exception as exc:
            raise RuleLoadError(f"could not construct rule {rule_dir.name!r}") from exc

        if not isinstance(rule.id, str) or not isinstance(rule.name, str):
            raise RuleLoadError(f"rule {rule_dir.name!r} must expose string id and name")

        return LoadedRule(rule=rule, config=config)

    @staticmethod
    def _load_module(rule_dir: Path):
        """Import one rule file without introducing an entry-point framework."""

        rule_path = rule_dir / "rule.py"
        digest = hashlib.sha1(str(rule_path.resolve()).encode("utf-8")).hexdigest()[:12]
        module_name = f"dcs_performance_rule_{rule_dir.name}_{digest}"
        spec = importlib.util.spec_from_file_location(module_name, rule_path)
        if spec is None or spec.loader is None:
            raise RuleLoadError(f"could not create import spec for {rule_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(module_name, None)
            raise RuleLoadError(f"could not import {rule_path}") from exc
        return module

# Short name retained for the terminology used in the project brief.
Loader = RuleLoader

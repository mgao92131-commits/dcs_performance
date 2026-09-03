"""End-to-end, failure-atomic Result Package delivery."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from dcs_performance.core.result import AssignedAssessmentEvent
from dcs_performance.data.client import DcsDataClient
from dcs_performance.engine.loader import RuleLoader
from dcs_performance.engine.runner import RuleRunner
from dcs_performance.results.scorer import AssessmentScorer
from dcs_performance.results.summary import AssessmentSummarizer
from dcs_performance.shifts.model import Shift
from dcs_performance.visualization.loader import VisualizationLoader
from dcs_performance.visualization.models import PointVisualizationContext

from .models import DeliveryResult, PointAssessmentResult, RuleAssessmentResult
from .paths import build_run_id, point_image_filename
from .serializer import build_result_document, write_result_json


class DeliveryError(RuntimeError):
    """Raised when a complete package cannot be safely published."""


class DeliveryManager:
    def __init__(
        self,
        *,
        loader: RuleLoader | None = None,
        runner: RuleRunner | None = None,
        scorer: AssessmentScorer | None = None,
        visualization_loader: VisualizationLoader | Any | None = None,
        rules_dir: str | Path | None = None,
        data_client: DcsDataClient | None = None,
        clock: Any | None = None,
    ) -> None:
        self.loader = loader or RuleLoader(rules_dir=rules_dir, data_client=data_client)
        self.runner = runner or RuleRunner()
        self.scorer = scorer or AssessmentScorer()
        self.data_client = data_client or getattr(self.loader, "data_client", None)
        loader_rules_dir = rules_dir or getattr(self.loader, "rules_dir", None)
        if visualization_loader is None:
            if loader_rules_dir is None:
                raise ValueError("rules_dir is required to load visualizers")
            visualization_loader = VisualizationLoader(loader_rules_dir)
        self.visualization_loader = visualization_loader
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def deliver(
        self,
        shift: Shift,
        output_root: str | Path,
        *,
        overwrite: bool = False,
    ) -> DeliveryResult:
        if self.data_client is None:
            raise DeliveryError("Result Package delivery requires a data client")
        if shift.start_time.tzinfo is not None or shift.end_time.tzinfo is not None:
            raise DeliveryError("Result Package shifts must use timezone-naive local time")
        output_root = Path(output_root)
        run_id = build_run_id(shift.start_time, shift.end_time, shift.team_id)
        target = output_root / run_id
        output_root.mkdir(parents=True, exist_ok=True)
        _recover_interrupted_delivery(output_root, run_id, target)
        if target.exists() and not overwrite:
            raise DeliveryError(f"result package already exists: {target}")

        temporary = Path(tempfile.mkdtemp(prefix=f".tmp-{run_id}-", dir=output_root))
        backup: Path | None = None
        try:
            images_dir = temporary / "images"
            images_dir.mkdir()
            rule_results = self._execute_and_render(shift, images_dir)
            generated_at = self.clock()
            if not isinstance(generated_at, datetime) or generated_at.tzinfo is None:
                raise DeliveryError("generated_at clock must return a timezone-aware datetime")
            generated_at = generated_at.astimezone(timezone.utc)
            event_count = sum(rule.event_count for rule in rule_results)
            total_score = sum(rule.score for rule in rule_results)
            point_count = sum(len(rule.points) for rule in rule_results)
            provisional = DeliveryResult(
                run_id=run_id,
                package_path=target,
                result_json_path=target / "result.json",
                images_path=target / "images",
                shift=shift,
                generated_at=generated_at,
                rule_count=len(rule_results),
                point_count=point_count,
                event_count=event_count,
                total_score=total_score,
            )
            document = build_result_document(provisional, rule_results)
            write_result_json(temporary / "result.json", document)

            if target.exists() and not overwrite:
                raise DeliveryError(f"result package already exists: {target}")
            if target.exists():
                backup = output_root / f".backup-{run_id}-{uuid4().hex}"
                os.replace(target, backup)
            try:
                os.replace(temporary, target)
            except Exception:
                if backup is not None and not target.exists():
                    os.replace(backup, target)
                    backup = None
                raise
            if backup is not None:
                # The new package is already complete and published.  A stale
                # backup cleanup problem must not turn that success into a
                # misleading failure result.
                shutil.rmtree(backup, ignore_errors=True)
                backup = None
            return provisional
        except Exception as exc:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
            if backup is not None and backup.exists() and not target.exists():
                os.replace(backup, target)
            if isinstance(exc, DeliveryError):
                raise
            raise DeliveryError(f"could not deliver Result Package {run_id}: {exc}") from exc

    def _execute_and_render(
        self,
        shift: Shift,
        images_dir: Path,
    ) -> tuple[RuleAssessmentResult, ...]:
        results: list[RuleAssessmentResult] = []
        used_filenames: dict[str, tuple[str, str]] = {}
        for loaded in self.loader.load_enabled():
            execution = self.runner.run_execution(shift, loaded)
            points = _enabled_points(execution.rule_id, execution.config)
            if not points:
                if execution.events:
                    raise DeliveryError(
                        f"Rule {execution.rule_id} returned events but defines no points"
                    )
                results.append(
                    RuleAssessmentResult(
                        rule_id=execution.rule_id,
                        rule_name=execution.rule_name,
                        window=execution.window,
                        event_count=0,
                        score=0.0,
                        points=(),
                    )
                )
                continue

            point_ids = {point_id for point_id, _ in points}
            for evaluated in execution.events:
                point_id = evaluated.event.data.get("point_id")
                if not isinstance(point_id, str) or not point_id:
                    raise DeliveryError(
                        f"Rule {execution.rule_id} returned event without point_id"
                    )
                if point_id not in point_ids:
                    raise DeliveryError(
                        f"Rule {execution.rule_id} returned event for unknown or disabled "
                        f"point_id {point_id!r}"
                    )
            assigned = tuple(self.scorer.score(item) for item in execution.events)
            summary = AssessmentSummarizer(point_ids=point_ids).summarize(
                assigned,
                shift=shift,
                window=execution.window,
                allow_multiple_windows=_has_multiple_point_windows(
                    execution.point_windows
                ),
            )
            by_point: dict[str, list[AssignedAssessmentEvent]] = {
                point_id: [] for point_id, _ in points
            }
            for event in assigned:
                point_id = event.data.get("point_id")
                assert isinstance(point_id, str) and point_id in by_point
                by_point[point_id].append(event)

            visualizer = self.visualization_loader.load(execution.rule_id)
            point_results: list[PointAssessmentResult] = []
            for point_id, point_config in points:
                filename = point_image_filename(execution.rule_id, point_id)
                identity = (execution.rule_id, point_id)
                filename = _deduplicate_filename(filename, identity, used_filenames)
                used_filenames[filename] = identity
                point_events = tuple(by_point[point_id])
                point_summary = summary.by_point[point_id]
                point_window = execution.window_for_point(point_id)
                context = PointVisualizationContext(
                    rule_id=execution.rule_id,
                    rule_name=execution.rule_name,
                    point_id=point_id,
                    point_config=point_config,
                    rule_config=execution.config,
                    shift=shift,
                    window=point_window,
                    events=point_events,
                    data_client=self.data_client,
                )
                output_path = images_dir / filename
                artifact = visualizer.render_point(context, output_path)
                expected_image_path = f"images/{filename}"
                _validate_artifact(output_path, artifact, expected_image_path)
                point_results.append(
                    PointAssessmentResult(
                        rule_id=execution.rule_id,
                        rule_name=execution.rule_name,
                        point_id=point_id,
                        event_count=point_summary.event_count,
                        score=point_summary.score,
                        status="violation" if point_events else "normal",
                        data_status=artifact.data_status,
                        image_path=expected_image_path,
                        events=point_events,
                        metadata=dict(artifact.metadata),
                        window=point_window,
                    )
                )
            results.append(
                RuleAssessmentResult(
                    rule_id=execution.rule_id,
                    rule_name=execution.rule_name,
                    window=execution.window,
                    event_count=summary.event_count,
                    score=summary.total_score,
                    points=tuple(point_results),
                )
            )
        return tuple(results)


def _enabled_points(
    rule_id: str,
    config: Mapping[str, Any],
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    parameters = config.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise DeliveryError(f"Rule {rule_id} parameters must be an object")
    raw_points = parameters.get("points")
    if raw_points is None:
        return ()
    if not isinstance(raw_points, list):
        raise DeliveryError(f"Rule {rule_id} parameters.points must be an array")
    result: list[tuple[str, Mapping[str, Any]]] = []
    seen: set[str] = set()
    for index, point in enumerate(raw_points):
        if not isinstance(point, Mapping):
            raise DeliveryError(f"Rule {rule_id} point {index} must be an object")
        enabled = point.get("enabled", True)
        if not isinstance(enabled, bool):
            raise DeliveryError(f"Rule {rule_id} point {index} enabled must be boolean")
        point_id = point.get("id")
        if not isinstance(point_id, str) or not point_id:
            raise DeliveryError(f"Rule {rule_id} point {index} id must be non-empty text")
        if point_id in seen:
            raise DeliveryError(f"Rule {rule_id} has duplicate point_id {point_id!r}")
        seen.add(point_id)
        if enabled:
            result.append((point_id, point))
    return tuple(result)


def _validate_artifact(path: Path, artifact: Any, expected_image_path: str) -> None:
    data_status = getattr(artifact, "data_status", None)
    if data_status not in {"ok", "partial", "no_data"}:
        raise DeliveryError(f"visualizer returned unsupported data_status {data_status!r}")
    image_path = getattr(artifact, "image_path", None)
    if not isinstance(image_path, str) or image_path.replace("\\", "/") != expected_image_path:
        raise DeliveryError(
            f"visualizer returned image_path {image_path!r}; expected {expected_image_path!r}"
        )
    if not path.is_file() or path.stat().st_size <= 8:
        raise DeliveryError(f"visualizer did not create a non-empty PNG: {path}")
    with path.open("rb") as handle:
        if handle.read(8) != b"\x89PNG\r\n\x1a\n":
            raise DeliveryError(f"visualizer output is not a PNG: {path}")


def _has_multiple_point_windows(
    point_windows: Mapping[str, Any],
) -> bool:
    windows = {
        (window.start_time, window.end_time)
        for window in point_windows.values()
    }
    return len(windows) > 1


def _deduplicate_filename(
    filename: str,
    identity: tuple[str, str],
    used: Mapping[str, tuple[str, str]],
) -> str:
    existing = used.get(filename)
    if existing is None or existing == identity:
        return filename
    stem = filename[:-4]
    digest = hashlib.sha256(f"{identity[0]}\0{identity[1]}".encode("utf-8")).hexdigest()
    for length in range(8, len(digest) + 1, 4):
        candidate = f"{stem}__{digest[:length]}.png"
        if candidate not in used or used[candidate] == identity:
            return candidate
    raise DeliveryError(f"could not create a unique image filename for {identity}")


def _recover_interrupted_delivery(
    output_root: Path,
    run_id: str,
    target: Path,
) -> None:
    """Recover the last successful package after an interrupted overwrite.

    Publishing is single-writer per run ID. A temporary directory is never a
    successful package, while a backup was moved from the formal target and is
    safe to restore when it is the only candidate.
    """

    backups = sorted(output_root.glob(f".backup-{run_id}-*"))
    temporaries = sorted(output_root.glob(f".tmp-{run_id}-*"))
    invalid = [path for path in (*backups, *temporaries) if not path.is_dir()]
    if invalid:
        names = ", ".join(path.name for path in invalid)
        raise DeliveryError(f"invalid Result Package recovery artifact(s): {names}")

    if not target.exists():
        if len(backups) > 1:
            names = ", ".join(path.name for path in backups)
            raise DeliveryError(
                f"cannot recover Result Package {run_id}: multiple backups found: {names}"
            )
        if backups:
            try:
                os.replace(backups[0], target)
            except OSError as exc:
                raise DeliveryError(
                    f"could not restore Result Package backup {backups[0]}"
                ) from exc
            backups = []

    # With a formal target present, matching backups are from a completed
    # replacement immediately before a crash. Temporaries were never
    # published and can be discarded before starting the next attempt.
    for stale in (*backups, *temporaries):
        shutil.rmtree(stale, ignore_errors=True)

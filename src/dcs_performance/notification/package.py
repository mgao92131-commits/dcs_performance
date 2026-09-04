"""Read-only parser for a published Result Package.

This module intentionally has no imports from the DCS client, assessment
engine, or Excel/reporting code.  A notification is built solely from the
package's ``result.json`` and the referenced PNG files.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import TEAM_LABELS


class ResultPackageError(ValueError):
    """Raised when a Result Package cannot be safely used for email."""


@dataclass(frozen=True)
class PackageEvent:
    """An event copied from one Result Package point."""

    event_type: str
    start: str | None
    end: str | None
    duration_seconds: float | None
    score: float | None
    message: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class DeductionPoint:
    """One point whose package score is positive and therefore is displayed."""

    rule_id: str
    rule_name: str
    point_id: str
    score: float
    event_count: int
    status: str
    data_status: str
    image_relative_path: str
    image_path: Path
    events: tuple[PackageEvent, ...]


@dataclass(frozen=True)
class ResultPackage:
    """Validated, immutable view of the business data used in an email."""

    package_path: Path
    result_json_path: Path
    run_id: str
    team_id: str
    team_label: str
    shift_type: str
    shift_start: str
    shift_end: str
    generated_at: str | None
    total_score: float
    rule_count: int
    point_count: int
    event_count: int
    deductions: tuple[DeductionPoint, ...]

    @property
    def deduction_count(self) -> int:
        return len(self.deductions)

    @property
    def has_deductions(self) -> bool:
        return bool(self.deductions)


def parse_result_package(path: str | Path) -> ResultPackage:
    """Parse a package directory or its ``result.json`` without external I/O.

    Only positive-score points are loaded as deductions.  Images for those
    points must be real PNG files located inside the package directory.
    """

    supplied = Path(path).expanduser()
    if supplied.is_file():
        result_json = supplied
        package_path = supplied.parent
    elif supplied.is_dir():
        package_path = supplied
        result_json = supplied / "result.json"
    else:
        raise ResultPackageError(f"Result Package 不存在: {supplied}")
    if result_json.name != "result.json" or not result_json.is_file():
        raise ResultPackageError(f"Result Package 缺少 result.json: {result_json}")
    package_path = package_path.resolve()
    result_json = result_json.resolve()
    try:
        document = json.loads(result_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResultPackageError(f"无法读取 Result Package: {result_json}: {exc}") from exc
    root = _mapping(document, "$", ResultPackageError)

    if root.get("schema_version") != "1.0":
        raise ResultPackageError("Result Package schema_version 必须为 1.0")
    if root.get("time_basis") != "local":
        raise ResultPackageError("Result Package time_basis 必须为 local")

    run = _mapping(root.get("run"), "$.run", ResultPackageError)
    run_id = _text(run.get("run_id"), "$.run.run_id", ResultPackageError)
    generated_at = _optional_text(run.get("generated_at"), "$.run.generated_at", ResultPackageError)
    shift = _mapping(root.get("shift"), "$.shift", ResultPackageError)
    team_id = _text(shift.get("team_id"), "$.shift.team_id", ResultPackageError).upper()
    if team_id not in TEAM_LABELS:
        raise ResultPackageError(f"Result Package 的 team_id 无效: {team_id!r}")
    shift_type = _text(shift.get("shift_type"), "$.shift.shift_type", ResultPackageError)
    if shift_type not in {"day", "night"}:
        raise ResultPackageError(f"Result Package 的 shift_type 无效: {shift_type!r}")
    shift_start = _text(shift.get("start"), "$.shift.start", ResultPackageError)
    shift_end = _text(shift.get("end"), "$.shift.end", ResultPackageError)

    summary = _mapping(root.get("summary"), "$.summary", ResultPackageError)
    rule_count = _nonnegative_int(summary.get("rule_count"), "$.summary.rule_count")
    point_count = _nonnegative_int(summary.get("point_count"), "$.summary.point_count")
    event_count = _nonnegative_int(summary.get("event_count"), "$.summary.event_count")
    total_score = _number(summary.get("total_score"), "$.summary.total_score")

    rules = root.get("rules")
    if not isinstance(rules, list):
        raise ResultPackageError("Result Package rules 必须是数组")
    if rule_count != len(rules):
        raise ResultPackageError(
            f"Result Package summary.rule_count 与 rules 数量不一致: {rule_count} != {len(rules)}"
        )

    deductions: list[DeductionPoint] = []
    seen_keys: set[tuple[str, str]] = set()
    actual_points = 0
    actual_events = 0
    for rule_index, raw_rule in enumerate(rules):
        rule_context = f"$.rules[{rule_index}]"
        rule = _mapping(raw_rule, rule_context, ResultPackageError)
        rule_id = _text(rule.get("rule_id"), f"{rule_context}.rule_id", ResultPackageError)
        rule_name = _optional_text(rule.get("rule_name"), f"{rule_context}.rule_name", ResultPackageError) or rule_id
        rule_events = _nonnegative_int(rule.get("event_count"), f"{rule_context}.event_count")
        rule_score = _number(rule.get("score"), f"{rule_context}.score")
        points = rule.get("points")
        if not isinstance(points, list):
            raise ResultPackageError(f"{rule_context}.points 必须是数组")
        calculated_rule_events = 0
        calculated_rule_score = 0.0
        for point_index, raw_point in enumerate(points):
            point_context = f"{rule_context}.points[{point_index}]"
            point = _mapping(raw_point, point_context, ResultPackageError)
            point_id = _text(point.get("point_id"), f"{point_context}.point_id", ResultPackageError)
            key = (rule_id, point_id)
            if key in seen_keys:
                raise ResultPackageError(f"Result Package 中点位重复: {rule_id}/{point_id}")
            seen_keys.add(key)
            status = _text(point.get("status"), f"{point_context}.status", ResultPackageError)
            if status not in {"normal", "violation"}:
                raise ResultPackageError(f"{point_context}.status 无效: {status}")
            data_status = _text(point.get("data_status"), f"{point_context}.data_status", ResultPackageError)
            if data_status not in {"ok", "partial", "no_data"}:
                raise ResultPackageError(f"{point_context}.data_status 无效: {data_status}")
            event_count_value = _nonnegative_int(point.get("event_count"), f"{point_context}.event_count")
            score = _number(point.get("score"), f"{point_context}.score")
            events = _parse_events(point.get("events"), point_context, rule_id)
            if len(events) != event_count_value:
                raise ResultPackageError(
                    f"{point_context}.events 与 event_count 不一致"
                )
            expected_status = "violation" if event_count_value else "normal"
            if status != expected_status:
                raise ResultPackageError(
                    f"{point_context}.status 与 event_count 不一致"
                )
            actual_points += 1
            actual_events += event_count_value
            calculated_rule_events += event_count_value
            calculated_rule_score += score
            raw_image = point.get("image", point.get("image_path"))
            image_relative: str | None = None
            image_path: Path | None = None
            if raw_image is not None:
                image_relative = _image_relative_path(raw_image, f"{point_context}.image")
                image_path = _safe_image_path(package_path, image_relative)
                _validate_png(image_path, f"{point_context}.image")
            if score <= 0:
                continue
            if image_relative is None or image_path is None:
                raise ResultPackageError(f"{point_context}.image 必须是正分点位的 PNG")
            deductions.append(
                DeductionPoint(
                    rule_id=rule_id,
                    rule_name=rule_name,
                    point_id=point_id,
                    score=score,
                    event_count=event_count_value,
                    status=status,
                    data_status=data_status,
                    image_relative_path=image_relative,
                    image_path=image_path,
                    events=events,
                )
            )
        if calculated_rule_events != rule_events:
            raise ResultPackageError(f"{rule_context}.event_count 汇总不一致")
        if not math.isclose(calculated_rule_score, rule_score, abs_tol=1e-9):
            raise ResultPackageError(f"{rule_context}.score 汇总不一致")

    if actual_points != point_count:
        raise ResultPackageError("Result Package summary.point_count 汇总不一致")
    if actual_events != event_count:
        raise ResultPackageError("Result Package summary.event_count 汇总不一致")

    return ResultPackage(
        package_path=package_path,
        result_json_path=result_json,
        run_id=run_id,
        team_id=team_id,
        team_label=TEAM_LABELS[team_id],
        shift_type=shift_type,
        shift_start=shift_start,
        shift_end=shift_end,
        generated_at=generated_at,
        total_score=total_score,
        rule_count=rule_count,
        point_count=point_count,
        event_count=event_count,
        deductions=tuple(deductions),
    )


def _parse_events(raw: object, context: str, rule_id: str) -> tuple[PackageEvent, ...]:
    if not isinstance(raw, list):
        raise ResultPackageError(f"{context}.events 必须是数组")
    events: list[PackageEvent] = []
    for index, raw_event in enumerate(raw):
        event_context = f"{context}.events[{index}]"
        event = _mapping(raw_event, event_context, ResultPackageError)
        event_type = (
            _optional_text(
                event.get("event_type"),
                f"{event_context}.event_type",
                ResultPackageError,
            )
            or rule_id
        )
        start = _optional_text(event.get("start"), f"{event_context}.start", ResultPackageError)
        end = _optional_text(event.get("end"), f"{event_context}.end", ResultPackageError)
        duration = event.get("duration_seconds")
        duration_value = None if duration is None else _number(duration, f"{event_context}.duration_seconds")
        score = event.get("score")
        score_value = None if score is None else _number(score, f"{event_context}.score")
        message = event.get("message", "")
        if not isinstance(message, str):
            raise ResultPackageError(f"{event_context}.message 必须是字符串")
        data = event.get("data", {})
        if not isinstance(data, Mapping):
            raise ResultPackageError(f"{event_context}.data 必须是对象")
        events.append(PackageEvent(event_type, start, end, duration_value, score_value, message, dict(data)))
    return tuple(events)


def _image_relative_path(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResultPackageError(f"{context} 必须是非空路径")
    text = value.strip().replace("\\", "/")
    path = Path(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ResultPackageError(f"{context} 必须是包内相对路径")
    if not text.lower().startswith("images/") or path.suffix.lower() != ".png":
        raise ResultPackageError(f"{context} 必须指向 images/*.png")
    return "/".join(path.parts)


def _safe_image_path(package_path: Path, relative: str) -> Path:
    candidate = (package_path / Path(relative)).resolve()
    try:
        candidate.relative_to(package_path)
    except ValueError as exc:
        raise ResultPackageError(f"图片路径越过 Result Package 边界: {relative}") from exc
    if not candidate.is_file():
        raise ResultPackageError(f"Result Package 缺少图片: {relative}")
    return candidate


def _validate_png(path: Path, context: str) -> None:
    try:
        with path.open("rb") as handle:
            signature = handle.read(8)
    except OSError as exc:
        raise ResultPackageError(f"无法读取 {context}: {path}") from exc
    if signature != b"\x89PNG\r\n\x1a\n":
        raise ResultPackageError(f"{context} 不是有效 PNG: {path}")


def _mapping(value: object, context: str, error_type: type[Exception]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise error_type(f"{context} 必须是对象")
    return value


def _text(value: object, context: str, error_type: type[Exception]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error_type(f"{context} 必须是非空字符串")
    return value.strip()


def _optional_text(value: object, context: str, error_type: type[Exception]) -> str | None:
    if value in (None, ""):
        return None
    return _text(value, context, error_type)


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResultPackageError(f"{context} 必须是数字")
    result = float(value)
    if not math.isfinite(result):
        raise ResultPackageError(f"{context} 必须是有限数字")
    return result


def _nonnegative_int(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResultPackageError(f"{context} 必须是非负整数")
    return value


__all__ = [
    "DeductionPoint",
    "PackageEvent",
    "ResultPackage",
    "ResultPackageError",
    "parse_package",
    "parse_result_package",
]


parse_package = parse_result_package

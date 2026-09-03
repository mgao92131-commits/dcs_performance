"""Strict JSON V1 serialization for result packages."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Any

from .models import DeliveryResult, RuleAssessmentResult


SCHEMA_VERSION = "1.0"


def build_result_document(
    delivery: DeliveryResult,
    rules: tuple[RuleAssessmentResult, ...],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "run_id": delivery.run_id,
            "generated_at": delivery.generated_at,
        },
        "time_basis": "local",
        "shift": {
            "team_id": delivery.shift.team_id,
            "shift_type": delivery.shift.shift_type,
            "start": delivery.shift.start_time,
            "end": delivery.shift.end_time,
        },
        "summary": {
            "rule_count": delivery.rule_count,
            "point_count": delivery.point_count,
            "event_count": delivery.event_count,
            "total_score": delivery.total_score,
        },
        "rules": [_rule_document(rule) for rule in rules],
    }


def _rule_document(rule: RuleAssessmentResult) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "rule_name": rule.rule_name,
        "assessment_window": {
            "start": rule.window.start_time,
            "end": rule.window.end_time,
        },
        "event_count": rule.event_count,
        "score": rule.score,
        "points": [
            {
                "point_id": point.point_id,
                "status": point.status,
                "data_status": point.data_status,
                "event_count": point.event_count,
                "score": point.score,
                "image": point.image_path.replace("\\", "/"),
                "events": [_event_document(event) for event in point.events],
                **({"metadata": point.metadata} if point.metadata else {}),
            }
            for point in rule.points
        ],
    }


def _event_document(event: Any) -> dict[str, Any]:
    event_type = event.data.get("event_type") or event.rule_id
    if event_type in {"flow_balance", "level_rate"}:
        direction = event.data.get("direction")
        if isinstance(direction, str) and direction:
            event_type = direction
    return {
        "event_type": event_type,
        "start": event.event_start,
        "end": event.event_end,
        "duration_seconds": (event.event_end - event.event_start).total_seconds(),
        "score": event.score,
        "message": event.message,
        "data": event.data,
    }


def normalize_json_value(value: Any, *, path: str = "$") -> Any:
    """Normalize supported business values; never silently stringify objects."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"non-finite number at {path}")
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"JSON object key at {path} must be text")
            result[key] = normalize_json_value(item, path=f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [
            normalize_json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(f"unsupported JSON value at {path}: {type(value).__name__}")


def write_result_json(path: Path, document: Mapping[str, Any]) -> None:
    normalized = normalize_json_value(document)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            normalized,
            handle,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        handle.write("\n")
        handle.flush()
        import os

        os.fsync(handle.fileno())
    temporary.replace(path)

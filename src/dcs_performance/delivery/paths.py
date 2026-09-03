"""Deterministic and portable result-package names."""

import re
from datetime import datetime


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_filename(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("filename identity must be non-empty text")
    sanitized = _UNSAFE.sub("_", value)
    if sanitized in {".", ".."}:
        sanitized = sanitized.replace(".", "_")
    return sanitized


def build_run_id(start: datetime, end: datetime, team_id: str) -> str:
    return (
        f"{start.strftime('%Y%m%dT%H%M%S')}_"
        f"{end.strftime('%Y%m%dT%H%M%S')}_"
        f"{sanitize_filename(team_id)}"
    )


def point_image_filename(rule_id: str, point_id: str) -> str:
    return f"{sanitize_filename(rule_id)}__{sanitize_filename(point_id)}.png"

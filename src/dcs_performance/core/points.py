"""Shared selection and validation for point-aware assessment rules."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import TypeVar


PointT = TypeVar("PointT")


def select_points(
    points: Iterable[PointT],
    point_ids: Collection[str] | None,
    *,
    rule_id: str,
) -> tuple[PointT, ...]:
    """Select enabled configured points for one rule evaluation.

    ``None`` means all enabled points.  An explicit collection is a strict
    subset request: every ID must exist and be enabled.  The returned order is
    the order of ``point_ids`` so callers can keep deterministic query and
    event ordering.  Duplicate IDs are collapsed to avoid duplicate work.
    """

    inventory = tuple(points)
    by_id: dict[str, PointT] = {}
    enabled_by_id: dict[str, bool] = {}
    for point in inventory:
        point_id = _point_id(point)
        if point_id is None:
            raise ValueError(f"rule {rule_id} contains a point without an id")
        by_id[point_id] = point
        enabled_by_id[point_id] = _point_enabled(point)

    if point_ids is None:
        return tuple(point for point in inventory if _point_enabled(point))

    if isinstance(point_ids, (str, bytes)):
        raise TypeError("point_ids must be a collection of point ID strings")

    selected: list[PointT] = []
    seen: set[str] = set()
    for point_id in point_ids:
        if not isinstance(point_id, str) or not point_id:
            raise ValueError("point_ids must contain non-empty point ID strings")
        if point_id not in by_id:
            raise ValueError(
                f"unknown point_id {point_id!r} for rule {rule_id}"
            )
        if not enabled_by_id[point_id]:
            raise ValueError(
                f"disabled point_id {point_id!r} for rule {rule_id}"
            )
        if point_id not in seen:
            selected.append(by_id[point_id])
            seen.add(point_id)
    return tuple(selected)


def _point_id(point: object) -> str | None:
    if isinstance(point, Mapping):
        value = point.get("id")
    else:
        value = getattr(point, "id", None)
    return value if isinstance(value, str) and value else None


def _point_enabled(point: object) -> bool:
    if isinstance(point, Mapping):
        value = point.get("enabled", True)
    else:
        value = getattr(point, "enabled", True)
    if not isinstance(value, bool):
        raise ValueError("point enabled must be boolean")
    return value

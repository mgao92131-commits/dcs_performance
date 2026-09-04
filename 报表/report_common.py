"""Shared schedule helpers for the monthly report scripts."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_SRC = PROJECT_ROOT / "src"
DEFAULT_SCHEDULE_PATH = (
    PROJECT_SRC
    / "dcs_performance"
    / "shifts"
    / "performance_schedule.json"
)


def load_production_calendar(
    schedule_path: Path | None = None,
) -> tuple[Any, Any, Path]:
    """Load the project's production cyclic calendar.

    The report scripts can be run directly from this directory, so add the
    local ``src`` directory only when the project package is not installed.
    The schedule is deliberately a hard requirement: a report must not fall
    back to a different shift model.
    """

    path = (schedule_path or DEFAULT_SCHEDULE_PATH).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"生产排班配置不存在: {path}")

    source = str(PROJECT_SRC.resolve())
    if source not in sys.path:
        sys.path.insert(0, source)

    try:
        from dcs_performance.shifts import (
            Cyclic12HourShiftCalendar,
            load_performance_schedule_config,
        )
    except ImportError as exc:
        raise RuntimeError(
            "无法加载项目排班模块，请从项目环境运行或安装 dcs-performance"
        ) from exc

    config = load_performance_schedule_config(path)
    return config, Cyclic12HourShiftCalendar(config), path


def report_date_for_shift(shift: Any) -> date:
    """Return the report date for a shift: night is counted on its end date."""

    if shift.shift_type == "night":
        return shift.end_time.date()
    return shift.start_time.date()


def shifts_for_date(calendar: Any, config: Any, day: date) -> list[Any]:
    """Return report-day shifts in the required night-then-day order."""

    start = datetime.combine(day - timedelta(days=1), config.reference_start.time())
    end = datetime.combine(day + timedelta(days=1), config.reference_start.time())
    shifts = [
        shift
        for shift in calendar.get_shifts(start, end)
        if report_date_for_shift(shift) == day
    ]
    shifts.sort(
        key=lambda shift: (
            0 if shift.shift_type == "night" else 1,
            shift.start_time,
        )
    )
    if len(shifts) != 2:
        raise ValueError(
            f"生产排班在 {day.isoformat()} 未生成恰好两个班次: {shifts!r}"
        )
    return shifts


def parse_local_datetime(value: object, field_name: str) -> datetime:
    """Parse one timezone-naive ISO datetime from a Result Package."""

    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} 必须是本地 ISO datetime 字符串")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} 不是有效的 ISO datetime: {value!r}") from exc
    if parsed.tzinfo is not None:
        raise ValueError(f"{field_name} 不得包含时区偏移")
    return parsed


def expected_run_id(start_time: datetime, end_time: datetime, team_id: str) -> str:
    """Return the deterministic Result Package run ID used by the project."""

    return (
        f"{start_time.strftime('%Y%m%dT%H%M%S')}_"
        f"{end_time.strftime('%Y%m%dT%H%M%S')}_"
        f"{team_id}"
    )


def concise_team_name(config: Any, team_id: str) -> str:
    """Use the configured team name while keeping the visible header short."""

    name = str(config.team_names.get(team_id, team_id))
    return name.removesuffix("班") or team_id


def save_workbook_atomically(workbook: Any, output_file: Path) -> None:
    """Save an Excel workbook completely before replacing its target file."""

    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{output_file.stem}.tmp-",
        suffix=output_file.suffix,
        dir=output_file.parent,
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        workbook.save(str(temp_path))
        os.replace(str(temp_path), str(output_file))
    finally:
        if temp_path.exists():
            temp_path.unlink()

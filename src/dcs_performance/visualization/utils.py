"""Small, stateless plotting helpers shared by rule visualizers."""

from __future__ import annotations

from collections.abc import Iterable
from math import isfinite
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "Arial Unicode MS",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

from dcs_performance.data.history_quality import is_usable_history_sample
from dcs_performance.data.models import HistorySample

from .models import PointVisualizationContext, VisualizationArtifact


EVENT_COLORS = {
    "low_limit": "#d62728",
    "high_limit": "#ff7f0e",
    "persistent_high": "#d62728",
    "low_flow": "#d62728",
    "switch_timeout": "#9467bd",
    "rate_down": "#1f77b4",
    "rate_up": "#d62728",
    "flow_low": "#1f77b4",
    "flow_high": "#d62728",
    "viscosity_low": "#1f77b4",
    "viscosity_high": "#d62728",
    "stability_deviation": "#ff7f0e",
    "trend_drift": "#9467bd",
}


def close_figures_after(function):
    """Ensure a failing render cannot leak figures in a batch run."""

    def wrapped(*args, **kwargs):
        existing = set(plt.get_fignums())
        try:
            return function(*args, **kwargs)
        finally:
            for number in set(plt.get_fignums()) - existing:
                plt.close(number)

    return wrapped


def numeric_series(samples: Iterable[HistorySample]) -> tuple[list, list[float]]:
    times, values = [], []
    for sample in samples:
        if not is_usable_history_sample(sample):
            continue
        try:
            value = float(sample.value)
        except (TypeError, ValueError):
            continue
        if isfinite(value):
            times.append(sample.timestamp)
            values.append(value)
    return times, values


def event_type(event) -> str:
    value = event.data.get("event_type") or event.rule_id
    if value in {"flow_balance", "level_rate"}:
        value = event.data.get("direction", value)
    return str(value)


def decorate_axes(axes, context: PointVisualizationContext) -> None:
    axis_list = list(axes) if isinstance(axes, (list, tuple)) else [axes]
    for axis in axis_list:
        labels: set[str] = set()
        for event in context.events:
            kind = event_type(event)
            severity = event.data.get("severity")
            label = f"Event: {kind}"
            if isinstance(severity, str) and severity:
                label += f" ({severity})"
            axis.axvspan(
                event.event_start,
                event.event_end,
                color=EVENT_COLORS.get(kind, "#d62728"),
                alpha=0.20,
                label=label if label not in labels else None,
            )
            labels.add(label)
        if context.window.start_time < context.shift.start_time < context.window.end_time:
            axis.axvline(context.shift.start_time, color="#555", ls=":", label="Shift Start")
        if context.window.start_time < context.shift.end_time < context.window.end_time:
            axis.axvline(context.shift.end_time, color="#777", ls=":", label="Shift End")
        axis.set_xlim(context.window.start_time, context.window.end_time)
        axis.grid(True, alpha=0.22)
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))


def finish_figure(
    fig,
    axes,
    context: PointVisualizationContext,
    output_path: Path,
    *,
    data_status: str,
    note: str | None = None,
    metadata=None,
) -> VisualizationArtifact:
    axis_list = list(axes) if isinstance(axes, (list, tuple)) else [axes]
    try:
        decorate_axes(axis_list, context)
        if data_status == "no_data":
            axis_list[0].text(
                0.5,
                0.5,
                "No valid history data",
                transform=axis_list[0].transAxes,
                ha="center",
                va="center",
                fontsize=14,
                color="#a33",
            )
        title = (
            f"{context.rule_name} | {context.point_id} | Team {context.shift.team_id}\n"
            f"Shift {context.shift.start_time.isoformat()} — {context.shift.end_time.isoformat()} | "
            f"Events {len(context.events)} | Score {context.score:g}"
        )
        if note:
            title += f" | {note}"
        fig.suptitle(title, fontsize=11)
        for axis in axis_list:
            handles, labels = axis.get_legend_handles_labels()
            if handles:
                axis.legend(loc="best", fontsize=8)
        fig.autofmt_xdate()
        fig.tight_layout(rect=(0, 0, 1, 0.91))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=150, format="png", bbox_inches="tight")
    finally:
        plt.close(fig)
    return VisualizationArtifact(
        image_path=f"images/{output_path.name}",
        data_status=data_status,
        metadata=metadata or {},
    )


def new_figure(rows: int = 1):
    fig, axes = plt.subplots(rows, 1, figsize=(12, 4.5 if rows == 1 else 3.5 * rows), sharex=rows > 1)
    return fig, ([axes] if rows == 1 else list(axes))

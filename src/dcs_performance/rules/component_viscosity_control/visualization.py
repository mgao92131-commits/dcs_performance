from collections.abc import Mapping
from datetime import datetime
from math import isfinite

from dcs_performance.rules.component_viscosity_control.config import parse_config
from dcs_performance.rules.component_viscosity_control.rule import build_history_query_range
from dcs_performance.rules.component_viscosity_control.detector import (
    DisturbanceWindow,
    aggregate_minute_medians,
    calculate_metric,
    detect_disturbance_windows,
)
from dcs_performance.visualization.utils import close_figures_after, finish_figure, new_figure, numeric_series


class Visualizer:
    @close_figures_after
    def render_point(self, context, output_path):
        point = next(p for p in parse_config(context.rule_config).points if p.id == context.point_id)
        query_start, query_end = build_history_query_range(
            point, context.window.start_time, context.window.end_time
        )
        samples = context.data_client.get_history(point.history_tag, query_start, query_end)
        raw_t, raw_v = numeric_series(samples)
        aggregated = aggregate_minute_medians(
            samples,
            bucket_seconds=point.aggregation.bucket_seconds,
            min_samples=point.aggregation.min_samples,
        )
        metric = calculate_metric(
            samples,
            bucket_seconds=point.aggregation.bucket_seconds,
            bucket_min_samples=point.aggregation.min_samples,
            smoothing_enabled=point.smoothing.enabled,
            window_seconds=point.smoothing.window_seconds,
            smoothing_min_samples=point.smoothing.min_samples,
        )
        exclusions = detect_disturbance_windows(
            metric, point.exclusion, bucket_seconds=point.aggregation.bucket_seconds
        )
        known = {
            (item.core_start, item.core_end, item.remove_start, item.remove_end)
            for item in exclusions
        }
        for event in context.events:
            raw_exclusion = event.data.get("exclusion")
            windows = raw_exclusion.get("windows", []) if isinstance(raw_exclusion, dict) else []
            for window in windows:
                if not isinstance(window, dict):
                    continue
                values = (
                    window.get("core_start"), window.get("core_end"),
                    window.get("remove_start"), window.get("remove_end"),
                )
                if all(value is not None for value in values) and values not in known:
                    exclusions.append(DisturbanceWindow(*values))
                    known.add(values)
        fig, axes = new_figure()
        ax = axes[0]
        if raw_t:
            ax.plot(raw_t, raw_v, color="#aaa", lw=0.6, alpha=0.55, label="Raw value")
        if aggregated:
            ax.plot([x.timestamp for x in aggregated], [x.value for x in aggregated], color="#ff7f0e", lw=0.9, label="Aggregated value")
        if metric:
            ax.plot([x.timestamp for x in metric], [x.value for x in metric], color="#1f77b4", lw=1.4, label="Smoothed value")
        ax.axhline(point.assessment.target, color="#2ca02c", label="Target")
        ax.axhline(point.assessment.low_limit, color="#1f77b4", ls="--", label="Low limit")
        ax.axhline(point.assessment.high_limit, color="#d62728", ls="--", label="High limit")
        for index, window in enumerate(exclusions):
            ax.axvspan(window.remove_start, window.remove_end, color="#777", alpha=0.16, hatch="//", label="Excluded / unstable section" if index == 0 else None)
        penalty_units, checkpoint_count = _draw_penalty_checkpoints(ax, context)
        ax.set_ylabel("Value")
        return finish_figure(
            fig, axes, context, output_path,
            data_status="ok" if raw_t else "no_data",
            note=f"Penalties {penalty_units:g}",
            metadata={
                "exclusion_window_count": len(exclusions),
                "penalty_unit_count": penalty_units,
                "penalty_checkpoint_count": checkpoint_count,
            },
        )


def _draw_penalty_checkpoints(ax, context) -> tuple[int, int]:
    """Draw light checkpoint markers without splitting event spans."""

    total_units = 0
    checkpoint_count = 0
    labels: set[str] = set()
    for event in context.events:
        penalty = event.data.get("penalty")
        if not isinstance(penalty, Mapping):
            continue

        units = penalty.get("units")
        if (
            isinstance(units, (int, float))
            and not isinstance(units, bool)
            and isfinite(float(units))
            and units >= 0
            and float(units).is_integer()
        ):
            total_units += int(units)

        checkpoints = penalty.get("checkpoints", ())
        if not isinstance(checkpoints, (list, tuple)):
            continue
        for raw_checkpoint in checkpoints:
            checkpoint = _checkpoint_datetime(raw_checkpoint)
            if checkpoint is None:
                continue
            label = "Penalty checkpoint"
            ax.axvline(
                checkpoint,
                color="#6b7280",
                ls=":",
                lw=0.8,
                alpha=0.58,
                label=label if label not in labels else None,
            )
            labels.add(label)
            checkpoint_count += 1

    return total_units, checkpoint_count


def _checkpoint_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None

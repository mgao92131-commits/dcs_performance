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
        ax.set_ylabel("Value")
        return finish_figure(
            fig, axes, context, output_path,
            data_status="ok" if raw_t else "no_data",
            metadata={"exclusion_window_count": len(exclusions)},
        )

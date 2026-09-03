from dcs_performance.rules.analog_trend_stability.config import parse_config
from dcs_performance.rules.analog_trend_stability.rule import QueryPlanner
from dcs_performance.rules.analog_trend_stability.trend import (
    calculate_drift,
    calculate_trends_for_segments,
    split_numeric_segments,
)
from dcs_performance.visualization.utils import close_figures_after, finish_figure, new_figure, numeric_series


class Visualizer:
    @close_figures_after
    def render_point(self, context, output_path):
        point = next(p for p in parse_config(context.rule_config).points if p.id == context.point_id)
        plan = QueryPlanner.plan(
            (replace(point, enabled=True),),
            context.window.start_time,
            context.window.end_time,
        )[0]
        histories = context.data_client.get_histories(
            list(plan.tags), plan.query_start, plan.query_end
        )
        samples = histories.get(point.history_tag, [])
        raw_t, raw_v = numeric_series(samples)
        segments = split_numeric_segments(samples, point.quality.max_gap_seconds)
        trends = calculate_trends_for_segments(segments, point.trend)
        rows = 3 if point.drift.enabled else 2
        fig, axes = new_figure(rows)
        if raw_t:
            axes[0].plot(raw_t, raw_v, color="#999", lw=0.7, label="Raw value")
        if trends:
            times = [x.timestamp for x in trends]
            axes[0].plot(times, [x.trend for x in trends], color="#1f77b4", label="Trend value")
            axes[1].plot(times, [x.deviation for x in trends], color="#ff7f0e", label="Deviation from trend")
        axes[0].set_ylabel("Value")
        if point.stability.enabled:
            for threshold, label, color in (
                (point.stability.warning_deviation, "Warning threshold", "#ff7f0e"),
                (point.stability.high_deviation, "High threshold", "#d62728"),
            ):
                axes[1].axhline(threshold, color=color, ls="--", label=label)
                axes[1].axhline(-threshold, color=color, ls="--")
        axes[1].set_ylabel("Deviation")
        if point.drift.enabled:
            drift = calculate_drift(trends, point.drift)
            for window in point.drift.windows:
                selected = [item for item in drift if item.window_id == window.id]
                if selected:
                    axes[2].plot([x.timestamp for x in selected], [x.change for x in selected], label=f"Drift {window.id}")
                for threshold, label, color in (
                    (window.warning_change, f"{window.id} warning", "#ff7f0e"),
                    (window.high_change, f"{window.id} high", "#d62728"),
                ):
                    axes[2].axhline(threshold, color=color, ls="--", alpha=0.7, label=label)
                    axes[2].axhline(-threshold, color=color, ls="--", alpha=0.7)
            axes[2].set_ylabel("Trend change")
        return finish_figure(fig, axes, context, output_path, data_status="ok" if raw_t else "no_data")
from dataclasses import replace

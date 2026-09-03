from dcs_performance.rules.level_rate_compliance.config import parse_config
from dcs_performance.rules.level_rate_compliance.detector import calculate_rate_points
from dcs_performance.visualization.utils import close_figures_after, finish_figure, new_figure, numeric_series


class Visualizer:
    @close_figures_after
    def render_point(self, context, output_path):
        point = next(p for p in parse_config(context.rule_config).points if p.id == context.point_id)
        lookback = point.rate_window_seconds + point.smoothing.window_seconds + point.max_gap_seconds
        samples = context.data_client.get_history(
            point.history_tag,
            context.window.start_time - timedelta(seconds=lookback),
            context.window.end_time,
        )
        raw_t, raw_v = numeric_series(samples)
        rate_points = calculate_rate_points(samples, point)
        fig, axes = new_figure(2)
        if raw_t:
            axes[0].plot(raw_t, raw_v, color="#888", lw=0.8, label="Raw level")
        if rate_points:
            axes[0].plot(
                [item.timestamp for item in rate_points],
                [item.smoothed_level for item in rate_points],
                color="#1f77b4", label="Smoothed level",
            )
            axes[1].plot(
                [item.timestamp for item in rate_points],
                [item.rate_per_hour for item in rate_points],
                color="#2ca02c", label="Calculated rate",
            )
        axes[0].set_ylabel("Level")
        axes[1].axhline(point.lower_rate, ls="--", color="#1f77b4", label="Lower rate")
        axes[1].axhline(point.upper_rate, ls="--", color="#d62728", label="Upper rate")
        axes[1].set_ylabel("Rate / hour")
        return finish_figure(
            fig, axes, context, output_path,
            data_status="ok" if raw_t else "no_data",
            note=(f"Rate window = {point.rate_window_seconds:g} s; "
                  f"Persistence = {point.persistence_seconds:g} s"),
        )
from datetime import timedelta

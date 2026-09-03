from datetime import timedelta

from dcs_performance.data.history_context import get_history_with_previous_sample
from dcs_performance.rules.analog_limit_exceedance.config import parse_config
from dcs_performance.rules.analog_limit_exceedance.smoothing import smooth_history_samples
from dcs_performance.visualization.utils import close_figures_after, finish_figure, new_figure, numeric_series


class Visualizer:
    @close_figures_after
    def render_point(self, context, output_path):
        typed = next(p for p in parse_config(context.rule_config).points if p.id == context.point_id)
        lookback = typed.smoothing.window_seconds if typed.smoothing.enabled else 0
        samples = get_history_with_previous_sample(
            context.data_client,
            typed.history_tag,
            context.window.start_time - timedelta(seconds=lookback),
            context.window.end_time,
        )
        times, values = numeric_series(samples)
        fig, axes = new_figure()
        ax = axes[0]
        if times:
            ax.plot(times, values, color="#777", lw=0.8, alpha=0.65, label="Raw")
        if typed.smoothing.enabled:
            smooth_times, smooth_values = numeric_series(smooth_history_samples(samples, typed.smoothing))
            if smooth_times:
                ax.plot(smooth_times, smooth_values, color="#1f77b4", lw=1.4, label="Smoothed")
        if typed.low.enabled:
            ax.axhline(typed.low.limit, color="#d62728", ls="--", label=f"Low limit {typed.low.limit:g}")
        if typed.high.enabled:
            ax.axhline(typed.high.limit, color="#ff7f0e", ls="--", label=f"High limit {typed.high.limit:g}")
        ax.set_ylabel("Value")
        return finish_figure(fig, axes, context, output_path, data_status="ok" if times else "no_data")

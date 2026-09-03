from dcs_performance.rules.flow_balance_compliance.config import parse_config
from dcs_performance.rules.flow_balance_compliance.detector import calculate_flow_balance_points
from dcs_performance.visualization.utils import close_figures_after, finish_figure, new_figure, numeric_series


class Visualizer:
    @close_figures_after
    def render_point(self, context, output_path):
        cfg = next(p for p in parse_config(context.rule_config).points if p.id == context.point_id)
        tags = [cfg.logic_tag, *cfg.sy_tags]
        lookback = cfg.smoothing.window_seconds + cfg.max_gap_seconds
        histories = context.data_client.get_histories(
            tags,
            context.window.start_time - timedelta(seconds=lookback),
            context.window.end_time,
        )
        raw = {tag: numeric_series(histories.get(tag, [])) for tag in tags}
        evidence = calculate_flow_balance_points(histories, cfg)
        fig, axes = new_figure(2)
        logic_t, logic_v = raw[cfg.logic_tag]
        if logic_t:
            axes[0].plot(logic_t, logic_v, alpha=0.45, lw=0.8, label="Logic flow (raw)")
        if evidence:
            times = [item.timestamp for item in evidence]
            axes[0].plot(times, [item.logic_flow for item in evidence], label="Logic flow")
            axes[0].plot(times, [item.sy_total for item in evidence], label="sum(SY flows)")
            axes[1].plot(times, [item.difference for item in evidence], color="#2ca02c", label="Deviation (logic - SY sum)")
        axes[0].set_ylabel("Flow")
        axes[1].axhline(cfg.low_limit, ls="--", color="#1f77b4", label="Low limit")
        axes[1].axhline(cfg.high_limit, ls="--", color="#d62728", label="High limit")
        axes[1].set_ylabel("Deviation")
        has_data = any(times for times, _ in raw.values())
        return finish_figure(fig, axes, context, output_path, data_status="ok" if has_data else "no_data")
from datetime import timedelta

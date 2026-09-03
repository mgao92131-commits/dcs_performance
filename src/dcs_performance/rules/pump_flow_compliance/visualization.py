from datetime import timedelta

from dcs_performance.data.history_context import get_histories_with_previous_samples
from dcs_performance.data.history_quality import is_usable_history_sample
from dcs_performance.rules.pump_flow_compliance.detector import parse_digital_state, parse_flow_value
from dcs_performance.visualization.utils import close_figures_after, finish_figure, new_figure


class Visualizer:
    @close_figures_after
    def render_point(self, context, output_path):
        cfg = context.point_config
        tags = [cfg["pump_a_tag"], cfg["pump_b_tag"], cfg["flow_tag"]]
        lookback = timedelta(seconds=cfg["max_switch_duration_seconds"])
        histories = get_histories_with_previous_samples(
            context.data_client,
            tags,
            context.window.start_time - lookback,
            context.window.end_time,
        )
        flow_t, flow_v = _series(histories.get(cfg["flow_tag"], []), parse_flow_value)
        a_t, a_v = _series(histories.get(cfg["pump_a_tag"], []), parse_digital_state)
        b_t, b_v = _series(histories.get(cfg["pump_b_tag"], []), parse_digital_state)
        fig, axes = new_figure(2)
        if flow_t:
            axes[0].plot(flow_t, flow_v, label="Flow", color="#1f77b4")
        axes[0].axhline(cfg["normal_min_flow"], ls="--", color="#d62728", label="Normal min flow")
        axes[0].axhline(cfg["switching_min_flow"], ls=":", color="#ff7f0e", label="Switching min flow")
        axes[0].set_ylabel("Flow")
        if a_t:
            axes[1].step(a_t, a_v, where="post", label="Pump A state")
        if b_t:
            axes[1].step(b_t, b_v, where="post", label="Pump B state")
        switch_t, switch_v = _switch_series(a_t, a_v, b_t, b_v, int(cfg.get("running_value", "1")))
        if switch_t:
            axes[1].step(
                switch_t, switch_v, where="post", color="#9467bd", ls="--",
                label="Switching state (both running or both stopped)",
            )
        axes[1].set_ylabel("Pump state")
        series_by_tag = {
            cfg["flow_tag"]: flow_t,
            cfg["pump_a_tag"]: a_t,
            cfg["pump_b_tag"]: b_t,
        }
        missing_tags = [tag for tag, times in series_by_tag.items() if not times]
        populated = len(series_by_tag) - len(missing_tags)
        data_status = "ok" if populated == 3 else "partial" if populated else "no_data"
        return finish_figure(
            fig, axes, context, output_path,
            data_status=data_status,
            note=f"Max switch duration = {cfg['max_switch_duration_seconds']:g} s",
            metadata={"missing_tags": missing_tags} if missing_tags else None,
        )


def _series(samples, parser):
    times, values = [], []
    for sample in samples:
        if not is_usable_history_sample(sample):
            continue
        try:
            value = parser(sample.value)
        except ValueError:
            continue
        times.append(sample.timestamp)
        values.append(value)
    return times, values


def _switch_series(a_times, a_values, b_times, b_values, running_value):
    changes = {}
    for timestamp, value in zip(a_times, a_values):
        changes.setdefault(timestamp, {})["a"] = value
    for timestamp, value in zip(b_times, b_values):
        changes.setdefault(timestamp, {})["b"] = value
    current_a = current_b = None
    times, values = [], []
    for timestamp in sorted(changes):
        current_a = changes[timestamp].get("a", current_a)
        current_b = changes[timestamp].get("b", current_b)
        if current_a is None or current_b is None:
            continue
        times.append(timestamp)
        values.append(int((current_a == running_value) == (current_b == running_value)))
    return times, values

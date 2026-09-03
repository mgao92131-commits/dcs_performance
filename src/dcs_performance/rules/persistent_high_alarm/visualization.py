from dcs_performance.rules.persistent_high_alarm.detector import parse_digital_state
from dcs_performance.data.history_quality import is_usable_history_sample
from dcs_performance.visualization.utils import close_figures_after, finish_figure, new_figure


class Visualizer:
    @close_figures_after
    def render_point(self, context, output_path):
        samples = get_history_with_previous_sample(
            context.data_client,
            context.point_config["history_tag"], context.window.start_time, context.window.end_time
        )
        times, states = [], []
        for sample in samples:
            if not is_usable_history_sample(sample):
                continue
            try:
                state = parse_digital_state(sample.value)
            except ValueError:
                continue
            times.append(sample.timestamp)
            states.append(state)
        parameters = context.rule_config["parameters"]
        active = int(parameters.get("active_value", "1"))
        threshold = parameters["threshold_seconds"]
        fig, axes = new_figure()
        ax = axes[0]
        if times:
            ax.step(times, states, where="post", color="#1f77b4", label="Raw digital state")
        ax.axhline(active, color="#d62728", ls="--", label=f"Active value {active}")
        ax.set_yticks(sorted({0, 1, active}))
        ax.set_ylabel("State")
        return finish_figure(
            fig, axes, context, output_path,
            data_status="ok" if times else "no_data",
            note=f"Persistent threshold = {threshold:g} s",
        )
from dcs_performance.data.history_context import get_history_with_previous_sample

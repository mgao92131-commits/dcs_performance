"""Compare LIC-117016 and LIC-217016 with the current LIC-217016 rule settings."""

from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dcs_performance.data import DEFAULT_DCS_SERVICE_BASE_URL, DcsServiceClient
from dcs_performance.data.models import HistorySample
from dcs_performance.data.parsers import parse_timestamp
from dcs_performance.rules.analog_limit_exceedance.config import (
    AnalogLimitExceedanceConfig,
    PointConfig,
    parse_config,
)
from dcs_performance.rules.analog_limit_exceedance.detector import (
    AnalogLimitExceedanceDetector,
    LimitOccurrence,
)
from dcs_performance.rules.analog_limit_exceedance.smoothing import (
    smooth_history_samples,
)


REFERENCE_ID = "LIC-217016"
REFERENCE_TAG = "LIC-217016/PID1/PV.CV"
COMPARISON_ID = "LIC-117016"
COMPARISON_TAG = "LIC-117016/PID1/PV.CV"
TAGS = (COMPARISON_TAG, REFERENCE_TAG)
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "src/dcs_performance/rules/analog_limit_exceedance/config.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare LIC-117016 and LIC-217016 using the current "
            "LIC-217016 analog-limit parameters"
        )
    )
    parser.add_argument("--base-url", default=DEFAULT_DCS_SERVICE_BASE_URL)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument(
        "--to",
        dest="end_time",
        help="source-local exclusive end time; defaults to the current local minute",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT,
        help="directory for CSV, JSON, and PNG outputs",
    )
    return parser


def load_same_rule_config() -> tuple[AnalogLimitExceedanceConfig, dict[str, PointConfig]]:
    """Load the configured 217 point and clone only its settings to 117."""

    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    configured = [
        point
        for point in raw["parameters"]["points"]
        if point.get("id") == REFERENCE_ID
    ]
    if len(configured) != 1:
        raise SystemExit(f"Expected exactly one configured {REFERENCE_ID} point")
    if not configured[0].get("enabled", True):
        raise SystemExit(f"Configured point {REFERENCE_ID} is disabled")

    comparison = copy.deepcopy(configured[0])
    comparison["id"] = COMPARISON_ID
    comparison["history_tag"] = COMPARISON_TAG
    raw["parameters"]["points"] = [configured[0], comparison]
    parsed = parse_config(raw)
    points = {point.id: point for point in parsed.points}
    return parsed, points


def valid_sample(sample: HistorySample) -> bool:
    return not (
        sample.is_history_hole
        or sample.is_cr_hole
        or sample.is_manually_deleted
        or sample.is_manually_inserted
    )


def minute_mean(samples: list[HistorySample]) -> pd.Series:
    rows = [
        (sample.timestamp, float(sample.value))
        for sample in samples
        if valid_sample(sample)
    ]
    if not rows:
        return pd.Series(dtype="float64", name="actual_value")
    frame = pd.DataFrame(rows, columns=["timestamp", "actual_value"])
    return (
        frame.groupby("timestamp", sort=True)["actual_value"]
        .mean()
        .resample("1min")
        .mean()
    )


def same_rule_smooth_series(
    samples: list[HistorySample],
    point: PointConfig,
) -> tuple[pd.Series, list[HistorySample]]:
    smoothed = smooth_history_samples(samples, point.smoothing)
    rows = [(sample.timestamp, float(sample.value)) for sample in smoothed]
    if not rows:
        return pd.Series(dtype="float64", name="smoothed_value"), smoothed
    frame = pd.DataFrame(rows, columns=["timestamp", "smoothed_value"])
    series = (
        frame.groupby("timestamp", sort=True)["smoothed_value"]
        .mean()
        .resample("1min")
        .mean()
    )
    return series, smoothed


def detect_events(
    smoothed_samples: list[HistorySample],
    point: PointConfig,
    start_time: datetime,
    visible_end: datetime,
    observation_end: datetime,
) -> list[LimitOccurrence]:
    occurrences = AnalogLimitExceedanceDetector().detect(
        smoothed_samples,
        point,
        start_time=start_time,
        observation_end=observation_end,
    )
    return [
        occurrence
        for occurrence in occurrences
        if start_time <= occurrence.start_time < visible_end
    ]


def event_rows(
    point: PointConfig,
    occurrences: list[LimitOccurrence],
    visible_end: datetime,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for occurrence in occurrences:
        event_end = occurrence.end_time or visible_end
        event_end = min(event_end, visible_end)
        rows.append(
            {
                "point_id": point.id,
                "history_tag": point.history_tag,
                "event_type": occurrence.event_type,
                "start_time": occurrence.start_time.isoformat(),
                "end_time": event_end.isoformat(),
                "duration_seconds": occurrence.duration_seconds,
                "limit": occurrence.limit,
                "extreme_value": occurrence.extreme_value,
                "extreme_time": occurrence.extreme_time.isoformat(),
                "is_open": occurrence.is_open,
                "message": (
                    f"same LIC-217016 rule: {occurrence.event_type} "
                    f"for {point.id}"
                ),
            }
        )
    return rows


def write_event_csv(rows: list[dict[str, object]], path: Path) -> None:
    fields = [
        "point_id",
        "history_tag",
        "event_type",
        "start_time",
        "end_time",
        "duration_seconds",
        "limit",
        "extreme_value",
        "extreme_time",
        "is_open",
        "message",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def quantile_summary(series: pd.Series) -> dict[str, float | None]:
    values = series.dropna()
    if values.empty:
        return {"p01": None, "p05": None, "p50": None, "p95": None, "p99": None}
    return {
        name: round(float(values.quantile(probability)), 4)
        for name, probability in (
            ("p01", 0.01),
            ("p05", 0.05),
            ("p50", 0.50),
            ("p95", 0.95),
            ("p99", 0.99),
        )
    }


def point_summary(
    point: PointConfig,
    samples: list[HistorySample],
    actual: pd.Series,
    smoothed: pd.Series,
    occurrences: list[LimitOccurrence],
    start_time: datetime,
    end_time: datetime,
) -> dict[str, object]:
    visible_actual = actual.loc[(actual.index >= start_time) & (actual.index < end_time)]
    visible_smoothed = smoothed.loc[
        (smoothed.index >= start_time) & (smoothed.index < end_time)
    ]
    events_by_type = {
        event_type: sum(
            occurrence.event_type == event_type for occurrence in occurrences
        )
        for event_type in ("low_limit", "high_limit")
    }
    return {
        "point_id": point.id,
        "history_tag": point.history_tag,
        "query_sample_count": len(samples),
        "visible_minute_count": int(visible_actual.notna().sum()),
        "visible_minute_coverage_percent": round(
            float(visible_actual.notna().mean() * 100), 2
        ),
        "actual_quantiles": quantile_summary(visible_actual),
        "same_rule_smoothed_quantiles": quantile_summary(visible_smoothed),
        "same_rule_smoothed_min": (
            round(float(visible_smoothed.min()), 4)
            if not visible_smoothed.dropna().empty
            else None
        ),
        "same_rule_smoothed_max": (
            round(float(visible_smoothed.max()), 4)
            if not visible_smoothed.dropna().empty
            else None
        ),
        "event_count": len(occurrences),
        "events_by_type": events_by_type,
    }


def plot_trends(
    trend: pd.DataFrame,
    events_by_point: dict[str, list[LimitOccurrence]],
    points: dict[str, PointConfig],
    start_time: datetime,
    end_time: datetime,
    output_path: Path,
) -> None:
    plt.rcParams.update(
        {
            "font.family": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
            "font.size": 10,
            "axes.titlesize": 13,
            "axes.labelsize": 10,
            "axes.unicode_minus": False,
        }
    )
    actual_columns = [f"{point_id}_actual_1min" for point_id in points]
    smooth_columns = [f"{point_id}_same_rule_60min" for point_id in points]
    all_values = trend[actual_columns + smooth_columns].stack().dropna()
    low = points[REFERENCE_ID].low.limit
    high = points[REFERENCE_ID].high.limit
    if all_values.empty:
        y_min, y_max = low - 1.0, high + 1.0
    else:
        y_min = min(float(all_values.min()), low)
        y_max = max(float(all_values.max()), high)
        padding = max((y_max - y_min) * 0.06, 0.1)
        y_min -= padding
        y_max += padding

    figure, axes = plt.subplots(
        2,
        1,
        figsize=(18, 10),
        sharex=True,
        sharey=True,
        constrained_layout=True,
    )
    panel_colors = {
        COMPARISON_ID: ("#94a3b8", "#2563eb"),
        REFERENCE_ID: ("#94a3b8", "#d97706"),
    }
    panel_order = (COMPARISON_ID, REFERENCE_ID)
    for axis, point_id in zip(axes, panel_order):
        actual_column = f"{point_id}_actual_1min"
        smooth_column = f"{point_id}_same_rule_60min"
        actual_color, smooth_color = panel_colors[point_id]
        axis.axhspan(low, high, color="#16a34a", alpha=0.045, zorder=0)
        axis.axhline(low, color="#dc2626", linewidth=1.1, linestyle="--")
        axis.axhline(high, color="#dc2626", linewidth=1.1, linestyle="--")
        axis.plot(
            trend.index,
            trend[actual_column],
            color=actual_color,
            linewidth=0.45,
            alpha=0.65,
            label="1-min actual mean",
        )
        axis.plot(
            trend.index,
            trend[smooth_column],
            color=smooth_color,
            linewidth=1.35,
            label="same-rule 60-min trailing mean",
        )
        for occurrence in events_by_point[point_id]:
            event_start = max(occurrence.start_time, start_time)
            event_end = min(occurrence.end_time or end_time, end_time)
            if event_end <= event_start:
                continue
            event_color = (
                "#ef4444"
                if occurrence.event_type == "low_limit"
                else "#f97316"
            )
            axis.axvspan(
                event_start,
                event_end,
                color=event_color,
                alpha=0.13,
                linewidth=0,
            )
        axis.set_title(
            f"{point_id}  |  same LIC-217016 rule  |  "
            f"events={len(events_by_point[point_id])}",
            loc="left",
            fontweight="bold",
        )
        axis.set_ylabel("PV")
        axis.set_ylim(y_min, y_max)
        axis.grid(axis="both", linewidth=0.5, alpha=0.28)

    handles = [
        Line2D([0], [0], color="#2563eb", linewidth=1.35, label="117 same-rule mean"),
        Line2D([0], [0], color="#d97706", linewidth=1.35, label="217 same-rule mean"),
        Line2D([0], [0], color="#94a3b8", linewidth=0.7, label="1-min actual mean"),
        Line2D([0], [0], color="#dc2626", linewidth=1.1, linestyle="--", label="low/high limits: 38.5 / 39.5"),
        Patch(facecolor="#ef4444", alpha=0.13, label="low-limit event"),
        Patch(facecolor="#f97316", alpha=0.13, label="high-limit event"),
    ]
    axes[0].legend(handles=handles, loc="upper right", ncol=3, frameon=False)
    axes[-1].set_xlabel("DCS source-local time (China Standard Time)")
    axes[-1].set_xlim(start_time, end_time)
    axes[-1].xaxis.set_major_locator(mdates.AutoDateLocator(minticks=7, maxticks=12))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M"))
    figure.suptitle(
        "117 / 217 liquid-level comparison with identical configured rule parameters\n"
        f"{start_time:%Y-%m-%d %H:%M} to {end_time:%Y-%m-%d %H:%M} | "
        "trailing_mean=60 min, min_samples=30, duration > 5 min",
        x=0.02,
        ha="left",
        fontsize=15,
        fontweight="bold",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def plot_distribution_and_counts(
    trend: pd.DataFrame,
    events_by_point: dict[str, list[LimitOccurrence]],
    points: dict[str, PointConfig],
    start_time: datetime,
    end_time: datetime,
    output_path: Path,
) -> None:
    low = points[REFERENCE_ID].low.limit
    high = points[REFERENCE_ID].high.limit
    values = [
        trend.loc[
            (trend.index >= start_time) & (trend.index < end_time),
            f"{point_id}_same_rule_60min",
        ].dropna()
        for point_id in (COMPARISON_ID, REFERENCE_ID)
    ]
    figure, (box_axis, count_axis) = plt.subplots(
        1,
        2,
        figsize=(14, 6),
        constrained_layout=True,
        gridspec_kw={"width_ratios": [1.1, 1]},
    )
    box_axis.boxplot(
        values,
        tick_labels=["117\nLIC-117016", "217\nLIC-217016"],
        showfliers=False,
        patch_artist=True,
        boxprops={"facecolor": "#dbeafe", "alpha": 0.8},
        medianprops={"color": "#111827", "linewidth": 1.5},
    )
    box_axis.axhspan(low, high, color="#16a34a", alpha=0.045)
    box_axis.axhline(low, color="#dc2626", linestyle="--", linewidth=1.1)
    box_axis.axhline(high, color="#dc2626", linestyle="--", linewidth=1.1)
    box_axis.set_title("60-min same-rule value distribution", loc="left")
    box_axis.set_ylabel("PV")
    box_axis.grid(axis="y", linewidth=0.5, alpha=0.28)
    box_axis.text(
        0.02,
        0.02,
        f"limits = {low:g} / {high:g}",
        transform=box_axis.transAxes,
        fontsize=9,
    )

    labels = ["117", "217"]
    low_counts = [
        sum(event.event_type == "low_limit" for event in events_by_point[point_id])
        for point_id in (COMPARISON_ID, REFERENCE_ID)
    ]
    high_counts = [
        sum(event.event_type == "high_limit" for event in events_by_point[point_id])
        for point_id in (COMPARISON_ID, REFERENCE_ID)
    ]
    positions = list(range(len(labels)))
    width = 0.34
    low_bars = count_axis.bar(
        [position - width / 2 for position in positions],
        low_counts,
        width,
        color="#ef4444",
        alpha=0.78,
        label="low-limit events",
    )
    high_bars = count_axis.bar(
        [position + width / 2 for position in positions],
        high_counts,
        width,
        color="#f97316",
        alpha=0.82,
        label="high-limit events",
    )
    for bars in (low_bars, high_bars):
        count_axis.bar_label(bars, padding=3)
    count_axis.set_xticks(positions, labels)
    count_axis.set_ylabel("event count")
    count_axis.set_title("Events from identical rule parameters", loc="left")
    count_axis.grid(axis="y", linewidth=0.5, alpha=0.28)
    count_axis.legend(frameon=False)
    figure.suptitle(
        "Same-rule distribution and event-count comparison\n"
        f"{start_time:%Y-%m-%d} to {end_time:%Y-%m-%d} | duration > 5 min",
        x=0.02,
        ha="left",
        fontsize=14,
        fontweight="bold",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.days <= 0:
        raise SystemExit("--days must be positive")
    end_time = (
        parse_timestamp(args.end_time)
        if args.end_time
        else datetime.now().replace(second=0, microsecond=0)
    )
    start_time = end_time - timedelta(days=args.days)

    parsed, points = load_same_rule_config()
    reference_point = points[REFERENCE_ID]
    max_smoothing_seconds = max(
        point.smoothing.window_seconds for point in points.values() if point.enabled
    )
    max_tail_seconds = max(
        side.min_duration_seconds + side.merge_gap_seconds
        for point in points.values()
        if point.enabled
        for side in (point.low, point.high)
        if side.enabled
    )
    query_start = start_time - timedelta(seconds=max_smoothing_seconds)
    query_end = end_time + timedelta(seconds=max_tail_seconds + 1)

    client = DcsServiceClient(
        args.base_url,
        timeout_seconds=70,
        total_timeout_seconds=300,
        max_retries=4,
    )
    info = client.get_info()
    for tag in TAGS:
        tag_info = client.check_tag(tag)
        if tag_info.status != "HistoryTagOK":
            raise SystemExit(f"TAG unavailable: {tag} ({tag_info.status})")
    histories = client.get_histories(list(TAGS), query_start, query_end)

    trend_index = pd.date_range(
        start=start_time,
        end=end_time - timedelta(minutes=1),
        freq="1min",
    )
    trend = pd.DataFrame(index=trend_index)
    events_by_point: dict[str, list[LimitOccurrence]] = {}
    summaries: dict[str, dict[str, object]] = {}
    all_event_rows: list[dict[str, object]] = []
    for point_id, point in points.items():
        samples = histories[point.history_tag]
        actual = minute_mean(samples)
        smoothed, smoothed_samples = same_rule_smooth_series(samples, point)
        occurrences = detect_events(
            smoothed_samples,
            point,
            start_time,
            end_time,
            query_end,
        )
        events_by_point[point_id] = occurrences
        trend[f"{point_id}_actual_1min"] = actual.reindex(trend_index)
        trend[f"{point_id}_same_rule_60min"] = smoothed.reindex(trend_index)
        summaries[point_id] = point_summary(
            point,
            samples,
            actual,
            smoothed,
            occurrences,
            start_time,
            end_time,
        )
        all_event_rows.extend(event_rows(point, occurrences, end_time))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    trend_path = args.output_dir / "LIC-117016_217016_same_rule_trends.csv"
    event_path = args.output_dir / "LIC-117016_217016_same_rule_events.csv"
    summary_path = args.output_dir / "LIC-117016_217016_same_rule_summary.json"
    trend_plot_path = args.output_dir / "LIC-117016_217016_same_rule_trends.png"
    comparison_plot_path = (
        args.output_dir / "LIC-117016_217016_same_rule_distribution.png"
    )

    trend.to_csv(trend_path, index_label="timestamp", encoding="utf-8-sig")
    write_event_csv(all_event_rows, event_path)
    plot_trends(
        trend,
        events_by_point,
        points,
        start_time,
        end_time,
        trend_plot_path,
    )
    plot_distribution_and_counts(
        trend,
        events_by_point,
        points,
        start_time,
        end_time,
        comparison_plot_path,
    )

    smoothing = reference_point.smoothing
    summary = {
        "source_timezone": info.source_timezone,
        "start": start_time.isoformat(),
        "end": end_time.isoformat(),
        "query_start_with_warmup": query_start.isoformat(),
        "query_end_with_confirmation_tail": query_end.isoformat(),
        "reference_config_point": REFERENCE_ID,
        "same_rule_parameters": {
            "smoothing": {
                "enabled": smoothing.enabled,
                "method": smoothing.method,
                "window_seconds": smoothing.window_seconds,
                "min_samples": smoothing.min_samples,
            },
            "low": {
                "enabled": reference_point.low.enabled,
                "limit": reference_point.low.limit,
                "min_duration_seconds": reference_point.low.min_duration_seconds,
                "merge_gap_seconds": reference_point.low.merge_gap_seconds,
            },
            "high": {
                "enabled": reference_point.high.enabled,
                "limit": reference_point.high.limit,
                "min_duration_seconds": reference_point.high.min_duration_seconds,
                "merge_gap_seconds": reference_point.high.merge_gap_seconds,
            },
        },
        "points": summaries,
        "outputs": {
            "trend_csv": trend_path.name,
            "event_csv": event_path.name,
            "trend_plot": trend_plot_path.name,
            "distribution_plot": comparison_plot_path.name,
        },
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"window={start_time.isoformat()} to {end_time.isoformat()} "
        f"source_timezone={info.source_timezone}"
    )
    print(
        "same_rule="
        f"smoothing:{smoothing.method}/{smoothing.window_seconds:g}s/"
        f"min_samples:{smoothing.min_samples} "
        f"low:{reference_point.low.limit:g} high:{reference_point.high.limit:g} "
        f"duration>{reference_point.low.min_duration_seconds:g}s"
    )
    for point_id in (COMPARISON_ID, REFERENCE_ID):
        result = summaries[point_id]
        counts = result["events_by_type"]
        print(
            f"{point_id} raw={result['query_sample_count']} "
            f"coverage={result['visible_minute_coverage_percent']}% "
            f"events={result['event_count']} "
            f"low={counts['low_limit']} high={counts['high_limit']}"
        )
    print(trend_path)
    print(event_path)
    print(trend_plot_path)
    print(comparison_plot_path)
    print(summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

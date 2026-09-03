"""Fetch LIC-217016 PV and plot trailing rolling means."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
import sys

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dcs_performance.data import DEFAULT_DCS_SERVICE_BASE_URL, DcsServiceClient
from dcs_performance.data.parsers import parse_timestamp


TAG = "LIC-217016/PID1/PV.CV"
WINDOWS_MINUTES = (15, 30, 60, 120)
OUTPUT_DIR = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch LIC-217016 PV and plot rolling-mean trends"
    )
    parser.add_argument("--base-url", default=DEFAULT_DCS_SERVICE_BASE_URL)
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument(
        "--to",
        dest="end_time",
        help="source-local exclusive end time; defaults to the current local minute",
    )
    return parser


def valid_sample(sample) -> bool:
    return not (
        sample.is_history_hole
        or sample.is_cr_hole
        or sample.is_manually_deleted
        or sample.is_manually_inserted
    )


def build_trends(samples, start_time: datetime, end_time: datetime) -> pd.DataFrame:
    rows = [
        (sample.timestamp, float(sample.value))
        for sample in samples
        if valid_sample(sample)
    ]
    if not rows:
        raise SystemExit(f"No valid history samples returned for {TAG}")

    raw = pd.DataFrame(rows, columns=["timestamp", "actual_value"])
    actual = (
        raw.groupby("timestamp", sort=True)["actual_value"]
        .mean()
        .resample("1min")
        .mean()
    )
    trend = actual.to_frame()
    for minutes in WINDOWS_MINUTES:
        trend[f"rolling_mean_{minutes}min"] = actual.rolling(
            f"{minutes}min",
            min_periods=max(1, minutes // 2),
        ).mean()
    return trend.loc[(trend.index >= start_time) & (trend.index < end_time)]


def plot_trends(
    trend: pd.DataFrame,
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
    figure, axis = plt.subplots(figsize=(18, 8), constrained_layout=True)
    axis.plot(
        trend.index,
        trend["actual_value"],
        color="#a8afb9",
        linewidth=0.65,
        alpha=0.72,
        label="Actual PV (1-min mean)",
    )
    colors = {
        15: "#2563eb",
        30: "#059669",
        60: "#d97706",
        120: "#7c3aed",
    }
    widths = {15: 1.0, 30: 1.25, 60: 1.55, 120: 1.9}
    for minutes in WINDOWS_MINUTES:
        axis.plot(
            trend.index,
            trend[f"rolling_mean_{minutes}min"],
            color=colors[minutes],
            linewidth=widths[minutes],
            label=f"{minutes}-min trailing mean",
        )

    valid_actual = trend["actual_value"].dropna()
    if valid_actual.empty:
        subtitle = "No valid values in selected range"
    else:
        subtitle = (
            f"Latest {valid_actual.iloc[-1]:.3f} at "
            f"{valid_actual.index[-1]:%Y-%m-%d %H:%M}"
        )
    axis.set_title(
        "LIC-217016 actual value: trend and rolling windows\n"
        f"{start_time:%Y-%m-%d %H:%M} to {end_time:%Y-%m-%d %H:%M} | {subtitle}",
        loc="left",
        fontweight="bold",
    )
    axis.set_xlabel("DCS source-local time (China Standard Time)")
    axis.set_ylabel("LIC-217016 PV")
    axis.set_xlim(start_time, end_time)
    axis.grid(axis="both", linewidth=0.5, alpha=0.28)
    axis.legend(loc="best", ncol=3, frameon=False)
    axis.xaxis.set_major_locator(mdates.HourLocator(interval=6))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.hours <= 0:
        raise SystemExit("--hours must be positive")

    client = DcsServiceClient(
        args.base_url,
        timeout_seconds=70,
        total_timeout_seconds=300,
        max_retries=4,
    )
    info = client.get_info()
    tag_info = client.check_tag(TAG)
    if tag_info.status != "HistoryTagOK":
        raise SystemExit(f"TAG unavailable: {TAG} ({tag_info.status})")

    end_time = (
        parse_timestamp(args.end_time)
        if args.end_time
        else datetime.now().replace(second=0, microsecond=0)
    )
    start_time = end_time - timedelta(hours=args.hours)
    # Fetch the longest window before the visible range so every curve is warm.
    query_start = start_time - timedelta(minutes=max(WINDOWS_MINUTES))
    print(
        f"fetching {query_start.isoformat()} <= t < {end_time.isoformat()} "
        f"tag={TAG}"
    )
    samples = client.get_history(TAG, query_start, end_time)
    trend = build_trends(samples, start_time, end_time)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "LIC-217016_3day_rolling_trends.csv"
    plot_path = OUTPUT_DIR / "LIC-217016_3day_rolling_trends.png"
    manifest_path = OUTPUT_DIR / "run_manifest.json"
    trend.to_csv(csv_path, index_label="timestamp", encoding="utf-8-sig")
    plot_trends(trend, start_time, end_time, plot_path)

    valid = trend["actual_value"].dropna()
    manifest = {
        "tag": TAG,
        "source_timezone": info.source_timezone,
        "start": start_time.isoformat(),
        "end": end_time.isoformat(),
        "query_start_with_warmup": query_start.isoformat(),
        "raw_sample_count": len(samples),
        "valid_minute_count": int(valid.size),
        "first_valid_timestamp": valid.index.min().isoformat() if not valid.empty else None,
        "last_valid_timestamp": valid.index.max().isoformat() if not valid.empty else None,
        "rolling_windows_minutes": list(WINDOWS_MINUTES),
        "csv": csv_path.name,
        "plot": plot_path.name,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"source_timezone={info.source_timezone}")
    print(f"raw_samples={len(samples)} valid_minutes={len(valid)}")
    print(csv_path)
    print(plot_path)
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

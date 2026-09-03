"""Run the production LIC-217016 limit rule for recent review windows."""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dcs_performance.data import DEFAULT_DCS_SERVICE_BASE_URL, DcsServiceClient
from dcs_performance.data.parsers import parse_timestamp
from dcs_performance.rules.analog_limit_exceedance.rule import Rule


POINT_ID = "LIC-217016"
OUTPUT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "src/dcs_performance/rules/analog_limit_exceedance/config.json"
)
EVENT_FIELDS = [
    "point_id",
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the configured LIC-217016 rule for recent windows"
    )
    parser.add_argument("--base-url", default=DEFAULT_DCS_SERVICE_BASE_URL)
    parser.add_argument("--hours", type=int, nargs="+", default=[72, 48])
    parser.add_argument(
        "--to",
        dest="end_time",
        help="source-local exclusive end time; defaults to the current local minute",
    )
    return parser


def load_point_config() -> dict:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    matches = [
        point
        for point in raw["parameters"]["points"]
        if point.get("id") == POINT_ID
    ]
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one enabled {POINT_ID} configuration")
    if not matches[0].get("enabled", True):
        raise SystemExit(f"Configured point {POINT_ID} is disabled")
    raw["parameters"]["points"] = matches
    by_point = raw.get("scoring", {}).get("by_point_event_type", {})
    if POINT_ID in by_point:
        raw["scoring"]["by_point_event_type"] = {POINT_ID: by_point[POINT_ID]}
    return raw


def event_row(event) -> dict[str, object]:
    return {
        "point_id": event.data["point_id"],
        "event_type": event.data["event_type"],
        "start_time": event.start_time.isoformat(),
        "end_time": event.end_time.isoformat(),
        "duration_seconds": event.data["duration_seconds"],
        "limit": event.data["limit"],
        "extreme_value": event.data["extreme_value"],
        "extreme_time": event.data["extreme_time"].isoformat(),
        "is_open": event.data["is_open"],
        "message": event.message,
    }


def json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def write_run(hours: int, start_time: datetime, end_time: datetime, events) -> None:
    stem = f"LIC-217016_rule_run_{hours}h"
    csv_path = OUTPUT_DIR / f"{stem}_events.csv"
    json_path = OUTPUT_DIR / f"{stem}_summary.json"
    rows = [event_row(event) for event in events]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVENT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    counts = {
        event_type: sum(row["event_type"] == event_type for row in rows)
        for event_type in ("low_limit", "high_limit")
    }
    payload = {
        "point_id": POINT_ID,
        "start": start_time.isoformat(),
        "end": end_time.isoformat(),
        "hours": hours,
        "event_count": len(events),
        "events_by_type": counts,
        "events": [
            {
                "start_time": event.start_time,
                "end_time": event.end_time,
                "message": event.message,
                "data": event.data,
            }
            for event in events
        ],
    }
    json_path.write_text(
        json.dumps(json_value(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"window={hours}h start={start_time.isoformat()} end={end_time.isoformat()} "
        f"events={len(events)} low={counts['low_limit']} high={counts['high_limit']}"
    )
    print(csv_path)
    print(json_path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.hours or any(hours <= 0 for hours in args.hours):
        raise SystemExit("--hours values must be positive")
    end_time = (
        parse_timestamp(args.end_time)
        if args.end_time
        else datetime.now().replace(second=0, microsecond=0)
    )
    client = DcsServiceClient(
        args.base_url,
        timeout_seconds=70,
        total_timeout_seconds=300,
        max_retries=4,
    )
    info = client.get_info()
    point_config = load_point_config()
    rule = Rule(data_client=client, config=point_config)
    print(f"source_timezone={info.source_timezone} point={POINT_ID}")
    for hours in dict.fromkeys(args.hours):
        start_time = end_time - timedelta(hours=hours)
        events = rule.evaluate(start_time, end_time)
        write_run(hours, start_time, end_time, events)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

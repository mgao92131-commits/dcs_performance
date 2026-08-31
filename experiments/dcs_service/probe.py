"""Manual probe for a real dcs-service V1 endpoint."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dcs_performance.data.dcs_service import DcsServiceClient
from dcs_performance.data.errors import DcsServiceError
from dcs_performance.data.parsers import parse_timestamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe a dcs-service V1 endpoint")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--minutes", type=int, default=5)
    parser.add_argument("--from", dest="from_time")
    parser.add_argument("--to", dest="to_time")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        client = DcsServiceClient(args.base_url)

        print(f"Base URL: {args.base_url}")
        healthy = client.health()
        print(f"Health: {'ok' if healthy else 'not-ok'}")
        if not healthy:
            return 1

        info = client.get_info()
        print(f"Service Version: {info.version}")
        print(f"Source TimeZone: {info.source_timezone}")
        print(f"History concurrency: {info.history_max_concurrent}")
        print(f"Event concurrency: {info.event_max_concurrent}")

        start_time, end_time = _resolve_range(parser, args, info.source_timezone)

        tag_info = client.check_tag(args.tag)
        print(f"TAG status: {tag_info.status}")
        print(f"TAG data type: {tag_info.data_type or '<none>'}")

        history = client.get_history(args.tag, start_time, end_time)
        _print_range_summary("History", history)

        events = client.get_events(start_time, end_time)
        _print_range_summary("Events", events)
        return 0
    except DcsServiceError as exc:
        print(
            f"ERROR status={exc.status_code} code={exc.code} message={exc.message}",
            file=sys.stderr,
        )
        return 1


def _resolve_range(
    parser: argparse.ArgumentParser,
    args,
    source_timezone: str,
) -> tuple[datetime, datetime]:
    if (args.from_time is None) != (args.to_time is None):
        parser.error("--from and --to must be supplied together")
    if args.from_time is not None:
        start_time = parse_timestamp(args.from_time)
        end_time = parse_timestamp(args.to_time)
    else:
        if args.minutes <= 0:
            parser.error("--minutes must be greater than zero")
        if not _system_clock_matches_source(source_timezone):
            parser.error(
                "--minutes uses the client machine's naive local clock; "
                "use --from/--to unless it matches the DCS source timezone"
            )
        end_time = datetime.now().replace(microsecond=0)
        start_time = end_time - timedelta(minutes=args.minutes)
    if end_time <= start_time:
        parser.error("--to must be after --from")
    return start_time, end_time


def _system_clock_matches_source(source_timezone: str) -> bool:
    """Allow ``--minutes`` only when no timezone conversion is needed."""

    expected_offsets = {
        "China Standard Time": timedelta(hours=8),
        "UTC": timedelta(0),
    }
    expected_offset = expected_offsets.get(source_timezone)
    if expected_offset is None:
        return False
    return datetime.now().astimezone().utcoffset() == expected_offset


def _print_range_summary(label: str, values) -> None:
    print(f"{label}: rows={len(values)}")
    if not values:
        print(f"{label}: first=<none> last=<none>")
        return
    timestamps = [value.timestamp for value in values]
    print(f"{label}: first={min(timestamps).isoformat()} last={max(timestamps).isoformat()}")


if __name__ == "__main__":
    raise SystemExit(main())

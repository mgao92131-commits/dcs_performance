"""Command-line entry points for rule inspection and production delivery."""

import argparse
from datetime import datetime
from pathlib import Path
import sys
from typing import Sequence

from .data.dcs_service import DcsServiceClient
from .delivery.manager import DeliveryManager
from .engine.loader import RuleLoader
from .notification import NotificationConfigError, NotificationSendError, send_package_email
from .shifts import Cyclic12HourShiftCalendar, load_performance_schedule_config


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dcs-performance")
    parser.add_argument(
        "--rules-dir",
        type=Path,
        default=None,
        help="override the directory containing rule subdirectories",
    )
    parser.add_argument(
        "--list-rules",
        action="store_true",
        help="list rules discovered from their local config.json files",
    )
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="produce one complete Result Package")
    run_parser.add_argument("--at", required=True, type=_local_datetime)
    run_parser.add_argument("--output", required=True, type=Path)
    run_parser.add_argument("--rules-dir", type=Path, default=argparse.SUPPRESS)
    run_parser.add_argument("--service-url", default=None)
    run_parser.add_argument("--overwrite", action="store_true")
    email_parser = subparsers.add_parser(
        "send-email",
        help="send a notification from one published Result Package",
    )
    email_parser.add_argument(
        "--package",
        "--result-package",
        "--result-json",
        "--result",
        required=True,
        type=Path,
        help="Result Package directory or its result.json",
    )
    email_parser.add_argument("--config", type=Path, default=None)
    email_parser.add_argument("--state", type=Path, default=None)
    email_parser.add_argument("--dry-run", action="store_true")
    email_parser.add_argument(
        "--preview",
        action="store_true",
        help="render and print both email alternatives without sending",
    )
    email_parser.add_argument(
        "--resend",
        action="store_true",
        help="send even when this run_id was already sent",
    )
    args = parser.parse_args(argv)

    if args.list_rules:
        for metadata in RuleLoader(rules_dir=args.rules_dir).list_metadata():
            state = "enabled" if metadata.enabled else "disabled"
            print(f"{metadata.id}\t{metadata.name}\t{state}")
        return 0

    if args.command == "run":
        try:
            schedule_path = Path(__file__).resolve().parent / "shifts" / "performance_schedule.json"
            calendar = Cyclic12HourShiftCalendar(
                load_performance_schedule_config(schedule_path)
            )
            shift = calendar.shift_for_timestamp(args.at)
            client = (
                DcsServiceClient(args.service_url)
                if args.service_url is not None
                else DcsServiceClient()
            )
            delivery = DeliveryManager(
                rules_dir=args.rules_dir,
                data_client=client,
            ).deliver(shift, args.output, overwrite=args.overwrite)
        except Exception as exc:
            print(f"Assessment failed: {exc}", file=sys.stderr)
            return 1
        print(
            "Assessment completed\n\n"
            f"Run:\n  {delivery.run_id}\n\n"
            "Shift:\n"
            f"  team={shift.team_id}\n"
            f"  start={shift.start_time.isoformat()}\n"
            f"  end={shift.end_time.isoformat()}\n\n"
            f"Rules: {delivery.rule_count}\n"
            f"Points: {delivery.point_count}\n"
            f"Events: {delivery.event_count}\n"
            f"Score: {delivery.total_score:g}\n\n"
            f"Result:\n  {delivery.result_json_path}\n\n"
            f"Images:\n  {delivery.images_path}"
        )
        return 0

    if args.command == "send-email":
        try:
            result = send_package_email(
                args.package,
                config_path=args.config,
                state_path=args.state,
                dry_run=args.dry_run,
                preview=args.preview,
                resend=args.resend,
            )
        except (NotificationConfigError, NotificationSendError, OSError, ValueError) as exc:
            print(f"Email notification failed: {exc}", file=sys.stderr)
            return 1
        print(
            f"Email notification {result.status}: {result.run_id}\n"
            f"Recipients: {', '.join(result.recipients)}\n"
            f"Subject: {result.subject}\n"
            f"State: {result.state_path}"
        )
        if args.preview and result.rendered is not None:
            print("\n--- plain text preview ---\n")
            print(result.rendered.text)
            print("\n--- HTML preview ---\n")
            print(result.rendered.html)
        return 0

    parser.print_help()
    return 0


def _local_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--at must be an ISO local datetime") from exc
    if parsed.tzinfo is not None:
        raise argparse.ArgumentTypeError("--at must not include a timezone offset")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())

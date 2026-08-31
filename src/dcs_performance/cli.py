"""Small command-line entry point for inspecting the phase-one skeleton."""

import argparse
from pathlib import Path
from typing import Sequence

from .engine.loader import RuleLoader


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
    args = parser.parse_args(argv)

    if args.list_rules:
        for loaded in RuleLoader(rules_dir=args.rules_dir).load_all():
            state = "enabled" if loaded.enabled else "disabled"
            print(f"{loaded.id}\t{loaded.name}\t{state}")
        return 0

    parser.print_help()
    return 0

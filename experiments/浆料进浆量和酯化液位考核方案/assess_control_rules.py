"""Run the two approved slurry/esterification assessment indicators.

The script deliberately uses the LOGIC27 value as returned by DCS.  DCS has
already applied the correction, so this script never divides LOGIC27 by
1.0099.  It delegates all calculations to the production rule packages:

* esterification level absolute limits: 71 to 73, with a 60-second mean;
* esterification level rate: +/-0.14 level/hour for two continuous hours;
* slurry flow balance: 60-second mean of LOGIC27 versus SY116 + SY216,
  with a +/-15 difference for five continuous minutes.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT.parents[1] / "src"))

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.data.models import HistorySample
from dcs_performance.data.parsers import parse_history_csv, parse_timestamp
from dcs_performance.engine.loader import RuleLoader


HISTORY_FILES = {
    "LOGIC27/YK-TLFH/OUT1.CV": "LOGIC27_YK-TLFH_OUT1.CV.csv",
    "SY-116/AI1/PV.CV": "SY-116_AI1_PV.CV.csv",
    "SY-216/AI1/PV.CV": "SY-216_AI1_PV.CV.csv",
    "LICA-012019/PID1/PV.CV": "LICA-012019_PID1_PV.CV.csv",
}


class CsvHistoryClient:
    """Small read-only DCS client backed by the fetched History CSV files."""

    def __init__(self, data_dir: Path) -> None:
        self.histories: dict[str, list[HistorySample]] = {}
        for tag, filename in HISTORY_FILES.items():
            path = data_dir / filename
            if not path.is_file():
                raise FileNotFoundError(path)
            body = path.read_bytes()
            if body.startswith(b"\xef\xbb\xbf"):
                body = body[3:]
            # Older locally exported files use Python-style True/False while
            # the V1 parser intentionally accepts only lowercase booleans.
            text = body.decode("utf-8")
            text = re.sub(
                r"(?<=,)(True|False)(?=,|\r?$)",
                lambda match: match.group(1).lower(),
                text,
                flags=re.MULTILINE,
            )
            body = text.encode("utf-8")
            self.histories[tag] = parse_history_csv(body)

    def get_history(
        self,
        tag: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[HistorySample]:
        return [
            sample
            for sample in self.histories.get(tag, [])
            if start_time <= sample.timestamp < end_time
        ]

    def get_histories(
        self,
        tags: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, list[HistorySample]]:
        return {
            tag: self.get_history(tag, start_time, end_time)
            for tag in dict.fromkeys(tags)
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assess the two slurry/esterification control indicators"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ROOT / "validation_7d",
        help="directory containing the four History CSV files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="directory for the new rule-based assessment outputs; defaults to data-dir/assessment_outputs_rule_v2",
    )
    return parser


def read_manifest(data_dir: Path) -> tuple[datetime, datetime, dict[str, Any]]:
    manifest = json.loads(
        (data_dir / "download_manifest.json").read_text(encoding="utf-8")
    )
    start = parse_timestamp(str(manifest["start"]))
    end = parse_timestamp(str(manifest["end"]))
    if end <= start:
        raise ValueError("manifest end must be after start")
    return start, end, manifest


def write_events(events: list[AssessmentEvent], path: Path) -> None:
    fields = [
        "indicator",
        "rule_id",
        "event_type",
        "direction",
        "start_time",
        "end_time",
        "message",
        "event_data",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for event in events:
            rule_id = _rule_id(event)
            writer.writerow(
                {
                    "indicator": _indicator(rule_id),
                    "rule_id": rule_id,
                    "event_type": event.data.get("event_type", ""),
                    "direction": event.data.get("direction", ""),
                    "start_time": event.start_time.isoformat(sep=" "),
                    "end_time": event.end_time.isoformat(sep=" "),
                    "message": event.message,
                    "event_data": json.dumps(
                        event.data,
                        ensure_ascii=False,
                        default=str,
                    ),
                }
            )


def write_summary(events: list[AssessmentEvent], path: Path) -> None:
    definitions = [
        ("酯化液位绝对上下限", "analog_limit_exceedance"),
        ("酯化液位变化速率", "level_rate_compliance"),
        ("浆料进料量平衡", "flow_balance_compliance"),
    ]
    fields = [
        "indicator",
        "rule_id",
        "event_count",
        "low_or_down_count",
        "high_or_up_count",
        "status",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for indicator, rule_id in definitions:
            selected = [event for event in events if _rule_id(event) == rule_id]
            low_count = sum(
                1
                for event in selected
                if event.data.get("direction") in {"low", "rate_down", "flow_low"}
                or event.data.get("event_type") == "low_limit"
            )
            writer.writerow(
                {
                    "indicator": indicator,
                    "rule_id": rule_id,
                    "event_count": len(selected),
                    "low_or_down_count": low_count,
                    "high_or_up_count": len(selected) - low_count,
                    "status": "不合格" if selected else "合格",
                }
            )


def write_parameters(path: Path, start: datetime, end: datetime) -> None:
    parameters = {
        "data_start": start.isoformat(),
        "data_end": end.isoformat(),
        "logic27_source": "DCS corrected value",
        "logic27_extra_correction": None,
        "level_absolute_limits": {
            "lower": 71.0,
            "upper": 73.0,
            "smoothing_seconds": 60,
            "persistence_seconds": 300,
        },
        "level_rate": {
            "lower_rate_per_hour": -0.14,
            "upper_rate_per_hour": 0.14,
            "rate_window_seconds": 7200,
            "persistence_seconds": 7200,
            "smoothing_seconds": 60,
        },
        "flow_balance": {
            "logic_tag": "LOGIC27/YK-TLFH/OUT1.CV",
            "sy_tags": ["SY-116/AI1/PV.CV", "SY-216/AI1/PV.CV"],
            "difference_lower": -15.0,
            "difference_upper": 15.0,
            "smoothing_seconds": 60,
            "persistence_seconds": 300,
        },
    }
    path.write_text(
        json.dumps(parameters, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_report(
    path: Path,
    start: datetime,
    end: datetime,
    events: list[AssessmentEvent],
) -> None:
    counts: dict[str, int] = {}
    for event in events:
        rule_id = _rule_id(event)
        counts[rule_id] = counts.get(rule_id, 0) + 1
    report = f"""# 浆料进料量和酯化液位考核方案（新规则版）

数据窗口：`{start}` 至 `{end}`。

本次执行只包含两个主指标，不包含调节响应考核。

## 规则

1. 酯化液位先使用60秒尾随滑动平均，再判断71～73绝对上下限；速率规则同样使用该60秒平滑值，2小时速率超过±0.14且连续2小时才形成事件。
2. 浆料流量直接使用DCS已经修正的LOGIC27，与SY116+SY216的60秒滑动平均比较；偏差超过±15且连续5分钟才形成事件。

本次没有对LOGIC27再除以1.0099。

## 事件数量

| 指标 | 事件数 |
|---|---:|
| 酯化液位绝对上下限 | {counts.get('analog_limit_exceedance', 0)} |
| 酯化液位变化速率 | {counts.get('level_rate_compliance', 0)} |
| 浆料进料量平衡 | {counts.get('flow_balance_compliance', 0)} |
"""
    path.write_text(report, encoding="utf-8")


def _rule_id(event: AssessmentEvent) -> str:
    return str(event.data.get("rule_id", ""))


def _indicator(rule_id: str) -> str:
    return {
        "analog_limit_exceedance": "酯化液位绝对上下限",
        "level_rate_compliance": "酯化液位变化速率",
        "flow_balance_compliance": "浆料进料量平衡",
    }.get(rule_id, rule_id)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    start, end, _ = read_manifest(args.data_dir)
    output_dir = args.output_dir or args.data_dir / "assessment_outputs_rule_v2"
    client = CsvHistoryClient(args.data_dir)
    loader = RuleLoader(data_client=client)
    rules = [
        loader.load("analog_limit_exceedance").rule,
        loader.load("level_rate_compliance").rule,
        loader.load("flow_balance_compliance").rule,
    ]

    events: list[AssessmentEvent] = []
    for rule in rules:
        for event in rule.evaluate(start, end):
            event.data["rule_id"] = rule.id
            events.append(event)
    events.sort(
        key=lambda event: (
            event.start_time,
            str(event.data.get("rule_id", "")),
        )
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_events(events, output_dir / "assessment_events.csv")
    write_summary(events, output_dir / "assessment_summary.csv")
    write_parameters(output_dir / "assessment_parameters.json", start, end)
    write_report(output_dir / "assessment_report.md", start, end, events)

    print(f"data=[{start}, {end})")
    print(f"events={len(events)}")
    print(f"output_dir={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

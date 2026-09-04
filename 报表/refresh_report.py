"""旧入口兼容层：规则刷新统一转交 report.py，禁止重建整张工作簿。"""

from __future__ import annotations

import argparse

from report import main as report_main


def main() -> int:
    parser = argparse.ArgumentParser(description="兼容入口；请改用 report.py sync-rules")
    parser.add_argument("--excel", required=True)
    args = parser.parse_args()
    print("提示：refresh_report.py 已停用重建逻辑，现转交 report.py sync-rules。")
    return report_main(["--excel", args.excel, "sync-rules"])


if __name__ == "__main__":
    raise SystemExit(main())

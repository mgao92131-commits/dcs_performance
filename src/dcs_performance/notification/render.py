"""HTML and plain-text renderers for Result Package notifications."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Iterable

from .config import NotificationConfig
from .package import DeductionPoint, ResultPackage


@dataclass(frozen=True)
class InlineImage:
    """PNG bytes and the CID used by one HTML ``img`` element."""

    cid: str
    filename: str
    data: bytes


@dataclass(frozen=True)
class RenderedNotification:
    """Fully rendered alternatives plus inline image parts."""

    subject: str
    html: str
    text: str
    images: tuple[InlineImage, ...]


def render_notification(
    package: ResultPackage,
    config: NotificationConfig | None = None,
) -> RenderedNotification:
    """Render one Result Package, showing positive-score points only."""

    subject_prefix = config.subject_prefix if config else "DCS班次考核通知"
    subject = (
        f"{subject_prefix} - {package.team_label}班 - "
        f"{_short_time(package.shift_start)}"
    )
    images: list[InlineImage] = []
    sections: list[str] = []
    summary_rows: list[str] = []
    text_summary: list[str] = []

    for index, point in enumerate(package.deductions, 1):
        cid = _image_cid(package.run_id, point.rule_id, point.point_id)
        image = InlineImage(cid=cid, filename=point.image_path.name, data=point.image_path.read_bytes())
        images.append(image)
        summary_rows.append(
            "<tr>"
            f"<td>{index}</td>"
            f"<td>{escape(point.rule_name)}</td>"
            f"<td>{escape(point.point_id)}</td>"
            f"<td class=\"number\">{_format_score(point.score)}</td>"
            f"<td class=\"number\">{point.event_count}</td>"
            "</tr>"
        )
        text_summary.append(
            f"{index}. {point.rule_name} / {point.point_id}: "
            f"扣分 {_format_score(point.score)}，事件 {point.event_count}"
        )
        sections.append(_render_point_html(index, point, cid))

    count = package.deduction_count
    total = _format_score(package.total_score)
    if count:
        deduction_notice = ""
        summary_table = (
            "<table class=\"summary\"><thead><tr>"
            "<th>#</th><th>规则</th><th>考核点</th><th>扣分</th><th>事件数</th>"
            "</tr></thead><tbody>"
            + "".join(summary_rows)
            + "</tbody></table>"
        )
        text_summary_block = "\n".join(text_summary)
    else:
        deduction_notice = '<p class="notice">本班次无扣分。</p>'
        summary_table = (
            "<table class=\"summary\"><thead><tr>"
            "<th>扣分项目</th><th>状态</th>"
            "</tr></thead><tbody><tr><td>0</td><td>无扣分</td></tr></tbody></table>"
        )
        text_summary_block = "无扣分"

    html = _html_document(
        subject=subject,
        package=package,
        count=count,
        total=total,
        deduction_notice=deduction_notice,
        summary_table=summary_table,
        sections="".join(sections),
    )
    text = _text_document(
        subject=subject,
        package=package,
        count=count,
        total=total,
        summary=text_summary_block,
        points=package.deductions,
    )
    return RenderedNotification(subject=subject, html=html, text=text, images=tuple(images))


def render_email(
    package: ResultPackage,
    config: NotificationConfig | None = None,
) -> RenderedNotification:
    """Compatibility alias for callers that prefer an email-oriented name."""

    return render_notification(package, config)


def render_html(
    package: ResultPackage,
    config: NotificationConfig | None = None,
) -> str:
    """Return only the HTML alternative for simple preview integrations."""

    return render_notification(package, config).html


def render_plain_text(
    package: ResultPackage,
    config: NotificationConfig | None = None,
) -> str:
    """Return only the plain-text alternative for simple preview integrations."""

    return render_notification(package, config).text


def _html_document(
    *,
    subject: str,
    package: ResultPackage,
    count: int,
    total: str,
    deduction_notice: str,
    summary_table: str,
    sections: str,
) -> str:
    shift_type = _shift_type_label(package.shift_type)
    status_label = "有扣分" if count else "无扣分"
    status_class = "status-warning" if count else "status-ok"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{escape(subject)}</title>
<style>
body {{ margin: 0; padding: 24px 12px; background: #f1f5f9; color: #243447;
        font-family: Arial, "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
        line-height: 1.55; }}
.shell {{ max-width: 820px; margin: 0 auto; background: #ffffff; border: 1px solid #dbe4ee;
          border-radius: 16px; overflow: hidden; box-shadow: 0 8px 28px rgba(30, 64, 96, .10); }}
.hero {{ padding: 28px 32px 26px; color: #ffffff;
         background: linear-gradient(135deg, #185a9d 0%, #1e7bb6 58%, #2a9d8f 100%); }}
.eyebrow {{ font-size: 11px; letter-spacing: 1.8px; text-transform: uppercase; opacity: .82; }}
.hero h1 {{ margin: 8px 0 5px; font-size: 25px; line-height: 1.25; font-weight: 700; }}
.hero p {{ margin: 0; font-size: 14px; opacity: .92; }}
.content {{ padding: 26px 32px 30px; }}
.status {{ display: inline-block; margin: 0 0 18px; padding: 5px 12px; border-radius: 999px;
           font-size: 13px; font-weight: 700; }}
.status-warning {{ color: #8a4b08; background: #fff3d6; border: 1px solid #f3d18b; }}
.status-ok {{ color: #17643a; background: #e5f7ec; border: 1px solid #a9dfbd; }}
.metrics {{ width: 100%; border-collapse: separate; border-spacing: 0; margin: 0 0 22px;
            border: 1px solid #e1eaf3; border-radius: 10px; overflow: hidden; }}
.metrics th, .metrics td {{ padding: 11px 15px; border-bottom: 1px solid #e7edf4;
                            text-align: left; vertical-align: middle; }}
.metrics tr:last-child th, .metrics tr:last-child td {{ border-bottom: 0; }}
.metrics th {{ width: 125px; color: #6b7c93; background: #f8fbfe; font-size: 12px; font-weight: 600; }}
.metric-value {{ color: #173b63; font-size: 16px; font-weight: 700; word-break: break-word; }}
.metric-value.total {{ color: #c05621; }}
.section-title {{ display: flex; align-items: center; gap: 9px; margin: 25px 0 10px; color: #173b63;
                  font-size: 18px; }}
.section-title:before {{ display: inline-block; width: 4px; height: 20px; border-radius: 4px;
                         background: #2a9d8f; content: ""; }}
.summary {{ width: 100%; border-collapse: separate; border-spacing: 0; overflow: hidden;
            border: 1px solid #dbe4ee; border-radius: 10px; margin: 0 0 22px; }}
.summary th, .summary td {{ padding: 10px 11px; border-bottom: 1px solid #e7edf4;
                             text-align: left; vertical-align: top; }}
.summary th {{ color: #48627d; background: #f2f7fb; font-size: 12px; font-weight: 700; }}
.summary tbody tr:last-child td {{ border-bottom: 0; }}
.summary tbody tr:nth-child(even) td {{ background: #fbfdff; }}
.number {{ text-align: right !important; font-variant-numeric: tabular-nums; }}
.notice {{ margin: 0 0 19px; padding: 12px 14px; border-radius: 9px; color: #17643a;
           background: #f0fbf4; border: 1px solid #c6ead2; font-weight: 600; }}
.evidence-intro {{ margin: -2px 0 12px; color: #718096; font-size: 13px; }}
.evidence-card {{ margin: 0 0 22px; padding: 0 0 16px; border: 1px solid #dbe4ee;
                  border-radius: 12px; overflow: hidden; background: #ffffff; }}
.point-title {{ padding: 13px 16px; color: #173b63; background: #f4f8fc; border-bottom: 1px solid #dbe4ee;
                font-size: 15px; font-weight: 700; }}
.point-title .point-number {{ display: inline-block; min-width: 28px; margin-right: 7px; color: #1e7bb6; }}
.point-title .point-score {{ float: right; color: #c05621; font-size: 13px; }}
img.evidence {{ display: block; max-width: calc(100% - 30px); height: auto; margin: 15px auto 0;
                border: 1px solid #d1dbe6; border-radius: 7px; background: #f8fafc; }}
.footer {{ margin-top: 25px; padding-top: 16px; border-top: 1px solid #e7edf4; color: #8a98a9;
           font-size: 12px; }}
@media only screen and (max-width: 620px) {{
  body {{ padding: 0; }}
  .shell {{ border: 0; border-radius: 0; }}
  .hero, .content {{ padding-left: 18px; padding-right: 18px; }}
  .metrics th, .metrics td {{ padding: 9px 10px; }}
  .metrics th {{ width: 96px; }}
  .metric-value {{ font-size: 14px; }}
  .summary th, .summary td {{ padding: 8px 6px; font-size: 12px; }}
}}
</style></head><body>
<div class="shell">
  <header class="hero">
    <div class="eyebrow">DCS PERFORMANCE · SHIFT REPORT</div>
    <h1>班次考核通知</h1>
    <p>{escape(package.team_label)}班 · {escape(shift_type)}</p>
  </header>
  <main class="content">
    <div class="status {status_class}">{status_label}</div>
    <table class="metrics"><tbody>
      <tr><th class="metric-label">班次</th><td class="metric-value">{escape(_time_range_text(package))}</td></tr>
      <tr><th class="metric-label">扣分项目</th><td class="metric-value">{count} 项</td></tr>
      <tr><th class="metric-label">合计扣分</th><td class="metric-value total">{escape(total)} 分</td></tr>
    </tbody></table>
    {deduction_notice}
    <h2 class="section-title">扣分汇总</h2>
    {summary_table}
    {('<h2 class="section-title">扣分点证据</h2><p class="evidence-intro">以下仅列出扣分考核点标题及对应截图。</p>' + sections) if count else ''}
    <div class="footer">本邮件根据已发布的 Result Package 自动生成，请以正式报表为准。</div>
  </main>
</div>
</body></html>"""


def _render_point_html(index: int, point: DeductionPoint, cid: str) -> str:
    """Render a deduction title and its inline evidence image.

    Event-level explanations intentionally stay out of the visual email.  The
    summary table already carries the aggregate event count; the evidence
    section is kept scannable by showing only each point title and its PNG.
    """

    return (
        '<figure class="evidence-card">'
        '<figcaption class="point-title">'
        f'<span class="point-number">{index:02d}</span>'
        f'{escape(point.rule_name)} / {escape(point.point_id)}'
        f'<span class="point-score">扣分 {_format_score(point.score)}</span>'
        '</figcaption>'
        f'<img class="evidence" src="cid:{escape(cid)}" '
        f'alt="{escape(point.rule_name)} / {escape(point.point_id)} 证据图">'
        '</figure>'
    )


def _text_document(
    *,
    subject: str,
    package: ResultPackage,
    count: int,
    total: str,
    summary: str,
    points: Iterable[DeductionPoint],
) -> str:
    point_items = tuple(points)
    lines = [
        subject,
        "=" * len(subject),
        f"班次: {_shift_text(package)}",
        f"班组: {package.team_label}班（{package.team_id}）",
        f"扣分项目数量: {count}",
        f"合计扣分: {total}",
        "",
        "扣分汇总:",
        summary,
    ]
    for index, point in enumerate(point_items, 1):
        lines.extend(
            [
                "",
                f"{index}. {point.rule_name} / {point.point_id}（扣分 {_format_score(point.score)}）",
            ]
        )
        lines.append(f"  - 证据图: {point.image_relative_path}")
    if not point_items:
        lines.extend(["", "本班次无扣分。"])
    return "\n".join(lines) + "\n"


def _shift_text(package: ResultPackage) -> str:
    return f"{_short_time(package.shift_start)} — {_short_time(package.shift_end)}（{_shift_type_label(package.shift_type)}）"


def _time_range_text(package: ResultPackage) -> str:
    """Return only the clock range used by the compact summary row."""

    return f"{_short_clock(package.shift_start)} ~ {_short_clock(package.shift_end)}"


def _short_clock(value: str) -> str:
    compact = _short_time(value)
    return compact.rsplit(" ", 1)[-1] if " " in compact else compact


def _shift_type_label(value: str) -> str:
    return {"day": "白班", "night": "夜班"}.get(value, value)


def _short_time(value: str) -> str:
    """Return a compact local timestamp without seconds.

    Result Packages use ISO-8601 strings.  We deliberately do not convert
    time zones here because the package contract is ``time_basis=local``.
    """

    text = str(value).strip()
    if not text:
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # Keep malformed-but-validated legacy values readable while removing
        # the common seconds component from ``YYYY-MM-DD[T ]HH:MM:SS``.
        compact = text.replace("T", " ", 1)
        if len(compact) >= 19 and compact[13] == ":" and compact[16] == ":":
            return compact[:16]
        return compact
    if "T" not in text and len(text) == 10:
        return parsed.strftime("%Y-%m-%d")
    return parsed.strftime("%Y-%m-%d %H:%M")


def _format_score(value: float | int) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _image_cid(run_id: str, rule_id: str, point_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{rule_id}\0{point_id}".encode("utf-8")).hexdigest()[:20]
    return f"dcs-{digest}@dcs-performance"


__all__ = [
    "InlineImage",
    "RenderedNotification",
    "render_email",
    "render_html",
    "render_notification",
    "render_plain_text",
]

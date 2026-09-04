from __future__ import annotations

import json
import smtplib
from pathlib import Path

import pytest

from dcs_performance.cli import main
from dcs_performance.notification import (
    NotificationConfigError,
    NotificationSendError,
    build_email_message,
    load_notification_config,
    parse_result_package,
    render_notification,
    send_package_email,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"minimal-test-png"


def _config_file(tmp_path: Path, *, security: str = "ssl") -> Path:
    path = tmp_path / "notification.config.json"
    path.write_text(
        json.dumps(
            {
                "smtp": {
                    "host": "smtp.test",
                    "port": 465 if security == "ssl" else 587,
                    "security": security,
                    "from": "sender@example.com",
                    "username": "sender@example.com",
                    "password_env": "TEST_SMTP_PASSWORD",
                },
                "teams": {
                    "A": ["a@example.com"],
                    "B": {"recipients": ["b@example.com"]},
                    "C": "c@example.com",
                },
                "state_path": "state.json",
                "subject_prefix": "测试考核",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _package(tmp_path: Path, *, score: float = 2.5, team_id: str = "A") -> Path:
    package = tmp_path / "20260903T080000_20260903T200000_A"
    images = package / "images"
    images.mkdir(parents=True)
    if score > 0:
        (images / "rule__POINT-1.png").write_bytes(PNG)
    point = {
        "point_id": "POINT-1",
        "status": "violation" if score > 0 else "normal",
        "data_status": "ok",
        "event_count": 1 if score > 0 else 0,
        "score": score,
        "events": (
            [
                {
                    "event_type": "high_limit",
                    "start": "2026-09-03T09:00:00",
                    "end": "2026-09-03T09:05:00",
                    "duration_seconds": 300,
                    "score": score,
                    "message": "液位超过上限",
                    "data": {"tag": "LIC-1"},
                }
            ]
            if score > 0
            else []
        ),
    }
    if score > 0:
        point["image"] = "images/rule__POINT-1.png"
    document = {
        "schema_version": "1.0",
        "time_basis": "local",
        "run": {
            "run_id": package.name,
            "generated_at": "2026-09-03T12:00:00+00:00",
        },
        "shift": {
            "team_id": team_id,
            "shift_type": "day",
            "start": "2026-09-03T08:00:00",
            "end": "2026-09-03T20:00:00",
        },
        "summary": {
            "rule_count": 1,
            "point_count": 1,
            "event_count": 1 if score > 0 else 0,
            "total_score": score,
        },
        "rules": [
            {
                "rule_id": "rule",
                "rule_name": "液位考核",
                "event_count": 1 if score > 0 else 0,
                "score": score,
                "points": [point],
            }
        ],
    }
    (package / "result.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return package


def test_config_maps_team_ids_and_rejects_password_in_file(tmp_path):
    config = load_notification_config(_config_file(tmp_path))
    assert config.mailbox_for("A").label == "甲"
    assert config.mailbox_for("B").recipients == ("b@example.com",)
    assert config.mailbox_for("C").recipients == ("c@example.com",)
    assert config.state_path == (tmp_path / "state.json").resolve()

    raw = json.loads((_config_file(tmp_path)).read_text(encoding="utf-8"))
    raw["smtp"]["password"] = "must-not-be-stored"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(NotificationConfigError, match="password"):
        load_notification_config(bad)
    with pytest.raises(NotificationConfigError, match="TEST_SMTP_PASSWORD"):
        config.password({})


def test_parser_and_renderer_show_only_positive_points_and_cid_image(tmp_path):
    package = parse_result_package(_package(tmp_path, score=2.5))
    rendered = render_notification(package, load_notification_config(_config_file(tmp_path)))
    assert package.team_label == "甲"
    assert package.deduction_count == 1
    assert 'class="metric-label">扣分项目</th><td class="metric-value">1 项' in rendered.html
    assert 'class="metric-label">班组</th>' not in rendered.html
    assert 'class="metric-label">班次</th><td class="metric-value">08:00 ~ 20:00' in rendered.html
    assert rendered.html.count('class="metric-label"') == 3
    assert rendered.html.count('<table class="metrics">') == 1
    # The visual evidence section intentionally contains only the point title;
    # event-level explanations are omitted to keep the mail scannable.
    assert "液位超过上限" not in rendered.html
    assert "液位超过上限" not in rendered.text
    assert "event-time" not in rendered.html
    assert "2026-09-03 08:00" in rendered.html
    assert "2026-09-03T08:00:00" not in rendered.html
    assert '<figcaption class="point-title">' in rendered.html
    assert rendered.html.count("cid:") == 1
    assert len(rendered.images) == 1
    assert rendered.images[0].cid in rendered.html
    assert "POINT-1" in rendered.text


def test_no_deduction_still_renders_notice(tmp_path):
    package = parse_result_package(_package(tmp_path, score=0))
    rendered = render_notification(package)
    assert package.deduction_count == 0
    assert "本班次无扣分" in rendered.html
    assert "无扣分" in rendered.text
    assert rendered.images == ()


def test_parser_rejects_image_traversal_and_non_png(tmp_path):
    package = _package(tmp_path, score=1)
    document = json.loads((package / "result.json").read_text(encoding="utf-8"))
    document["rules"][0]["points"][0]["image"] = "../outside.png"
    (package / "result.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="包内相对路径"):
        parse_result_package(package)

    package = _package(tmp_path / "second", score=1)
    image = package / "images" / "rule__POINT-1.png"
    image.write_bytes(b"not png")
    with pytest.raises(ValueError, match="有效 PNG"):
        parse_result_package(package)


class _FakeSMTP:
    instances: list["_FakeSMTP"] = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.logged_in = None
        self.message = None
        self.tls = False
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        return None

    def starttls(self, *, context):
        self.tls = True

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message, *, from_addr, to_addrs):
        self.message = (message, from_addr, tuple(to_addrs))


def test_ssl_send_embeds_cid_and_deduplicates_then_resends(tmp_path):
    package = _package(tmp_path, score=2.5)
    config_path = _config_file(tmp_path)
    env = {"TEST_SMTP_PASSWORD": "secret"}
    _FakeSMTP.instances.clear()
    first = send_package_email(
        package,
        config_path=config_path,
        environ=env,
        smtp_ssl_factory=_FakeSMTP,
    )
    assert first.status == "sent"
    smtp = _FakeSMTP.instances[-1]
    message = smtp.message[0]
    assert smtp.args[:2] == ("smtp.test", 465)
    assert smtp.logged_in == ("sender@example.com", "secret")
    assert "Content-ID:" in message.as_string()
    assert "Content-Disposition: inline" in message.as_string()
    assert "cid:" in message.get_body(preferencelist=("html",)).get_content()
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["runs"][package.name]["status"] == "sent"

    second = send_package_email(
        package,
        config_path=config_path,
        environ=env,
        smtp_ssl_factory=_FakeSMTP,
    )
    assert second.status == "skipped"
    assert len(_FakeSMTP.instances) == 1

    third = send_package_email(
        package,
        config_path=config_path,
        environ=env,
        resend=True,
        smtp_ssl_factory=_FakeSMTP,
    )
    assert third.status == "sent"
    assert len(_FakeSMTP.instances) == 2


def test_starttls_send_and_failure_state_is_retryable(tmp_path):
    package = _package(tmp_path, score=1)
    config_path = _config_file(tmp_path, security="starttls")
    _FakeSMTP.instances.clear()
    result = send_package_email(
        package,
        config_path=config_path,
        environ={"TEST_SMTP_PASSWORD": "secret"},
        smtp_starttls_factory=_FakeSMTP,
    )
    assert result.status == "sent"
    assert _FakeSMTP.instances[-1].tls is True
    assert _FakeSMTP.instances[-1].args[:2] == ("smtp.test", 587)

    class BrokenSMTP(_FakeSMTP):
        def send_message(self, *args, **kwargs):
            raise smtplib.SMTPException("temporary failure")

    with pytest.raises(NotificationSendError, match="temporary failure"):
        send_package_email(
            _package(tmp_path / "broken", score=1),
            config_path=_config_file(tmp_path / "broken", security="ssl"),
            environ={"TEST_SMTP_PASSWORD": "secret"},
            smtp_ssl_factory=BrokenSMTP,
        )
    failed_state = json.loads(
        ((tmp_path / "broken") / "state.json").read_text(encoding="utf-8")
    )
    assert failed_state["runs"][(tmp_path / "broken" / "20260903T080000_20260903T200000_A").name]["status"] == "failed"


def test_cli_preview_does_not_need_smtp_password(tmp_path, capsys):
    package = _package(tmp_path, score=0)
    config_path = _config_file(tmp_path)
    assert main([
        "send-email",
        "--package",
        str(package),
        "--config",
        str(config_path),
        "--preview",
    ]) == 0
    output = capsys.readouterr().out
    assert "Email notification preview" in output
    assert "本班次无扣分" in output


def test_dry_run_does_not_write_state_or_open_smtp(tmp_path):
    package = _package(tmp_path, score=1)
    config_path = _config_file(tmp_path)
    result = send_package_email(
        package,
        config_path=config_path,
        dry_run=True,
        smtp_ssl_factory=lambda *args, **kwargs: pytest.fail("SMTP must not open"),
    )
    assert result.status == "dry-run"
    assert not (tmp_path / "state.json").exists()

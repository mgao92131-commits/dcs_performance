"""Configuration for Result Package email notifications.

The configuration deliberately contains no password field.  SMTP secrets are
looked up from the environment only when a message is actually sent.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Mapping


CONFIG_ENV = "DCS_NOTIFICATION_CONFIG"
CONFIG_ENV_ALIAS = "DCS_EMAIL_CONFIG"
DEFAULT_CONFIG_FILENAME = "notification.config.json"
DEFAULT_PASSWORD_ENV = "DCS_SMTP_PASSWORD"
TEAM_IDS = ("A", "B", "C")
TEAM_LABELS = {"A": "甲", "B": "乙", "C": "丙"}


class NotificationConfigError(ValueError):
    """Raised when notification configuration is missing or invalid."""


@dataclass(frozen=True)
class TeamMailbox:
    """Recipients for one stable internal team ID."""

    team_id: str
    label: str
    recipients: tuple[str, ...]


@dataclass(frozen=True)
class SmtpConfig:
    """SMTP connection settings without a password."""

    host: str
    port: int
    security: str
    sender: str
    username: str | None = None
    password_env: str = DEFAULT_PASSWORD_ENV
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class NotificationConfig:
    """Validated notification settings."""

    smtp: SmtpConfig
    teams: Mapping[str, TeamMailbox]
    state_path: Path
    subject_prefix: str = "DCS班次考核通知"
    config_path: Path | None = None

    def mailbox_for(self, team_id: str) -> TeamMailbox:
        """Return the configured mailbox for an A/B/C team ID."""

        normalized = str(team_id).strip().upper()
        try:
            return self.teams[normalized]
        except KeyError as exc:
            raise NotificationConfigError(
                f"未配置班组 {team_id!r} 的收件邮箱（仅支持 A/B/C）"
            ) from exc

    def password(self, environ: Mapping[str, str] | None = None) -> str | None:
        """Read the SMTP password from an environment variable, never JSON."""

        if not self.smtp.username:
            return None
        env = os.environ if environ is None else environ
        password = env.get(self.smtp.password_env, "")
        if not password:
            raise NotificationConfigError(
                f"SMTP 已配置用户名，但环境变量 {self.smtp.password_env} 未设置"
            )
        return password


def load_notification_config(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> NotificationConfig:
    """Load and validate a JSON notification config.

    ``path`` wins over ``DCS_NOTIFICATION_CONFIG``.  If neither is supplied,
    ``notification.config.json`` in the current working directory is used.
    Relative ``state_path`` values are resolved next to the config file.
    """

    env = os.environ if environ is None else environ
    raw_path = path or env.get(CONFIG_ENV) or env.get(CONFIG_ENV_ALIAS) or DEFAULT_CONFIG_FILENAME
    config_path = Path(raw_path).expanduser().resolve()
    try:
        document = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise NotificationConfigError(
            f"邮件配置文件不存在: {config_path}；可用 --config 或 {CONFIG_ENV}/{CONFIG_ENV_ALIAS} 指定"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NotificationConfigError(f"无法读取邮件配置文件 {config_path}: {exc}") from exc

    if not isinstance(document, Mapping):
        raise NotificationConfigError("邮件配置根节点必须是 JSON 对象")
    smtp = _parse_smtp(document.get("smtp"), config_path)
    teams = _parse_teams(document, config_path)

    state_value = document.get("state_path", ".email-notification-state.json")
    state_path = _require_text(state_value, "state_path")
    state = Path(state_path).expanduser()
    if not state.is_absolute():
        state = config_path.parent / state

    prefix = document.get("subject_prefix", "DCS班次考核通知")
    prefix = _require_text(prefix, "subject_prefix")
    return NotificationConfig(
        smtp=smtp,
        teams=teams,
        state_path=state.resolve(),
        subject_prefix=prefix,
        config_path=config_path,
    )


def _parse_smtp(raw: object, config_path: Path) -> SmtpConfig:
    value = _require_mapping(raw, "smtp")
    if "password" in value:
        raise NotificationConfigError(
            f"{config_path} 不允许保存 smtp.password；请使用环境变量"
        )
    host = _require_text(value.get("host"), "smtp.host")
    security = str(value.get("security", "")).strip().lower()
    if not security and value.get("use_ssl") is True:
        security = "ssl"
    if not security and value.get("ssl") is True:
        security = "ssl"
    if not security and value.get("starttls") is True:
        security = "starttls"
    if security not in {"ssl", "starttls"}:
        raise NotificationConfigError("smtp.security 必须为 ssl 或 starttls")

    port_value = value.get("port")
    if port_value is None:
        port_value = 465 if security == "ssl" else 587
    if isinstance(port_value, bool) or not isinstance(port_value, int):
        raise NotificationConfigError("smtp.port 必须是整数")
    if not 1 <= port_value <= 65535:
        raise NotificationConfigError("smtp.port 必须在 1 到 65535 之间")

    username_value = value.get("username")
    username = None if username_value in (None, "") else _require_text(username_value, "smtp.username")
    sender_value = value.get(
        "from",
        value.get("sender", value.get("from_address", username)),
    )
    sender = _require_email(sender_value, "smtp.from")
    password_env = _require_text(
        value.get(
            "password_env",
            value.get("password_env_var", DEFAULT_PASSWORD_ENV),
        ),
        "smtp.password_env",
    )
    timeout_value = value.get("timeout_seconds", 30.0)
    if isinstance(timeout_value, bool) or not isinstance(timeout_value, (int, float)):
        raise NotificationConfigError("smtp.timeout_seconds 必须是数字")
    if not math.isfinite(float(timeout_value)) or float(timeout_value) <= 0:
        raise NotificationConfigError("smtp.timeout_seconds 必须是正数")
    return SmtpConfig(
        host=host,
        port=port_value,
        security=security,
        sender=sender,
        username=username,
        password_env=password_env,
        timeout_seconds=float(timeout_value),
    )


def _parse_teams(document: Mapping[str, Any], config_path: Path) -> dict[str, TeamMailbox]:
    raw_teams = document.get("teams", document.get("mailboxes"))
    if raw_teams is None:
        raw_teams = document.get(
            "team_recipients",
            document.get("team_emails", document.get("recipients")),
        )
    teams_value = _require_mapping(raw_teams, "teams")
    result: dict[str, TeamMailbox] = {}
    for team_id in TEAM_IDS:
        raw = None
        for key in (team_id, TEAM_LABELS[team_id], f"{team_id}班", f"{TEAM_LABELS[team_id]}班"):
            if key in teams_value:
                raw = teams_value[key]
                break
        if raw is None:
            raise NotificationConfigError(
                f"邮件配置缺少 {team_id}/{TEAM_LABELS[team_id]} 班收件邮箱"
            )
        recipients = _parse_recipients(raw, f"teams.{team_id}")
        result[team_id] = TeamMailbox(team_id, TEAM_LABELS[team_id], recipients)
    return result


def _parse_recipients(raw: object, context: str) -> tuple[str, ...]:
    if isinstance(raw, Mapping):
        for key in ("recipients", "to", "emails", "email", "address"):
            if key in raw:
                raw = raw[key]
                break
        else:
            raise NotificationConfigError(f"{context} 必须包含 recipients/to/emails/address")
    if isinstance(raw, str):
        values = [part.strip() for part in raw.replace(";", ",").split(",")]
    elif isinstance(raw, (list, tuple)):
        values = [item.strip() if isinstance(item, str) else item for item in raw]
    else:
        raise NotificationConfigError(f"{context} 收件邮箱必须是字符串或数组")
    if not values or any(not isinstance(item, str) or not item for item in values):
        raise NotificationConfigError(f"{context} 至少需要一个非空收件邮箱")
    normalized: list[str] = []
    for item in values:
        address = _require_email(item, f"{context}.recipients")
        if address not in normalized:
            normalized.append(address)
    return tuple(normalized)


def _require_mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NotificationConfigError(f"{context} 必须是 JSON 对象")
    return value


def _require_text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NotificationConfigError(f"{context} 必须是非空字符串")
    return value.strip()


def _require_email(value: object, context: str) -> str:
    text = _require_text(value, context)
    _, address = parseaddr(text)
    if address != text or "@" not in address or any(char.isspace() for char in address):
        raise NotificationConfigError(f"{context} 不是有效邮箱地址")
    return address


__all__ = [
    "CONFIG_ENV",
    "CONFIG_ENV_ALIAS",
    "DEFAULT_CONFIG_FILENAME",
    "DEFAULT_PASSWORD_ENV",
    "TEAM_IDS",
    "TEAM_LABELS",
    "NotificationConfig",
    "NotificationConfigError",
    "SmtpConfig",
    "TeamMailbox",
    "load_config",
    "load_notification_config",
]


# Short alias kept for small scripts and older local integrations.
load_config = load_notification_config

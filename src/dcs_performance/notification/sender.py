"""SMTP transport and idempotent sending for Result Package emails."""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import NotificationConfig, NotificationConfigError, load_notification_config
from .package import ResultPackage, parse_result_package
from .render import RenderedNotification, render_notification
from .state import NotificationState, NotificationStateError


class NotificationSendError(RuntimeError):
    """Raised when an email cannot be sent or its status cannot be recorded."""


@dataclass(frozen=True)
class SendResult:
    """Outcome of a send, preview, dry-run, or duplicate suppression."""

    status: str
    run_id: str
    package_path: Path
    recipients: tuple[str, ...]
    subject: str
    state_path: Path
    rendered: RenderedNotification | None = None
    error: str | None = None

    @property
    def sent(self) -> bool:
        return self.status == "sent"


def send_package_email(
    package: str | Path | ResultPackage,
    *,
    config: NotificationConfig | None = None,
    config_path: str | Path | None = None,
    state_path: str | Path | None = None,
    dry_run: bool = False,
    preview: bool = False,
    resend: bool = False,
    environ: Mapping[str, str] | None = None,
    smtp_ssl_factory: Callable[..., Any] | None = None,
    smtp_starttls_factory: Callable[..., Any] | None = None,
) -> SendResult:
    """Render and send one Result Package.

    ``dry_run`` and ``preview`` never open an SMTP connection or update the
    state file.  A successful send is suppressed on later calls unless
    ``resend`` is true.  Failed sends are recorded but remain retryable.
    """

    package_view = package if isinstance(package, ResultPackage) else parse_result_package(package)
    notification_config = config or load_notification_config(config_path, environ=environ)
    mailbox = notification_config.mailbox_for(package_view.team_id)
    rendered = render_notification(package_view, notification_config)
    resolved_state = Path(state_path).expanduser().resolve() if state_path else notification_config.state_path
    state = NotificationState.load(resolved_state)

    if preview:
        return SendResult(
            status="preview",
            run_id=package_view.run_id,
            package_path=package_view.package_path,
            recipients=mailbox.recipients,
            subject=rendered.subject,
            state_path=resolved_state,
            rendered=rendered,
        )
    if dry_run:
        return SendResult(
            status="dry-run",
            run_id=package_view.run_id,
            package_path=package_view.package_path,
            recipients=mailbox.recipients,
            subject=rendered.subject,
            state_path=resolved_state,
            rendered=rendered,
        )
    if not resend and state.is_sent(package_view.run_id):
        return SendResult(
            status="skipped",
            run_id=package_view.run_id,
            package_path=package_view.package_path,
            recipients=mailbox.recipients,
            subject=rendered.subject,
            state_path=resolved_state,
        )

    message = build_email_message(rendered, mailbox.recipients, notification_config.smtp.sender)
    password = notification_config.password(environ)
    try:
        _send_smtp(
            message,
            notification_config,
            mailbox.recipients,
            password,
            smtp_ssl_factory=smtp_ssl_factory,
            smtp_starttls_factory=smtp_starttls_factory,
        )
    except (
        OSError,
        smtplib.SMTPException,
        NotificationConfigError,
        NotificationSendError,
    ) as exc:
        error = _safe_error_text(exc, secrets=(password,))
        try:
            state.record(
                package_view.run_id,
                status="failed",
                package_path=package_view.package_path,
                recipients=mailbox.recipients,
                subject=rendered.subject,
                error=error,
            )
        except NotificationStateError as state_exc:
            raise NotificationSendError(
                f"邮件发送失败且无法记录状态: {error}; {state_exc}"
            ) from exc
        raise NotificationSendError(f"邮件发送失败: {error}") from exc

    try:
        state.record(
            package_view.run_id,
            status="sent",
            package_path=package_view.package_path,
            recipients=mailbox.recipients,
            subject=rendered.subject,
        )
    except NotificationStateError as exc:
        raise NotificationSendError(
            f"邮件已发送，但无法记录发送状态: {exc}"
        ) from exc
    return SendResult(
        status="sent",
        run_id=package_view.run_id,
        package_path=package_view.package_path,
        recipients=mailbox.recipients,
        subject=rendered.subject,
        state_path=resolved_state,
        rendered=rendered,
    )


def build_email_message(
    rendered: RenderedNotification,
    recipients: tuple[str, ...] | list[str],
    sender: str,
) -> EmailMessage:
    """Create a multipart/alternative + related message with CID PNGs."""

    message = EmailMessage(policy=SMTP)
    message["Subject"] = rendered.subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(rendered.text)
    message.add_alternative(rendered.html, subtype="html")
    html_part = message.get_body(preferencelist=("html",))
    if html_part is None:  # pragma: no cover - guarded by add_alternative above
        raise NotificationSendError("无法构建 HTML 邮件正文")
    for image in rendered.images:
        html_part.add_related(
            image.data,
            maintype="image",
            subtype="png",
            cid=f"<{image.cid}>",
            filename=image.filename,
        )
        # ``add_related`` adds a filename for mail clients that need one but
        # marks it as an attachment by default.  CID images should be inline;
        # preserve the filename while changing only the disposition header.
        related_part = html_part.get_payload()[-1]
        disposition = f'inline; filename="{image.filename}"'
        if related_part.get("Content-Disposition"):
            related_part.replace_header("Content-Disposition", disposition)
        else:  # pragma: no cover - current email package always adds it
            related_part.add_header("Content-Disposition", "inline", filename=image.filename)
    return message


def _send_smtp(
    message: EmailMessage,
    config: NotificationConfig,
    recipients: tuple[str, ...],
    password: str | None,
    *,
    smtp_ssl_factory: Callable[..., Any] | None,
    smtp_starttls_factory: Callable[..., Any] | None,
) -> None:
    smtp = config.smtp
    if smtp.security == "ssl":
        factory = smtp_ssl_factory or smtplib.SMTP_SSL
        connection = factory(smtp.host, smtp.port, timeout=smtp.timeout_seconds, context=ssl.create_default_context())
    elif smtp.security == "starttls":
        factory = smtp_starttls_factory or smtplib.SMTP
        connection = factory(smtp.host, smtp.port, timeout=smtp.timeout_seconds)
    else:  # validated config should make this unreachable
        raise NotificationSendError(f"不支持 SMTP 安全方式: {smtp.security}")

    try:
        with connection as client:
            if smtp.security == "starttls":
                client.ehlo()
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if smtp.username:
                if password is None:
                    raise NotificationConfigError(
                        f"SMTP 已配置用户名，但环境变量 {smtp.password_env} 未设置"
                    )
                client.login(smtp.username, password)
            refused = client.send_message(
                message,
                from_addr=smtp.sender,
                to_addrs=list(recipients),
            )
            if refused:
                refused_text = ", ".join(str(address) for address in refused)
                raise NotificationSendError(f"SMTP 拒收收件人: {refused_text}")
    except NotificationConfigError:
        raise
    except (OSError, smtplib.SMTPException):
        raise
    except Exception as exc:
        # Test doubles and third-party SMTP adapters may use a different
        # exception type; expose it consistently without leaking credentials.
        raise NotificationSendError(f"SMTP transport error: {exc}") from exc


def _safe_error_text(
    exc: BaseException,
    *,
    secrets: tuple[str | None, ...] = (),
) -> str:
    text = str(exc).strip()
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text or exc.__class__.__name__


send_email = send_package_email


__all__ = [
    "NotificationSendError",
    "SendResult",
    "build_email_message",
    "send_email",
    "send_package_email",
]

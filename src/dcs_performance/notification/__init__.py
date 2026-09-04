"""Result Package email notifications.

The public API is intentionally independent from DCS access and Excel report
writing.  Callers provide a published Result Package path and receive a
rendered or sent notification.
"""

from .config import (
    CONFIG_ENV,
    CONFIG_ENV_ALIAS,
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_PASSWORD_ENV,
    TEAM_IDS,
    TEAM_LABELS,
    NotificationConfig,
    NotificationConfigError,
    SmtpConfig,
    TeamMailbox,
    load_config,
    load_notification_config,
)
from .package import (
    DeductionPoint,
    PackageEvent,
    ResultPackage,
    ResultPackageError,
    parse_package,
    parse_result_package,
)
from .render import (
    InlineImage,
    RenderedNotification,
    render_email,
    render_html,
    render_notification,
    render_plain_text,
)
from .sender import (
    NotificationSendError,
    SendResult,
    build_email_message,
    send_email,
    send_package_email,
)
from .state import NotificationState, NotificationStateError

__all__ = [
    "CONFIG_ENV",
    "CONFIG_ENV_ALIAS",
    "DEFAULT_CONFIG_FILENAME",
    "DEFAULT_PASSWORD_ENV",
    "TEAM_IDS",
    "TEAM_LABELS",
    "DeductionPoint",
    "InlineImage",
    "NotificationConfig",
    "NotificationConfigError",
    "NotificationSendError",
    "NotificationState",
    "NotificationStateError",
    "PackageEvent",
    "RenderedNotification",
    "ResultPackage",
    "ResultPackageError",
    "SendResult",
    "SmtpConfig",
    "TeamMailbox",
    "build_email_message",
    "load_config",
    "load_notification_config",
    "parse_result_package",
    "parse_package",
    "render_email",
    "render_html",
    "render_notification",
    "render_plain_text",
    "send_email",
    "send_package_email",
]

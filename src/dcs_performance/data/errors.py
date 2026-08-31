"""Typed errors for transport, protocol, and DCS data-integrity failures."""

from collections.abc import Mapping


class DcsServiceError(RuntimeError):
    """Base error exposed by the DCS data-access layer."""

    status_code: int | None
    code: str | None
    message: str
    context: dict[str, object]
    response_body_summary: str | None

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        context: Mapping[str, object] | None = None,
        response_body_summary: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.context = dict(context or {})
        self.response_body_summary = response_body_summary
        super().__init__(message)

    def add_context(self, **values: object) -> "DcsServiceError":
        """Attach bounded diagnostic context without changing the error code."""

        self.context.update(values)
        return self


class DcsArgumentError(DcsServiceError, ValueError):
    """A locally invalid client argument, such as an aware datetime."""


class DcsProtocolError(DcsServiceError):
    """A malformed or unexpected response from the service."""


class DcsTransportError(DcsServiceError):
    """A temporary or final failure while making an HTTP request."""


class DcsRequestTimeoutError(DcsTransportError):
    """A timeout or temporary network failure."""


class DcsServiceBusyError(DcsServiceError):
    """The service asked the client to retry later."""


class DcsDataIntegrityError(DcsServiceError):
    """The service reports that returned Event data may be incomplete."""


class DcsHistoryQueryTooLargeError(DcsServiceError):
    """A History request exceeded the service's supported result size."""

    tag: str | None
    start_time: object | None
    end_time: object | None

    def __init__(self, message: str, **kwargs: object) -> None:
        self.tag = None
        self.start_time = None
        self.end_time = None
        super().__init__(message, **kwargs)  # type: ignore[arg-type]


_DATA_INTEGRITY_CODES = frozenset(
    {
        "source_changed",
        "event_cursor_expired",
        "retention_gap",
        "cursor_ahead",
        "cursor_window_empty",
        "event_overflow",
        "event_journal_full",
    }
)


def error_from_code(
    *,
    status_code: int,
    code: str,
    message: str,
    response_body_summary: str | None = None,
) -> DcsServiceError:
    """Map the documented ``error.code`` to a stable exception class."""

    kwargs = {
        "status_code": status_code,
        "code": code,
        "response_body_summary": response_body_summary,
    }
    if code in _DATA_INTEGRITY_CODES:
        return DcsDataIntegrityError(message, **kwargs)
    if code == "history_query_too_large":
        return DcsHistoryQueryTooLargeError(message, **kwargs)
    if code == "request_timeout":
        return DcsRequestTimeoutError(message, **kwargs)
    if code == "service_busy":
        return DcsServiceBusyError(message, **kwargs)
    return DcsServiceError(message, **kwargs)


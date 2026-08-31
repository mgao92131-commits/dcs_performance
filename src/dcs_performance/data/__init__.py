"""DCS service data-access interfaces, models, and errors."""

from .client import DcsDataClient
from .dcs_service import DcsServiceClient
from .errors import (
    DcsArgumentError,
    DcsDataIntegrityError,
    DcsHistoryQueryTooLargeError,
    DcsServiceError,
)
from .models import (
    DcsEvent,
    EventCursor,
    EventPage,
    HistorySample,
    ServiceInfo,
    TagInfo,
)
from .transport import DcsHttpTransport, HttpResponse

__all__ = [
    "DcsArgumentError",
    "DcsDataIntegrityError",
    "DcsDataClient",
    "DcsEvent",
    "DcsHistoryQueryTooLargeError",
    "DcsHttpTransport",
    "DcsServiceClient",
    "DcsServiceError",
    "EventCursor",
    "EventPage",
    "HistorySample",
    "HttpResponse",
    "ServiceInfo",
    "TagInfo",
]

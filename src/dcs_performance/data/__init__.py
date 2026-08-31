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
from .history_context import (
    DEFAULT_LOOKBACK_STEPS,
    HistoryContext,
    get_history_with_previous_sample,
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
    "DEFAULT_LOOKBACK_STEPS",
    "EventCursor",
    "EventPage",
    "HistorySample",
    "HistoryContext",
    "HttpResponse",
    "ServiceInfo",
    "TagInfo",
    "get_history_with_previous_sample",
]

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
    DEFAULT_FORWARD_SEARCH_STEPS,
    DEFAULT_RECOVERY_SEARCH_STEPS,
    HistoryContext,
    MAX_FORWARD_QUERY_SPAN,
    find_next_sample,
    get_histories_with_previous_samples,
    get_history_with_previous_sample,
)
from .transport import DcsHttpTransport, HttpResponse
from .settings import DEFAULT_DCS_SERVICE_BASE_URL

__all__ = [
    "DcsArgumentError",
    "DcsDataIntegrityError",
    "DcsDataClient",
    "DcsEvent",
    "DcsHistoryQueryTooLargeError",
    "DcsHttpTransport",
    "DcsServiceClient",
    "DEFAULT_DCS_SERVICE_BASE_URL",
    "DcsServiceError",
    "DEFAULT_FORWARD_SEARCH_STEPS",
    "DEFAULT_LOOKBACK_STEPS",
    "DEFAULT_RECOVERY_SEARCH_STEPS",
    "EventCursor",
    "EventPage",
    "HistorySample",
    "HistoryContext",
    "HttpResponse",
    "MAX_FORWARD_QUERY_SPAN",
    "ServiceInfo",
    "TagInfo",
    "find_next_sample",
    "get_histories_with_previous_samples",
    "get_history_with_previous_sample",
]

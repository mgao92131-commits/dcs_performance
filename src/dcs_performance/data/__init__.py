"""DCS service data-access interfaces, models, and errors."""

from .client import DcsDataClient
from .dcs_service import DcsServiceClient
from .errors import (
    DcsArgumentError,
    DcsDataIntegrityError,
    DcsIncompleteStreamError,
    DcsHistoryQueryTooLargeError,
    DcsProtocolError,
    DcsRequestTimeoutError,
    DcsServiceError,
    DcsTransportError,
)
from .models import (
    DcsEvent,
    EventCursor,
    HistorySample,
    ServiceInfo,
    TagInfo,
)
from .history_context import (
    DEFAULT_LOOKBACK_STEPS,
    DEFAULT_FORWARD_SEARCH_STEPS,
    DEFAULT_RECOVERY_SEARCH_STEPS,
    HistoryContext,
    find_next_sample,
    get_histories_with_previous_samples,
    get_history_with_previous_sample,
)
from .transport import DcsHttpTransport, HttpResponse, HttpStreamResponse
from .settings import DEFAULT_DCS_SERVICE_BASE_URL

__all__ = [
    "DcsArgumentError",
    "DcsDataIntegrityError",
    "DcsDataClient",
    "DcsEvent",
    "DcsIncompleteStreamError",
    "DcsHistoryQueryTooLargeError",
    "DcsHttpTransport",
    "DcsProtocolError",
    "DcsRequestTimeoutError",
    "DcsServiceClient",
    "DEFAULT_DCS_SERVICE_BASE_URL",
    "DcsServiceError",
    "DcsTransportError",
    "DEFAULT_FORWARD_SEARCH_STEPS",
    "DEFAULT_LOOKBACK_STEPS",
    "DEFAULT_RECOVERY_SEARCH_STEPS",
    "EventCursor",
    "HistorySample",
    "HistoryContext",
    "HttpResponse",
    "HttpStreamResponse",
    "ServiceInfo",
    "TagInfo",
    "find_next_sample",
    "get_histories_with_previous_samples",
    "get_history_with_previous_sample",
]

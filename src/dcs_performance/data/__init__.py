"""DCS service data-access interfaces, models, and errors."""

from .client import DcsDataClient
from .dcs_service import DcsServiceClient
from .errors import (
    DcsArgumentError,
    DcsDataIntegrityError,
    DcsIncompleteStreamError,
    DcsProtocolError,
    DcsRequestTimeoutError,
    DcsServiceError,
    DcsTransportError,
)
from .models import (
    DcsEvent,
    HistorySample,
    ServiceInfo,
    TagInfo,
)
from .history_quality import (
    GOOD_DELTA_V_STATUS,
    VALID_ARCHIVE_STATUS,
    NumericHistorySample,
    PreparedHistory,
    build_numeric_segments,
    is_usable_history_sample,
    prepare_numeric_history,
    split_valid_numeric_segments,
    trailing_mean_segments,
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
    "HistorySample",
    "GOOD_DELTA_V_STATUS",
    "VALID_ARCHIVE_STATUS",
    "NumericHistorySample",
    "PreparedHistory",
    "HistoryContext",
    "HttpResponse",
    "HttpStreamResponse",
    "ServiceInfo",
    "TagInfo",
    "find_next_sample",
    "get_histories_with_previous_samples",
    "get_history_with_previous_sample",
    "build_numeric_segments",
    "is_usable_history_sample",
    "prepare_numeric_history",
    "split_valid_numeric_segments",
    "trailing_mean_segments",
]

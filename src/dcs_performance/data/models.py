"""Models at the dcs-service V1 data boundary."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class HistorySample:
    """One raw Historian sample from the V1 History CSV."""

    timestamp: datetime
    value: str
    data_type: str
    delta_v_status: str
    archive_status: str
    sequence_no: int
    is_history_hole: bool
    is_cr_hole: bool
    is_manually_deleted: bool
    is_manually_inserted: bool


@dataclass(frozen=True)
class DcsEvent:
    """One instantaneous raw event from the V1 Event CSV."""

    timestamp: datetime
    frac_sec: int
    ord: int
    event_type: str
    event_sub_type: str
    category: str
    area: str
    node: str
    unit: str
    module: str
    module_description: str
    attribute: str
    state: str
    event_level: str
    desc1: str
    desc2: str
    is_archived: bool | None

    # Keep the protocol text so exact seven-digit timestamps can be compared
    # for ordering or preserved in a future incremental checkpoint.
    timestamp_raw: str | None = None


@dataclass(frozen=True)
class ServiceInfo:
    """The service capabilities returned by ``GET /api/v1/info``."""

    service: str
    version: str
    historian_server: str
    source_timezone: str
    history_max_concurrent: int
    event_max_concurrent: int
    history_stream_window_minutes: int
    event_stream_window_minutes: int
    read_only: bool


@dataclass(frozen=True)
class TagInfo:
    """The server's semantic result for one Historian TAG lookup."""

    tag: str
    status: str
    data_type: str | None


@dataclass(frozen=True)
class EventCursor:
    """An Event incremental-synchronisation checkpoint.

    This is not a pagination cursor for a fixed Event Range query.  The
    ``datetime_raw`` value preserves the exact server timestamp text for a
    future checkpoint request because Python ``datetime`` stores only six
    fractional digits while the protocol may provide seven.
    """

    datetime: datetime
    frac_sec: int
    ord: int
    datetime_raw: str | None = None

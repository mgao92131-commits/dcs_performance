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
    # with the next-cursor header without reconstructing them from datetime.
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
    read_only: bool


@dataclass(frozen=True)
class TagInfo:
    """The server's semantic result for one Historian TAG lookup."""

    tag: str
    status: str
    data_type: str | None


@dataclass(frozen=True)
class EventCursor:
    """The complete V1 Event cursor.

    ``datetime_raw`` preserves the exact server header text for subsequent
    cursor requests.  This is important because Python ``datetime`` stores
    microseconds, while the protocol may provide more timestamp precision.
    """

    datetime: datetime
    frac_sec: int
    ord: int
    datetime_raw: str | None = None


@dataclass(frozen=True)
class EventPage:
    """One validated Event Range or Cursor response page."""

    events: list[DcsEvent]
    source_generation: str
    has_more: bool
    next_datetime_raw: str | None
    next_frac_sec: int | None
    next_ord: int | None

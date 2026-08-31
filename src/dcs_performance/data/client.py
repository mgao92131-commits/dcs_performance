"""The boundary for reading DCS data.

No database or network implementation belongs in this phase.  Rules may keep
an implementation of this protocol during construction, while their public
``evaluate`` method remains time-range-only.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import DcsEvent, HistorySample


@runtime_checkable
class DcsDataClient(Protocol):
    """Minimal read-only interface required by future assessment rules."""

    def get_history(
        self,
        tag: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[HistorySample]:
        """Return historical tag samples in the requested time range."""

        ...
    def get_events(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[DcsEvent]:
        """Return raw DCS events in the requested time range."""

        ...

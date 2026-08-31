"""The only data interface visible to assessment rules."""

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
        """Return one TAG's raw Historian samples in the requested range."""

        ...

    def get_histories(
        self,
        tags: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, list[HistorySample]]:
        """Return multiple TAG histories through controlled client requests."""

        ...

    def get_events(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[DcsEvent]:
        """Return all raw events in the fixed half-open time range."""

        ...

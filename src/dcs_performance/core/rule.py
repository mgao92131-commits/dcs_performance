"""The single public interface implemented by assessment rules."""

from collections.abc import Collection
from datetime import datetime
from typing import Protocol, runtime_checkable

from .event import AssessmentEvent


@runtime_checkable
class AssessmentRule(Protocol):
    """A rule that finds assessment events in a requested time range."""

    id: str
    name: str

    def evaluate(
        self,
        start_time: datetime,
        end_time: datetime,
        *,
        point_ids: Collection[str] | None = None,
    ) -> list[AssessmentEvent]:
        """Return events for all, or the requested subset of, enabled points."""

        ...

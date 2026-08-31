"""The single public interface implemented by assessment rules."""

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
    ) -> list[AssessmentEvent]:
        """Return all assessment events in ``start_time``/``end_time``."""

        ...

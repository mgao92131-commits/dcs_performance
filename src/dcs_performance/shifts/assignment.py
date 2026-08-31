"""Interfaces for assigning rule events to shifts and teams."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from dcs_performance.core.event import AssessmentEvent

from .model import Shift
from .resolver import ShiftResolver


@dataclass(frozen=True)
class AssignedEventSlice:
    """The part of an assessment event attributed to one concrete shift."""

    shift: Shift
    event_start: datetime
    event_end: datetime

    def __post_init__(self) -> None:
        if self.event_end <= self.event_start:
            raise ValueError("assigned event end_time must be after start_time")
        if self.event_start < self.shift.start_time:
            raise ValueError("assigned event starts before its shift")
        if self.event_end > self.shift.end_time:
            raise ValueError("assigned event ends after its shift")


@runtime_checkable
class EventAssigner(Protocol):
    """Map one rule event to one or more shift-owned event slices."""

    def assign(self, event: AssessmentEvent) -> list[AssignedEventSlice]:
        ...


class SingleShiftAssigner:
    """Phase-one assigner for events fully contained in one shift.

    Cross-shift splitting is intentionally rejected until a production policy
    is agreed.  This avoids silently attributing a cross-boundary event to the
    wrong team.
    """

    def __init__(self, resolver: ShiftResolver) -> None:
        self.resolver = resolver

    def assign(self, event: AssessmentEvent) -> list[AssignedEventSlice]:
        shift = self.resolver.resolve(event.start_time)
        if event.end_time > shift.end_time:
            raise NotImplementedError(
                "cross-shift event splitting is deferred to a later phase"
            )
        return [
            AssignedEventSlice(
                shift=shift,
                event_start=event.start_time,
                event_end=event.end_time,
            )
        ]

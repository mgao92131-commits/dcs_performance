"""The normalized event returned by every assessment rule."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class AssessmentEvent:
    """A problem that should be assessed during a time interval.

    Team ownership and score are deliberately absent.  Those are assigned by
    later stages after a rule has returned its time-local findings.
    """

    start_time: datetime
    end_time: datetime
    message: str = ""
    data: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.end_time <= self.start_time:
            raise ValueError("assessment event end_time must be after start_time")

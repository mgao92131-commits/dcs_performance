"""The value object for an already-resolved shift."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Shift:
    """A concrete team assignment for one continuous time interval.

    ``Shift`` stores the result of scheduling.  It intentionally contains no
    rotation, overtime, swap, or calendar algorithm.
    """

    team_id: str
    start_time: datetime
    end_time: datetime
    shift_type: str

    def __post_init__(self) -> None:
        if self.end_time <= self.start_time:
            raise ValueError("shift end_time must be after start_time")

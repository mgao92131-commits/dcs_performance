"""Models for the assigned and scored result stage."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class AssignedAssessmentEvent:
    """An assessment event after rule, shift/team, and score are known."""

    rule_id: str
    rule_name: str
    team_id: str
    shift_start: datetime
    shift_end: datetime
    event_start: datetime
    event_end: datetime
    score: float
    message: str = ""
    window_start: datetime | None = None
    window_end: datetime | None = None
    data: dict[str, object] = field(default_factory=dict)

"""Models for the later assigned/scored result stage."""

from dataclasses import dataclass
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

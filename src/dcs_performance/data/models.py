"""Models returned by a future DCS data client.

These models deliberately do not know about shifts, teams, rules, or scores.
They are the small boundary objects that a concrete DCS adapter can return.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class HistorySample:
    """One historical value for a DCS tag."""

    timestamp: datetime
    value: object


@dataclass(frozen=True)
class DcsEvent:
    """A raw event returned by a DCS event source."""

    start_time: datetime
    end_time: datetime
    message: str = ""
    data: dict[str, object] = field(default_factory=dict)

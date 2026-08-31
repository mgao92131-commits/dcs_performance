"""Future scheduling-calendar boundary.

The production calendar will eventually account for three-shift/two-shift
rotation, overtime, swaps, and temporary replacements.  None of that policy
is implemented in phase one.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from .model import Shift


@runtime_checkable
class ShiftCalendar(Protocol):
    """Provide resolved shifts that overlap a requested time range."""

    def get_shifts(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Shift]:
        """Return scheduled shifts overlapping ``start_time``/``end_time``."""

        ...

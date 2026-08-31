"""A deliberately empty assessment rule used as an interface example."""

from datetime import datetime
from typing import Any, Mapping

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.data.client import DcsDataClient


class Rule:
    """A rule that validates the common shape and returns no findings."""

    id = "example_rule"
    name = "示例考核规则"

    def __init__(
        self,
        data_client: DcsDataClient | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self.data = data_client
        self.config = dict(config or {})
        # The directory config is authoritative when the rule is loaded.  The
        # class attributes keep the rule directly usable in a small test.
        self.id = str(self.config.get("id", self.id))
        self.name = str(self.config.get("name", self.name))

    def evaluate(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[AssessmentEvent]:
        """Return no events; this rule must not access real DCS data."""

        if end_time <= start_time:
            raise ValueError("end_time must be after start_time")
        return []

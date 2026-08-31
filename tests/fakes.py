"""In-memory DCS client used by rule and pipeline tests."""

from collections.abc import Iterable, Mapping
from datetime import datetime

from dcs_performance.data.models import DcsEvent, HistorySample


class FakeDataClient:
    """Implement the public DCS client shape without HTTP or CSV parsing."""

    def __init__(
        self,
        histories: Mapping[str, Iterable[HistorySample]] | None = None,
    ) -> None:
        self.histories = {
            tag: list(samples) for tag, samples in (histories or {}).items()
        }
        self.calls: list[tuple[str, datetime, datetime]] = []

    def get_history(
        self,
        tag: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[HistorySample]:
        self.calls.append((tag, start_time, end_time))
        return [
            sample
            for sample in self.histories.get(tag, [])
            if start_time <= sample.timestamp < end_time
        ]

    def get_histories(
        self,
        tags: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, list[HistorySample]]:
        return {
            tag: self.get_history(tag, start_time, end_time)
            for tag in dict.fromkeys(tags)
        }

    def get_events(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[DcsEvent]:
        return []


def make_history_sample(
    timestamp: datetime,
    value: str,
    sequence_no: int = 1,
) -> HistorySample:
    return HistorySample(
        timestamp=timestamp,
        value=value,
        data_type="Digital",
        delta_v_status="Good",
        archive_status="HistoryDataIsValid",
        sequence_no=sequence_no,
        is_history_hole=False,
        is_cr_hole=False,
        is_manually_deleted=False,
        is_manually_inserted=False,
    )

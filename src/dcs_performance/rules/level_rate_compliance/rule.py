"""Rule adapter for the sustained esterification-level rate check."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.data.client import DcsDataClient

from dcs_performance.rules.level_rate_compliance.config import (
    LevelRateConfig,
    PointConfig,
    parse_config,
)
from dcs_performance.rules.level_rate_compliance.detector import LevelRateDetector


DEFAULT_RULE_ID = "level_rate_compliance"
DEFAULT_RULE_NAME = "酯化液位变化速率考核"


class Rule:
    id = DEFAULT_RULE_ID
    name = DEFAULT_RULE_NAME

    def __init__(
        self,
        data_client: DcsDataClient | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        if data_client is None:
            raise ValueError("level_rate_compliance requires a data client")
        if not callable(getattr(data_client, "get_history", None)):
            raise TypeError("data_client must provide get_history()")
        if config is None or not isinstance(config, Mapping):
            raise ValueError("level_rate_compliance config must be an object")
        self.data_client = data_client
        self.config = dict(config)
        self.typed_config: LevelRateConfig = parse_config(config)
        self.id = self.typed_config.id
        self.name = self.typed_config.name
        self.points = self.typed_config.points
        self.detector = LevelRateDetector()

    def evaluate(self, start_time: datetime, end_time: datetime) -> list[AssessmentEvent]:
        _validate_range(start_time, end_time)
        events: list[AssessmentEvent] = []
        for point in self.points:
            if not point.enabled:
                continue
            lookback = point.rate_window_seconds + point.smoothing.window_seconds + point.max_gap_seconds
            observation_end = end_time + timedelta(
                seconds=point.persistence_seconds + point.merge_gap_seconds
            )
            samples = self.data_client.get_history(
                point.history_tag,
                start_time - timedelta(seconds=lookback),
                observation_end,
            )
            occurrences = self.detector.detect(
                samples,
                point,
                observation_end=observation_end,
            )
            for occurrence in occurrences:
                if not (start_time <= occurrence.start_time < end_time):
                    continue
                clipped_end = min(occurrence.end_time, end_time)
                if clipped_end <= occurrence.start_time:
                    continue
                events.append(
                    AssessmentEvent(
                        start_time=occurrence.start_time,
                        end_time=clipped_end,
                        message=_message(point, occurrence.direction),
                        data={
                            "point_id": point.id,
                            "history_tag": point.history_tag,
                            "event_type": "level_rate",
                            "score_key": occurrence.direction,
                            "direction": occurrence.direction,
                            "rate_window_seconds": point.rate_window_seconds,
                            "rate_lower_limit": point.lower_rate,
                            "rate_upper_limit": point.upper_rate,
                            "persistence_seconds": point.persistence_seconds,
                            "smoothing_window_seconds": point.smoothing.window_seconds,
                            "confirmation_time": occurrence.confirmation_time,
                            "peak_rate_per_hour": occurrence.peak_rate,
                            "mean_rate_per_hour": occurrence.mean_rate,
                            "is_open": occurrence.is_open,
                            "event_key": (
                                f"{self.id}:{point.id}:{occurrence.direction}:"
                                f"{occurrence.start_time.isoformat()}"
                            ),
                        },
                    )
                )
        return sorted(events, key=lambda event: (event.start_time, str(event.data.get("point_id", ""))))


def _message(point: PointConfig, direction: str) -> str:
    text = "下降" if direction == "rate_down" else "上升"
    return f"{point.id} 液位{text}速率超过 ±0.14 液位/小时并持续2小时"


def _validate_range(start_time: datetime, end_time: datetime) -> None:
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        raise TypeError("start_time and end_time must be datetime values")
    if (start_time.tzinfo is None) != (end_time.tzinfo is None):
        raise ValueError("start_time and end_time must have matching timezone modes")
    if start_time.tzinfo is not None:
        raise ValueError("level_rate_compliance requires timezone-naive local time")
    if end_time <= start_time:
        raise ValueError("end_time must be after start_time")

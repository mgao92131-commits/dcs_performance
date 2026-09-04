"""Rule adapter for slurry-feed versus SY-total balance assessment."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime, timedelta
from typing import Any

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.core.points import select_points
from dcs_performance.data.client import DcsDataClient

from dcs_performance.rules.flow_balance_compliance.config import (
    FlowBalanceConfig,
    PointConfig,
    parse_config,
)
from dcs_performance.rules.flow_balance_compliance.detector import (
    FLOW_HIGH,
    FLOW_LOW,
    FlowBalanceDetector,
)


DEFAULT_RULE_ID = "flow_balance_compliance"
DEFAULT_RULE_NAME = "浆料进料量平衡考核"


class Rule:
    id = DEFAULT_RULE_ID
    name = DEFAULT_RULE_NAME

    def __init__(
        self,
        data_client: DcsDataClient | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        if data_client is None:
            raise ValueError("flow_balance_compliance requires a data client")
        if not callable(getattr(data_client, "get_histories", None)):
            raise TypeError("data_client must provide get_histories()")
        if config is None or not isinstance(config, Mapping):
            raise ValueError("flow_balance_compliance config must be an object")
        self.data_client = data_client
        self.config = dict(config)
        self.typed_config: FlowBalanceConfig = parse_config(config)
        self.id = self.typed_config.id
        self.name = self.typed_config.name
        self.points = self.typed_config.points
        self.detector = FlowBalanceDetector()

    def evaluate(
        self,
        start_time: datetime,
        end_time: datetime,
        *,
        point_ids: Collection[str] | None = None,
    ) -> list[AssessmentEvent]:
        _validate_range(start_time, end_time)
        events: list[AssessmentEvent] = []
        selected_points = select_points(
            self.points,
            point_ids,
            rule_id=self.id,
        )
        for point in selected_points:
            tags = [point.logic_tag, *point.sy_tags]
            lookback = point.smoothing.window_seconds + point.max_gap_seconds
            observation_end = end_time + timedelta(
                seconds=point.min_duration_seconds + point.merge_gap_seconds
            )
            histories = self.data_client.get_histories(
                tags,
                start_time - timedelta(seconds=lookback),
                observation_end,
            )
            occurrences = self.detector.detect(
                histories,
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
                            "logic_tag": point.logic_tag,
                            "sy_tags": list(point.sy_tags),
                            "event_type": "flow_balance",
                            "score_key": occurrence.direction,
                            "direction": occurrence.direction,
                            "low_limit": point.low_limit,
                            "high_limit": point.high_limit,
                            "smoothing_window_seconds": point.smoothing.window_seconds,
                            "min_duration_seconds": point.min_duration_seconds,
                            "mean_difference": occurrence.mean_difference,
                            "minimum_difference": occurrence.minimum_difference,
                            "maximum_difference": occurrence.maximum_difference,
                            "peak_difference": occurrence.peak_difference,
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
    text = "低于" if direction == FLOW_LOW else "高于"
    return f"{point.id} LOGIC27与SY总流量偏差{text}±15并持续5分钟"


def _validate_range(start_time: datetime, end_time: datetime) -> None:
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        raise TypeError("start_time and end_time must be datetime values")
    if (start_time.tzinfo is None) != (end_time.tzinfo is None):
        raise ValueError("start_time and end_time must have matching timezone modes")
    if start_time.tzinfo is not None:
        raise ValueError("flow_balance_compliance requires timezone-naive local time")
    if end_time <= start_time:
        raise ValueError("end_time must be after start_time")

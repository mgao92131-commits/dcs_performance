"""Assessment rule for continuous digital high alarms."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from math import isfinite
from typing import Any

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.data.client import DcsDataClient
from dcs_performance.data.history_context import get_history_with_previous_sample

from dcs_performance.rules.persistent_high_alarm.detector import (
    PersistentHighAlarmDetector,
    parse_digital_state,
)


CONFIRMATION_MARGIN = timedelta(seconds=1)
DEFAULT_RULE_ID = "persistent_high_alarm"
DEFAULT_RULE_NAME = "持续高报考核"


class Rule:
    """Read configured Historian states and return qualifying alarm events."""

    id = DEFAULT_RULE_ID
    name = DEFAULT_RULE_NAME

    def __init__(
        self,
        data_client: DcsDataClient | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        if data_client is None:
            raise ValueError("persistent_high_alarm requires a data client")
        if not callable(getattr(data_client, "get_history", None)):
            raise TypeError("data_client must provide get_history()")
        if config is None or not isinstance(config, Mapping):
            raise ValueError("persistent_high_alarm config must be an object")

        self.data = data_client
        self.data_client = data_client
        self.config = dict(config)
        self.id = _required_text(self.config, "id", default=DEFAULT_RULE_ID)
        self.name = _required_text(self.config, "name", default=DEFAULT_RULE_NAME)
        if self.id != DEFAULT_RULE_ID:
            raise ValueError(
                f"persistent_high_alarm config id must be {DEFAULT_RULE_ID!r}"
            )

        parameters = self.config.get("parameters")
        if not isinstance(parameters, Mapping):
            raise ValueError("persistent_high_alarm parameters must be an object")

        raw_points = parameters.get("points")
        self.points = _validate_points(raw_points)
        self.threshold_seconds = _validate_threshold(
            parameters.get("threshold_seconds")
        )
        raw_active_value = parameters.get("active_value")
        self.active_value = parse_digital_state(_required_value(
            raw_active_value,
            "parameters.active_value",
        ))
        self.detector = PersistentHighAlarmDetector(
            threshold_seconds=self.threshold_seconds,
            active_value=self.active_value,
        )

    def evaluate(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[AssessmentEvent]:
        """Evaluate the responsibility range, with a forward confirmation tail."""

        _validate_range(start_time, end_time)
        query_end = end_time + timedelta(
            seconds=self.threshold_seconds
        ) + CONFIRMATION_MARGIN

        events: list[AssessmentEvent] = []
        for point in self.points:
            point_id = point["id"]
            history_tag = point["history_tag"]
            samples = get_history_with_previous_sample(
                self.data,
                history_tag,
                start_time,
                query_end,
            )
            occurrences = self.detector.detect(
                samples,
                point_id,
                history_tag,
                start_time,
                observation_end=query_end,
            )
            for occurrence in occurrences:
                # Keep this ownership check at the Rule boundary even though
                # the detector also excludes pre-window intervals.  The
                # assessment contract is explicitly based on alarm_start.
                if not (start_time <= occurrence.start_time < end_time):
                    continue
                effective_event_end = (
                    query_end if occurrence.is_open else occurrence.end_time
                )
                if effective_event_end is None:
                    raise RuntimeError("closed alarm occurrence has no end_time")
                event_key = _event_key(point_id, occurrence.start_time)
                events.append(
                    AssessmentEvent(
                        start_time=occurrence.start_time,
                        end_time=effective_event_end,
                        message=_event_message(point_id, self.threshold_seconds),
                        data={
                            "point_id": point_id,
                            "history_tag": history_tag,
                            "alarm_start": occurrence.start_time,
                            "alarm_end": occurrence.end_time,
                            "duration_seconds": occurrence.duration_seconds,
                            "threshold_seconds": self.threshold_seconds,
                            "is_open": occurrence.is_open,
                            "event_key": event_key,
                        },
                    )
                )
        return events


def _validate_points(raw_points: object) -> tuple[dict[str, str], ...]:
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError("persistent_high_alarm parameters.points must not be empty")

    points: list[dict[str, str]] = []
    point_ids: set[str] = set()
    for index, raw_point in enumerate(raw_points):
        if not isinstance(raw_point, Mapping):
            raise ValueError(f"parameters.points[{index}] must be an object")
        point_id = _required_text(raw_point, "id")
        history_tag = _required_text(raw_point, "history_tag")
        if point_id in point_ids:
            raise ValueError(f"duplicate persistent_high_alarm point id: {point_id!r}")
        point_ids.add(point_id)
        points.append({"id": point_id, "history_tag": history_tag})
    return tuple(points)


def _required_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str | None = None,
) -> str:
    value = mapping.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be non-empty text")
    return value


def _required_value(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text containing 0 or 1")
    return value


def _validate_threshold(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("parameters.threshold_seconds must be a positive number")
    numeric = float(value)
    if not isfinite(numeric) or numeric <= 0:
        raise ValueError("parameters.threshold_seconds must be a positive number")
    return numeric


def _validate_range(start_time: datetime, end_time: datetime) -> None:
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        raise TypeError("start_time and end_time must be datetime values")
    if (start_time.tzinfo is None) != (end_time.tzinfo is None):
        raise ValueError(
            "start_time and end_time must both be timezone-naive or timezone-aware"
        )
    if start_time.tzinfo is not None:
        raise ValueError("persistent_high_alarm requires timezone-naive local time")
    if end_time <= start_time:
        raise ValueError("end_time must be after start_time")


def _event_key(point_id: str, alarm_start: datetime) -> str:
    return f"{DEFAULT_RULE_ID}:{point_id}:{alarm_start.isoformat()}"


def _event_message(point_id: str, threshold_seconds: float) -> str:
    if threshold_seconds.is_integer() and threshold_seconds % 60 == 0:
        threshold_text = f"{threshold_seconds / 60:g} 分钟"
    else:
        threshold_text = f"{threshold_seconds:g} 秒"
    return f"{point_id} 高报持续超过 {threshold_text}"

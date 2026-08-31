"""Assessment rule for independent pump-group flow compliance."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from math import isfinite
from typing import Any

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.data.client import DcsDataClient
from dcs_performance.data.history_context import (
    get_histories_with_previous_samples,
)

from dcs_performance.rules.pump_flow_compliance.detector import (
    LOW_FLOW,
    SWITCH_TIMEOUT,
    PumpFlowDetector,
    parse_digital_state,
)


DEFAULT_RULE_ID = "pump_flow_compliance"
DEFAULT_RULE_NAME = "泵组流量考核"


class Rule:
    """Read each configured pump group and return normalized findings."""

    id = DEFAULT_RULE_ID
    name = DEFAULT_RULE_NAME

    def __init__(
        self,
        data_client: DcsDataClient | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        if data_client is None:
            raise ValueError("pump_flow_compliance requires a data client")
        if not callable(getattr(data_client, "get_histories", None)):
            raise TypeError("data_client must provide get_histories()")
        if config is None or not isinstance(config, Mapping):
            raise ValueError("pump_flow_compliance config must be an object")

        self.data = data_client
        self.data_client = data_client
        self.config = dict(config)
        self.id = _required_text(self.config, "id", default=DEFAULT_RULE_ID)
        self.name = _required_text(self.config, "name", default=DEFAULT_RULE_NAME)
        if self.id != DEFAULT_RULE_ID:
            raise ValueError(
                f"pump_flow_compliance config id must be {DEFAULT_RULE_ID!r}"
            )

        parameters = self.config.get("parameters")
        if not isinstance(parameters, Mapping):
            raise ValueError("pump_flow_compliance parameters must be an object")
        self.points = _validate_points(parameters.get("points"))
        self.max_switch_duration_seconds = max(
            point["max_switch_duration_seconds"] for point in self.points
        )
        self.lookback = timedelta(seconds=self.max_switch_duration_seconds)
        self.detectors = tuple(
            PumpFlowDetector(
                point["id"],
                point["pump_a_tag"],
                point["pump_b_tag"],
                point["flow_tag"],
                point["running_value"],
                point["normal_min_flow"],
                point["switching_min_flow"],
                point["max_switch_duration_seconds"],
            )
            for point in self.points
        )

    def evaluate(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[AssessmentEvent]:
        """Evaluate the responsibility window using a bounded context tail."""

        _validate_range(start_time, end_time)
        analysis_start = start_time - self.lookback
        observation_end = end_time + self.lookback
        histories = get_histories_with_previous_samples(
            self.data,
            self._history_tags(),
            analysis_start,
            observation_end,
        )

        events: list[AssessmentEvent] = []
        for point, detector in zip(self.points, self.detectors):
            occurrences = detector.detect(
                histories,
                window_start=start_time,
                window_end=end_time,
                analysis_start=analysis_start,
                observation_end=observation_end,
            )
            for occurrence in occurrences:
                if not (start_time <= occurrence.start_time < end_time):
                    continue
                data = dict(occurrence.data)
                data["event_key"] = _event_key(
                    point["id"],
                    occurrence.event_type,
                    occurrence.start_time,
                )
                events.append(
                    AssessmentEvent(
                        start_time=occurrence.start_time,
                        end_time=occurrence.end_time,
                        message=_event_message(
                            point["id"],
                            occurrence.event_type,
                        ),
                        data=data,
                    )
                )

        return sorted(
            events,
            key=lambda event: (
                event.start_time,
                str(event.data.get("point_id", "")),
                str(event.data.get("event_type", "")),
            ),
        )

    def _history_tags(self) -> list[str]:
        tags: list[str] = []
        for point in self.points:
            for field in ("pump_a_tag", "pump_b_tag", "flow_tag"):
                tag = point[field]
                if tag not in tags:
                    tags.append(tag)
        return tags


def _validate_points(raw_points: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError("pump_flow_compliance parameters.points must not be empty")

    required_fields = (
        "id",
        "pump_a_tag",
        "pump_b_tag",
        "flow_tag",
        "running_value",
        "normal_min_flow",
        "switching_min_flow",
        "max_switch_duration_seconds",
    )
    points: list[dict[str, Any]] = []
    point_ids: set[str] = set()
    for index, raw_point in enumerate(raw_points):
        if not isinstance(raw_point, Mapping):
            raise ValueError(f"parameters.points[{index}] must be an object")
        missing = [field for field in required_fields if field not in raw_point]
        if missing:
            raise ValueError(
                f"parameters.points[{index}] missing required field(s): "
                + ", ".join(missing)
            )

        point_id = _required_text(raw_point, "id")
        if point_id in point_ids:
            raise ValueError(f"duplicate pump_flow_compliance point id: {point_id!r}")
        point_ids.add(point_id)

        pump_a_tag = _required_text(raw_point, "pump_a_tag")
        pump_b_tag = _required_text(raw_point, "pump_b_tag")
        flow_tag = _required_text(raw_point, "flow_tag")
        if pump_a_tag == pump_b_tag:
            raise ValueError(
                f"parameters.points[{index}] pump_a_tag and pump_b_tag must differ"
            )

        try:
            running_value = parse_digital_state(raw_point["running_value"])
        except ValueError as exc:
            raise ValueError(
                f"parameters.points[{index}].running_value must be 0 or 1"
            ) from exc

        normal_min_flow = _positive_number(
            raw_point["normal_min_flow"],
            f"parameters.points[{index}].normal_min_flow",
        )
        switching_min_flow = _positive_number(
            raw_point["switching_min_flow"],
            f"parameters.points[{index}].switching_min_flow",
        )
        max_switch_duration_seconds = _positive_number(
            raw_point["max_switch_duration_seconds"],
            f"parameters.points[{index}].max_switch_duration_seconds",
        )
        points.append(
            {
                "id": point_id,
                "pump_a_tag": pump_a_tag,
                "pump_b_tag": pump_b_tag,
                "flow_tag": flow_tag,
                "running_value": running_value,
                "normal_min_flow": normal_min_flow,
                "switching_min_flow": switching_min_flow,
                "max_switch_duration_seconds": max_switch_duration_seconds,
            }
        )
    return tuple(points)


def _required_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str | None = None,
) -> str:
    value = mapping.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be non-empty text")
    return value


def _positive_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a positive finite number")
    numeric = float(value)
    if not isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{field_name} must be a positive finite number")
    return numeric


def _validate_range(start_time: datetime, end_time: datetime) -> None:
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        raise TypeError("start_time and end_time must be datetime values")
    if (start_time.tzinfo is None) != (end_time.tzinfo is None):
        raise ValueError(
            "start_time and end_time must both be timezone-naive or timezone-aware"
        )
    if start_time.tzinfo is not None:
        raise ValueError("pump_flow_compliance requires timezone-naive local time")
    if end_time <= start_time:
        raise ValueError("end_time must be after start_time")


def _event_key(point_id: str, event_type: str, start_time: datetime) -> str:
    return f"pump_flow_compliance:{point_id}:{event_type}:{start_time.isoformat()}"


def _event_message(point_id: str, event_type: str) -> str:
    if event_type == LOW_FLOW:
        return f"{point_id} 流量低于当前工况要求"
    if event_type == SWITCH_TIMEOUT:
        return f"{point_id} 切泵持续时间超过允许上限"
    return f"{point_id} pump flow compliance event"

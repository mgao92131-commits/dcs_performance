"""Assessment rule for continuous analog upper/lower limit violations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.data.client import DcsDataClient
from dcs_performance.data.history_context import (
    get_histories_with_previous_samples,
    get_history_with_previous_sample,
)
from dcs_performance.engine.loader import RuleLoadError

from dcs_performance.rules.analog_limit_exceedance.config import (
    DEFAULT_RULE_ID,
    DEFAULT_RULE_NAME,
    AnalogLimitExceedanceConfig,
    LimitSideConfig,
    PointConfig,
    parse_config,
)
from dcs_performance.rules.analog_limit_exceedance.detector import (
    AnalogLimitExceedanceDetector,
    LimitEventType,
)
from dcs_performance.rules.analog_limit_exceedance.smoothing import (
    smooth_history_samples,
)


CONFIRMATION_MARGIN = timedelta(seconds=1)


class Rule:
    """Read configured analog histories and emit qualified assessment events."""

    id = DEFAULT_RULE_ID
    name = DEFAULT_RULE_NAME

    def __init__(
        self,
        data_client: DcsDataClient | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        if data_client is None:
            raise ValueError(f"{DEFAULT_RULE_ID} requires a data client")
        if not callable(getattr(data_client, "get_history", None)) and not callable(
            getattr(data_client, "get_histories", None)
        ):
            raise TypeError(
                "data_client must provide get_history() or get_histories()"
            )
        if config is None or not isinstance(config, Mapping):
            raise RuleLoadError(f"{DEFAULT_RULE_ID} config must be an object")

        try:
            parsed = parse_config(config)
        except Exception as exc:
            if isinstance(exc, RuleLoadError):
                raise
            raise RuleLoadError(
                f"could not validate {DEFAULT_RULE_ID} configuration: {exc}"
            ) from exc

        self.data = data_client
        self.data_client = data_client
        self.config = dict(config)
        self.typed_config: AnalogLimitExceedanceConfig = parsed
        self.points = parsed.points
        self.id = parsed.id
        self.name = parsed.name
        self.max_confirmation_tail_seconds = _max_confirmation_tail_seconds(
            self.points
        )
        self.confirmation_tail = timedelta(
            seconds=self.max_confirmation_tail_seconds
        )
        self.detector = AnalogLimitExceedanceDetector()

    def evaluate(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[AssessmentEvent]:
        """Evaluate one responsibility window with a confirmation tail."""

        _validate_range(start_time, end_time)
        enabled_points = tuple(point for point in self.points if point.enabled)
        if not enabled_points:
            return []

        query_end = (
            end_time
            + timedelta(seconds=self.max_confirmation_tail_seconds)
            + CONFIRMATION_MARGIN
        )
        analysis_start = start_time - timedelta(
            seconds=_max_smoothing_window_seconds(enabled_points)
        )
        histories = self._get_histories(
            enabled_points,
            analysis_start,
            query_end,
        )

        events: list[AssessmentEvent] = []
        for point in enabled_points:
            samples = smooth_history_samples(
                histories.get(point.history_tag, []),
                point.smoothing,
            )
            occurrences = self.detector.detect(
                samples,
                point,
                start_time=start_time,
                observation_end=query_end,
            )
            for occurrence in occurrences:
                # Ownership is determined by the observed violation start,
                # never by the eventual recovery time.
                if not (start_time <= occurrence.start_time < end_time):
                    continue

                event_end = occurrence.end_time or query_end
                if event_end <= occurrence.start_time:
                    # The detector normally prevents this.  Keep the Rule
                    # boundary safe if a custom detector is supplied later.
                    continue
                events.append(
                    self._build_event(
                        point,
                        occurrence.event_type,
                        occurrence.start_time,
                        event_end,
                        occurrence.end_time,
                        occurrence.duration_seconds,
                        occurrence.limit,
                        occurrence.extreme_value,
                        occurrence.extreme_time,
                        occurrence.is_open,
                    )
                )

        events.sort(
            key=lambda event: (
                event.start_time,
                str(event.data.get("point_id", "")),
                str(event.data.get("event_type", "")),
            )
        )
        return events

    def _get_histories(
        self,
        points: tuple[PointConfig, ...],
        start_time: datetime,
        end_time: datetime,
    ) -> Mapping[str, list[Any]]:
        """Read all enabled TAGs through the shared history-context helper."""

        tags = [point.history_tag for point in points]
        if callable(getattr(self.data_client, "get_histories", None)):
            result = get_histories_with_previous_samples(
                self.data_client,
                tags,
                start_time,
                end_time,
            )
        else:
            result = {
                point.history_tag: get_history_with_previous_sample(
                    self.data_client,
                    point.history_tag,
                    start_time,
                    end_time,
                )
                for point in points
            }
        if not isinstance(result, Mapping):
            raise TypeError("history context must return a tag-to-history mapping")
        return result

    @staticmethod
    def _build_event(
        point: PointConfig,
        event_type: str,
        event_start: datetime,
        event_end: datetime,
        violation_end: datetime | None,
        duration_seconds: float,
        limit: float,
        extreme_value: float,
        extreme_time: datetime,
        is_open: bool,
    ) -> AssessmentEvent:
        side = _side_for_event(point, event_type)
        return AssessmentEvent(
            start_time=event_start,
            end_time=event_end,
            message=_event_message(point.id, event_type, side.min_duration_seconds),
            data={
                "point_id": point.id,
                "history_tag": point.history_tag,
                "event_type": event_type,
                "limit": limit,
                "violation_start": event_start,
                "violation_end": violation_end,
                "duration_seconds": duration_seconds,
                "min_duration_seconds": side.min_duration_seconds,
                "merge_gap_seconds": side.merge_gap_seconds,
                "extreme_value": extreme_value,
                "extreme_time": extreme_time,
                "is_open": is_open,
                "smoothing": {
                    "enabled": point.smoothing.enabled,
                    "method": point.smoothing.method,
                    "window_seconds": point.smoothing.window_seconds,
                    "min_samples": point.smoothing.min_samples,
                },
                "event_key": _event_key(point.id, event_type, event_start),
            },
        )


def _side_for_event(point: PointConfig, event_type: str) -> LimitSideConfig:
    if event_type == LimitEventType.LOW.value:
        return point.low
    if event_type == LimitEventType.HIGH.value:
        return point.high
    raise ValueError(f"unsupported analog limit event_type: {event_type!r}")


def _max_confirmation_tail_seconds(points: tuple[PointConfig, ...]) -> float:
    tails = [
        side.min_duration_seconds + side.merge_gap_seconds
        for point in points
        if point.enabled
        for side in (point.low, point.high)
        if side.enabled
    ]
    return max(tails, default=0.0)


def _max_smoothing_window_seconds(points: tuple[PointConfig, ...]) -> float:
    return max(
        (
            point.smoothing.window_seconds
            for point in points
            if point.enabled and point.smoothing.enabled
        ),
        default=0.0,
    )


def _event_key(point_id: str, event_type: str, event_start: datetime) -> str:
    return f"{DEFAULT_RULE_ID}:{point_id}:{event_type}:{event_start.isoformat()}"


def _event_message(
    point_id: str,
    event_type: str,
    min_duration_seconds: float,
) -> str:
    if min_duration_seconds.is_integer() and min_duration_seconds % 60 == 0:
        duration_text = f"{min_duration_seconds / 60:g} 分钟"
    else:
        duration_text = f"{min_duration_seconds:g} 秒"
    side_text = "高限" if event_type == LimitEventType.HIGH.value else "低限"
    return f"{point_id} {side_text}超限持续超过 {duration_text}"


def _validate_range(start_time: datetime, end_time: datetime) -> None:
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        raise TypeError("start_time and end_time must be datetime values")
    if (start_time.tzinfo is None) != (end_time.tzinfo is None):
        raise ValueError(
            "start_time and end_time must both be timezone-naive or timezone-aware"
        )
    if end_time <= start_time:
        raise ValueError("end_time must be after start_time")

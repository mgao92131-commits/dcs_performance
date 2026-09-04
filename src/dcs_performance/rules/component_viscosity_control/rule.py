"""Rule implementation for the PI-2311001 viscosity-proxy trend."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime, timedelta
from typing import Any

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.core.points import select_points
from dcs_performance.data.client import DcsDataClient
from dcs_performance.data.models import HistorySample
from dcs_performance.engine.loader import RuleLoadError
from dcs_performance.rules.analog_limit_exceedance.config import (
    LimitSideConfig as GenericLimitSideConfig,
    PointConfig as GenericPointConfig,
    SmoothingConfig as GenericSmoothingConfig,
)
from dcs_performance.rules.analog_limit_exceedance.detector import (
    AnalogLimitExceedanceDetector,
    LimitEventType,
)

from .config import (
    DEFAULT_RULE_ID,
    DEFAULT_RULE_NAME,
    ComponentViscosityControlConfig,
    PointConfig,
    parse_config,
)
from .detector import (
    DisturbanceWindow,
    MetricPoint,
    calculate_metric,
    detect_disturbance_windows,
    exclude_disturbances,
    split_contiguous_segments,
)


CONFIRMATION_MARGIN = timedelta(seconds=1)


class Rule:
    """Read PI histories, build the viscosity trend, and emit events."""

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
            raise RuleLoadError(
                f"could not validate {DEFAULT_RULE_ID} configuration: {exc}"
            ) from exc

        self.data = data_client
        self.data_client = data_client
        self.config = dict(config)
        self.typed_config: ComponentViscosityControlConfig = parsed
        self.points = parsed.points
        self.id = parsed.id
        self.name = parsed.name
        self.detector = AnalogLimitExceedanceDetector()

    def evaluate(
        self,
        start_time: datetime,
        end_time: datetime,
        *,
        point_ids: Collection[str] | None = None,
    ) -> list[AssessmentEvent]:
        """Evaluate the requested responsibility range."""

        _validate_range(start_time, end_time)
        events: list[AssessmentEvent] = []
        selected_points = select_points(
            self.points,
            point_ids,
            rule_id=self.id,
        )
        for point in selected_points:

            query_start, query_end = build_history_query_range(point, start_time, end_time)
            raw_samples = self._get_history(point.history_tag, query_start, query_end)
            metric = calculate_metric(
                raw_samples,
                bucket_seconds=point.aggregation.bucket_seconds,
                bucket_min_samples=point.aggregation.min_samples,
                smoothing_enabled=point.smoothing.enabled,
                window_seconds=point.smoothing.window_seconds,
                smoothing_min_samples=point.smoothing.min_samples,
            )
            exclusion_windows = detect_disturbance_windows(
                metric,
                point.exclusion,
                bucket_seconds=point.aggregation.bucket_seconds,
            )
            clean_metric = exclude_disturbances(
                metric,
                exclusion_windows,
                bucket_seconds=point.aggregation.bucket_seconds,
            )

            generic_point = _generic_point_config(point)
            for segment in split_contiguous_segments(
                clean_metric,
                bucket_seconds=point.aggregation.bucket_seconds,
            ):
                if not segment:
                    continue
                observations = _metric_history_samples(segment)
                segment_end = segment[-1].timestamp + timedelta(
                    seconds=point.aggregation.bucket_seconds
                )
                if segment_end <= start_time:
                    continue
                occurrences = self.detector.detect(
                    observations,
                    generic_point,
                    start_time=start_time,
                    observation_end=min(query_end, segment_end),
                    allow_initial_abnormal=_segment_follows_exclusion(
                        segment,
                        clean_metric,
                        exclusion_windows,
                    ),
                )
                for occurrence in occurrences:
                    if not (start_time <= occurrence.start_time < end_time):
                        continue
                    event_end = occurrence.end_time or min(query_end, segment_end)
                    if event_end <= occurrence.start_time:
                        continue
                    events.append(
                        _build_event(
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
                            exclusion_windows,
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

    def _get_history(
        self,
        history_tag: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[HistorySample]:
        if callable(getattr(self.data_client, "get_history", None)):
            result = self.data_client.get_history(history_tag, start_time, end_time)
        else:
            histories = self.data_client.get_histories(
                [history_tag],
                start_time,
                end_time,
            )
            if not isinstance(histories, Mapping):
                raise TypeError("get_histories() must return a tag-to-history mapping")
            result = histories.get(history_tag, [])
        if not isinstance(result, list):
            result = list(result)
        return result


def build_history_query_range(
    point: PointConfig,
    start_time: datetime,
    end_time: datetime,
) -> tuple[datetime, datetime]:
    bucket = point.aggregation.bucket_seconds
    trend_window = point.smoothing.window_seconds if point.smoothing.enabled else bucket
    preheat = trend_window + bucket
    if point.exclusion.enabled:
        preheat += point.exclusion.remove_after_start_seconds
    left_padding = timedelta(seconds=preheat)
    right_padding = timedelta(
        seconds=(
            point.assessment.min_duration_seconds
            + point.assessment.merge_gap_seconds
            + bucket
        )
    ) + CONFIRMATION_MARGIN
    return start_time - left_padding, end_time + right_padding


# Compatibility for any local experiments that used the former private name.
_query_range = build_history_query_range


def _generic_point_config(point: PointConfig) -> GenericPointConfig:
    assessment = point.assessment
    return GenericPointConfig(
        id=point.id,
        history_tag=point.history_tag,
        enabled=True,
        smoothing=GenericSmoothingConfig(enabled=False),
        low=GenericLimitSideConfig(
            enabled=True,
            limit=assessment.low_limit,
            min_duration_seconds=assessment.min_duration_seconds,
            merge_gap_seconds=assessment.merge_gap_seconds,
        ),
        high=GenericLimitSideConfig(
            enabled=True,
            limit=assessment.high_limit,
            min_duration_seconds=assessment.min_duration_seconds,
            merge_gap_seconds=assessment.merge_gap_seconds,
        ),
    )


def _metric_history_samples(points: list[MetricPoint]) -> list[HistorySample]:
    return [
        HistorySample(
            timestamp=point.timestamp,
            value=f"{point.value:.12g}",
            data_type="Analog",
            delta_v_status="Good",
            archive_status="HistoryDataIsValid",
            sequence_no=1,
            is_history_hole=False,
            is_cr_hole=False,
            is_manually_deleted=False,
            is_manually_inserted=False,
        )
        for point in points
    ]


def _segment_follows_exclusion(
    segment: list[MetricPoint],
    clean_metric: list[MetricPoint],
    exclusion_windows: list[DisturbanceWindow],
) -> bool:
    """Identify the clean segment immediately following a removed window.

    A normal data gap must retain the generic detector's UNKNOWN-at-first-
    observation behavior.  Only the first clean metric segment after an
    intentional exclusion window gets the explicit re-initialisation
    permission.  The first metric point may be delayed by smoothing or a
    subsequent history gap, so this is intentionally not limited to one
    bucket after ``remove_end``.
    """

    if not segment or not clean_metric or not exclusion_windows:
        return False
    first_timestamp = segment[0].timestamp
    for window in exclusion_windows:
        if window.remove_end > first_timestamp:
            continue
        if not any(
            window.remove_end <= point.timestamp < first_timestamp
            for point in clean_metric
        ):
            return True
    return False


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
    exclusion_windows: list[DisturbanceWindow],
) -> AssessmentEvent:
    is_low = event_type == LimitEventType.LOW.value
    viscosity_event_type = "viscosity_low" if is_low else "viscosity_high"
    direction_text = "偏低" if is_low else "偏高"
    assessment = point.assessment
    return AssessmentEvent(
        start_time=event_start,
        end_time=event_end,
        message=(
            f"{point.id} 粘度趋势{direction_text}"
            f"持续超过{assessment.min_duration_seconds / 60:g}分钟"
        ),
        data={
            "point_id": point.id,
            "history_tag": point.history_tag,
            "event_type": viscosity_event_type,
            "score_key": viscosity_event_type,
            "limit": limit,
            "target": assessment.target,
            "low_limit": assessment.low_limit,
            "high_limit": assessment.high_limit,
            "violation_start": event_start,
            "violation_end": violation_end,
            "duration_seconds": duration_seconds,
            "min_duration_seconds": assessment.min_duration_seconds,
            "merge_gap_seconds": assessment.merge_gap_seconds,
            "extreme_value": extreme_value,
            "extreme_time": extreme_time,
            "is_open": is_open,
            "aggregation": {
                "method": point.aggregation.method,
                "bucket_seconds": point.aggregation.bucket_seconds,
                "min_samples": point.aggregation.min_samples,
            },
            "smoothing": {
                "enabled": point.smoothing.enabled,
                "method": point.smoothing.method,
                "window_seconds": point.smoothing.window_seconds,
                "min_samples": point.smoothing.min_samples,
            },
            "exclusion": {
                "enabled": point.exclusion.enabled,
                "method": point.exclusion.method,
                "window_seconds": point.exclusion.window_seconds,
                "range_threshold": point.exclusion.range_threshold,
                "merge_gap_seconds": point.exclusion.merge_gap_seconds,
                "remove_after_start_seconds": point.exclusion.remove_after_start_seconds,
                "windows": [
                    {
                        "core_start": window.core_start,
                        "core_end": window.core_end,
                        "remove_start": window.remove_start,
                        "remove_end": window.remove_end,
                    }
                    for window in exclusion_windows
                ],
            },
            "event_key": (
                f"{DEFAULT_RULE_ID}:{point.id}:{viscosity_event_type}:"
                f"{event_start.isoformat()}"
            ),
        },
    )


def _validate_range(start_time: datetime, end_time: datetime) -> None:
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        raise TypeError("start_time and end_time must be datetime values")
    if (start_time.tzinfo is None) != (end_time.tzinfo is None):
        raise ValueError(
            "start_time and end_time must both be timezone-naive or timezone-aware"
        )
    if end_time <= start_time:
        raise ValueError("end_time must be after start_time")

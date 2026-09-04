"""Assessment rule for independent analog trend stability analyses."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from dcs_performance.core.event import AssessmentEvent
from dcs_performance.core.points import select_points
from dcs_performance.data.client import DcsDataClient
from dcs_performance.data.models import HistorySample
from dcs_performance.engine.loader import RuleLoadError

from dcs_performance.rules.analog_trend_stability.config import (
    AnalogTrendStabilityConfig,
    PointConfig,
    parse_config,
)
from dcs_performance.rules.analog_trend_stability.detector import (
    AnalogTrendStabilityDetector,
)
from dcs_performance.rules.analog_trend_stability.trend import (
    DriftPoint,
    TrendPoint,
    calculate_drift,
    calculate_trend,
    split_numeric_segments,
)


DEFAULT_RULE_ID = "analog_trend_stability"
DEFAULT_RULE_NAME = "连续量趋势稳定性考核"


@dataclass(frozen=True)
class QueryGroup:
    """Points that can share one batch history request."""

    points: tuple[PointConfig, ...]
    tags: tuple[str, ...]
    query_start: datetime
    query_end: datetime
    left_padding: timedelta
    right_padding: timedelta

    @property
    def start_time(self) -> datetime:
        """Alias for the start of the planned history query."""

        return self.query_start

    @property
    def end_time(self) -> datetime:
        """Alias for the end of the planned history query."""

        return self.query_end


class QueryPlanner:
    """Group points by their independently calculated history padding."""

    @staticmethod
    def plan(
        points: Iterable[PointConfig],
        start_time: datetime,
        end_time: datetime,
    ) -> tuple[QueryGroup, ...]:
        _validate_range(start_time, end_time)
        groups: dict[tuple[timedelta, timedelta], list[PointConfig]] = {}
        for point in points:
            if not isinstance(point, PointConfig):
                raise TypeError("query planner input must contain PointConfig values")
            if not point.enabled:
                continue
            left_padding, right_padding = _point_padding(point)
            groups.setdefault((left_padding, right_padding), []).append(point)

        planned: list[QueryGroup] = []
        for (left_padding, right_padding), group_points in groups.items():
            tags = tuple(dict.fromkeys(point.history_tag for point in group_points))
            planned.append(
                QueryGroup(
                    points=tuple(group_points),
                    tags=tags,
                    query_start=start_time - left_padding,
                    query_end=end_time + right_padding,
                    left_padding=left_padding,
                    right_padding=right_padding,
                )
            )
        return tuple(planned)

    # A more explicit spelling for callers that use a planning verb.
    build = plan


# Compatibility spelling for callers that model each request as a plan.
QueryPlan = QueryGroup


class Rule:
    """Read analog histories, calculate point-local metrics, and emit events."""

    id = DEFAULT_RULE_ID
    name = DEFAULT_RULE_NAME

    def __init__(
        self,
        data_client: DcsDataClient | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        if data_client is None:
            raise ValueError("analog_trend_stability requires a data client")
        if not callable(getattr(data_client, "get_histories", None)):
            raise TypeError("data_client must provide get_histories()")
        if config is None or not isinstance(config, Mapping):
            raise RuleLoadError("analog_trend_stability config must be an object")

        try:
            parsed = parse_config(config)
        except Exception as exc:
            if isinstance(exc, RuleLoadError):
                raise
            raise RuleLoadError(
                "could not validate analog_trend_stability configuration: "
                f"{exc}"
            ) from exc

        self.data = data_client
        self.data_client = data_client
        self.config = dict(config)
        self.typed_config: AnalogTrendStabilityConfig = parsed
        self.points = parsed.points
        self.id = parsed.id
        self.name = parsed.name
        self.query_planner = QueryPlanner()
        self.detector = AnalogTrendStabilityDetector()

    def evaluate(
        self,
        start_time: datetime,
        end_time: datetime,
        *,
        point_ids: Collection[str] | None = None,
    ) -> list[AssessmentEvent]:
        """Evaluate only the requested responsibility range.

        Query padding is calculated separately for every point, while points
        with identical padding share one ``get_histories`` request.  Findings
        may overlap the request boundary, but returned intervals are always
        clipped to ``[start_time, end_time]``.
        """

        _validate_range(start_time, end_time)
        selected_points = select_points(
            self.points,
            point_ids,
            rule_id=self.id,
        )
        events: list[AssessmentEvent] = []

        for group in QueryPlanner.plan(selected_points, start_time, end_time):
            raw_histories = self._get_histories(
                list(group.tags),
                group.query_start,
                group.query_end,
            )
            if not isinstance(raw_histories, Mapping):
                raise TypeError("get_histories() must return a tag-to-history mapping")

            for point in group.points:
                raw_samples = raw_histories.get(point.history_tag, [])
                segments = split_numeric_segments(
                    raw_samples,
                    point.quality.max_gap_seconds,
                )
                for segment_id, segment in enumerate(segments):
                    trend_points = calculate_trend(
                        segment,
                        point.trend,
                        segment_id=segment_id,
                    )
                    if not trend_points:
                        continue

                    if point.stability.enabled:
                        events.extend(
                            self._stability_events(
                                point,
                                trend_points,
                                start_time,
                                end_time,
                            )
                        )

                    if point.drift.enabled:
                        drift_points = calculate_drift(
                            trend_points,
                            point.drift.windows,
                        )
                        events.extend(
                            self._drift_events(
                                point,
                                drift_points,
                                start_time,
                                end_time,
                            )
                        )

        events.sort(
            key=lambda event: (
                event.start_time,
                str(event.data.get("point_id", "")),
                str(event.data.get("event_type", "")),
                str(event.data.get("direction", "")),
            )
        )
        return events

    def _get_histories(
        self,
        tags: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> Mapping[str, list[HistorySample]]:
        """Read one planned batch through the data-client protocol."""

        result = self.data_client.get_histories(tags, start_time, end_time)
        if not isinstance(result, Mapping):
            raise TypeError("get_histories() must return a tag-to-history mapping")
        return result

    def _stability_events(
        self,
        point: PointConfig,
        trend_points: Iterable[TrendPoint],
        start_time: datetime,
        end_time: datetime,
    ) -> list[AssessmentEvent]:
        # Re-detect after slicing the metric timeline.  A later shift may
        # upgrade the full occurrence, but it must not upgrade this shift's
        # responsibility slice.
        all_points = tuple(trend_points)
        sliced_points = _slice_metric_points(all_points, start_time, end_time)
        if not sliced_points:
            return []
        occurrences = self.detector.detect_stability(
            sliced_points,
            point.stability,
            observation_end=_metric_observation_end(all_points, end_time),
        )
        events: list[AssessmentEvent] = []
        for occurrence in occurrences:
            clipped = _clip_interval(
                occurrence.start_time,
                occurrence.end_time,
                start_time,
                end_time,
            )
            if clipped is None:
                continue
            event_start, event_end = clipped
            severity = occurrence.severity
            event_type = "stability_deviation"
            events.append(
                AssessmentEvent(
                    start_time=event_start,
                    end_time=event_end,
                    message=f"{point.id} 短周期波动异常",
                    data={
                        "point_id": point.id,
                        "history_tag": point.history_tag,
                        "event_type": event_type,
                        "severity": severity,
                        "score_key": f"{event_type}.{severity}",
                        "duration_seconds": (event_end - event_start).total_seconds(),
                        "max_abs_deviation": occurrence.max_abs_deviation,
                        "mean_abs_deviation": occurrence.mean_abs_deviation,
                        "trend_method": point.trend.method,
                        "trend_window_seconds": point.trend.window_seconds,
                        "event_key": _event_key(
                            point.id,
                            event_type,
                            None,
                            event_start,
                        ),
                    },
                )
            )
        return events

    def _drift_events(
        self,
        point: PointConfig,
        drift_points: Iterable[DriftPoint],
        start_time: datetime,
        end_time: datetime,
    ) -> list[AssessmentEvent]:
        # Drift evidence and severity are also responsibility-local.  The
        # drift values themselves were calculated with the full padded
        # history, but only metric points in this window may support scoring.
        all_points = tuple(drift_points)
        sliced_points = _slice_metric_points(all_points, start_time, end_time)
        if not sliced_points:
            return []
        occurrences = self.detector.detect_drift(
            sliced_points,
            point.drift,
            observation_end=_metric_observation_end(all_points, end_time),
        )
        events: list[AssessmentEvent] = []
        for occurrence in occurrences:
            clipped = _clip_interval(
                occurrence.start_time,
                occurrence.end_time,
                start_time,
                end_time,
            )
            if clipped is None:
                continue
            event_start, event_end = clipped
            event_type = "trend_drift"
            evidence = [
                {
                    "window_id": item.window_id,
                    "window_seconds": item.window_seconds,
                    "peak_change": item.peak_change,
                }
                for item in occurrence.evidence
            ]
            events.append(
                AssessmentEvent(
                    start_time=event_start,
                    end_time=event_end,
                    message=f"{point.id} 趋势漂移{_direction_text(occurrence.direction)}异常",
                    data={
                        "point_id": point.id,
                        "history_tag": point.history_tag,
                        "event_type": event_type,
                        "direction": occurrence.direction,
                        "severity": occurrence.severity,
                        "score_key": f"{event_type}.{occurrence.severity}",
                        "duration_seconds": (event_end - event_start).total_seconds(),
                        "evidence": evidence,
                        "trend_method": point.trend.method,
                        "trend_window_seconds": point.trend.window_seconds,
                        "event_key": _event_key(
                            point.id,
                            event_type,
                            occurrence.direction,
                            event_start,
                        ),
                    },
                )
            )
        return events


def _point_padding(point: PointConfig) -> tuple[timedelta, timedelta]:
    if point.trend.alignment == "centered":
        trend_left = point.trend.window_seconds / 2.0
        trend_right = point.trend.window_seconds / 2.0
    else:
        trend_left = point.trend.window_seconds
        trend_right = 0.0

    left_padding = trend_left + point.max_drift_window_seconds
    return timedelta(seconds=left_padding), timedelta(seconds=trend_right)


def _slice_metric_points(
    points: Iterable[TrendPoint] | Iterable[DriftPoint],
    start_time: datetime,
    end_time: datetime,
) -> list[TrendPoint] | list[DriftPoint]:
    """Return metric points belonging to one responsibility interval.

    Responsibility windows are half-open at the end boundary so a point at
    exactly 16:00 belongs to the following shift, not the preceding one.
    """

    return [
        point
        for point in points
        if start_time <= point.timestamp < end_time
    ]


def _metric_observation_end(
    points: Iterable[TrendPoint] | Iterable[DriftPoint],
    end_time: datetime,
) -> datetime:
    """Use the responsibility end when padded observations reach it."""

    latest = max((point.timestamp for point in points), default=end_time)
    return min(latest, end_time)


def _clip_interval(
    event_start: datetime,
    event_end: datetime,
    start_time: datetime,
    end_time: datetime,
) -> tuple[datetime, datetime] | None:
    clipped_start = max(event_start, start_time)
    clipped_end = min(event_end, end_time)
    if clipped_end <= clipped_start:
        return None
    return clipped_start, clipped_end


def _event_key(
    point_id: str,
    event_type: str,
    direction: str | None,
    start_time: datetime,
) -> str:
    direction_text = direction or "none"
    return (
        f"{DEFAULT_RULE_ID}:{point_id}:{event_type}:"
        f"{direction_text}:{start_time.isoformat()}"
    )


def _direction_text(direction: str) -> str:
    return "上升" if direction == "up" else "下降"


def _validate_range(start_time: datetime, end_time: datetime) -> None:
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        raise TypeError("start_time and end_time must be datetime values")
    if (start_time.tzinfo is None) != (end_time.tzinfo is None):
        raise ValueError(
            "start_time and end_time must both be timezone-naive or timezone-aware"
        )
    if end_time <= start_time:
        raise ValueError("end_time must be after start_time")

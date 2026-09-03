from datetime import datetime, timedelta

import pytest

from dcs_performance.data.history_quality import prepare_numeric_history
from dcs_performance.data.models import HistorySample


START = datetime(2026, 9, 1, 8, 0)


def sample(timestamp, value, **quality):
    return HistorySample(
        timestamp=timestamp,
        value=str(value),
        data_type="Analog",
        delta_v_status=quality.get("delta_v_status", "Good"),
        archive_status=quality.get("archive_status", "HistoryDataIsValid"),
        sequence_no=quality.get("sequence_no", 1),
        is_history_hole=quality.get("is_history_hole", False),
        is_cr_hole=quality.get("is_cr_hole", False),
        is_manually_deleted=quality.get("is_manually_deleted", False),
        is_manually_inserted=quality.get("is_manually_inserted", False),
    )


@pytest.mark.parametrize(
    "quality",
    [
        {"is_history_hole": True},
        {"is_cr_hole": True},
        {"is_manually_deleted": True},
        {"is_manually_inserted": True},
        {"delta_v_status": "Bad"},
        {"archive_status": "HistoryDataIsNotValid"},
    ],
)
def test_quality_fields_cut_numeric_segments_before_parsing(quality):
    prepared = prepare_numeric_history(
        [
            sample(START, 1),
            sample(START + timedelta(minutes=1), "not-a-number", **quality),
            sample(START + timedelta(minutes=2), 3),
        ],
        parse_value=float,
        max_gap_seconds=60,
    )

    assert [[item.value for item in segment] for segment in prepared.segments] == [
        [1.0],
        [3.0],
    ]
    assert prepared.break_times == (START + timedelta(minutes=1),)


def test_unflagged_malformed_value_is_a_quality_boundary_too():
    prepared = prepare_numeric_history(
        [sample(START, 1), sample(START + timedelta(minutes=1), "BAD")],
        parse_value=float,
    )

    assert [[item.value for item in segment] for segment in prepared.segments] == [[1.0]]
    assert prepared.break_times == (START + timedelta(minutes=1),)

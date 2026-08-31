import pytest

from dcs_performance.data.errors import DcsProtocolError
from dcs_performance.data.parsers import HISTORY_COLUMNS, parse_history_csv

from .support import make_csv


def test_history_parser_preserves_all_fields_and_raw_value():
    body = make_csv(
        HISTORY_COLUMNS,
        [
            [
                "2026-08-30T08:00:00.1234567",
                "12,5 \"raw\"",
                "Float",
                "Good",
                "HistoryDataIsValid",
                "42",
                "false",
                "true",
                "false",
                "true",
            ]
        ],
    )

    samples = parse_history_csv(body)

    assert len(samples) == 1
    sample = samples[0]
    assert sample.timestamp.microsecond == 123456
    assert sample.value == '12,5 "raw"'
    assert sample.data_type == "Float"
    assert sample.delta_v_status == "Good"
    assert sample.archive_status == "HistoryDataIsValid"
    assert sample.sequence_no == 42
    assert sample.is_history_hole is False
    assert sample.is_cr_hole is True
    assert sample.is_manually_deleted is False
    assert sample.is_manually_inserted is True


@pytest.mark.parametrize(
    "columns",
    [
        HISTORY_COLUMNS[:-1],
        HISTORY_COLUMNS + ("Unexpected",),
        tuple(reversed(HISTORY_COLUMNS)),
    ],
)
def test_history_parser_rejects_missing_extra_or_mismatched_schema(columns):
    body = make_csv(columns, [[]])
    with pytest.raises(DcsProtocolError, match="schema"):
        parse_history_csv(body)


def test_history_parser_rejects_non_protocol_boolean():
    row = [
        "2026-08-30T08:00:00",
        "12.5",
        "Float",
        "Good",
        "HistoryDataIsValid",
        "1",
        "False",
        "false",
        "false",
        "false",
    ]
    with pytest.raises(DcsProtocolError, match="true or false"):
        parse_history_csv(make_csv(HISTORY_COLUMNS, [row]))


def test_history_parser_handles_chinese_text_and_quoted_newline():
    row = [
        "2026-08-30T08:00:00",
        "阀门，\"原始\"\n备注",
        "String",
        "Good",
        "HistoryDataIsValid",
        "1",
        "false",
        "false",
        "false",
        "false",
    ]
    assert parse_history_csv(make_csv(HISTORY_COLUMNS, [row]))[0].value == row[1]


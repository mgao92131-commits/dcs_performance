import io

import pytest

from dcs_performance.data.errors import DcsProtocolError
from dcs_performance.data.parsers import (
    EVENT_COLUMNS,
    parse_event_csv,
    parse_event_csv_stream,
)

from .support import make_csv


def _event_row(timestamp, frac_sec, ordinal, archived):
    return [
        timestamp,
        frac_sec,
        ordinal,
        "Alarm",
        "High",
        "Process",
        "Area 1",
        "Node 1",
        "Unit 1",
        "Module 1",
        "模块一",
        "PV",
        "Active",
        "HI",
        "desc, with comma",
        'said "TEST"\nand more',
        archived,
    ]


def test_event_parser_preserves_seventeen_columns_and_duplicate_timestamps():
    body = make_csv(
        EVENT_COLUMNS,
        [
            _event_row("2026-08-30T08:00:00.123", 123, 1, "true"),
            _event_row("2026-08-30T08:00:00.123", 124, 2, "false"),
            _event_row("2026-08-30T08:00:01", 0, 3, ""),
        ],
    )

    events = parse_event_csv(body)

    assert len(events) == 3
    assert [(event.frac_sec, event.ord) for event in events] == [
        (123, 1),
        (124, 2),
        (0, 3),
    ]
    assert events[0].is_archived is True
    assert events[1].is_archived is False
    assert events[2].is_archived is None
    assert events[0].desc1 == "desc, with comma"
    assert events[0].desc2 == 'said "TEST"\nand more'
    assert events[0].module_description == "模块一"


@pytest.mark.parametrize(
    "columns",
    [EVENT_COLUMNS[:-1], EVENT_COLUMNS + ("Unexpected",), tuple(reversed(EVENT_COLUMNS))],
)
def test_event_parser_rejects_schema_mismatch(columns):
    with pytest.raises(DcsProtocolError, match="schema"):
        parse_event_csv(make_csv(columns, [[]]))


def test_event_parser_rejects_invalid_archived_value():
    row = _event_row("2026-08-30T08:00:00", 1, 1, "")
    row[-1] = "NULL"
    with pytest.raises(DcsProtocolError, match="true or false"):
        parse_event_csv(make_csv(EVENT_COLUMNS, [row]))


def test_event_parser_accepts_a_text_stream():
    body = make_csv(EVENT_COLUMNS, [])

    assert parse_event_csv_stream(io.StringIO(body.decode("utf-8"))) == []

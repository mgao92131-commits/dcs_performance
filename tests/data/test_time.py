from datetime import datetime, timezone, timedelta

import pytest

from dcs_performance.data.errors import DcsArgumentError
from dcs_performance.data.parsers import (
    ensure_naive_datetime,
    format_timestamp,
    parse_timestamp,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-08-30T08:00:00", datetime(2026, 8, 30, 8, 0)),
        ("2026-08-30T08:00:00.1", datetime(2026, 8, 30, 8, 0, 0, 100000)),
        ("2026-08-30T08:00:00.123456", datetime(2026, 8, 30, 8, 0, 0, 123456)),
    ],
)
def test_parse_timestamp_accepts_zero_to_six_fraction_digits(text, expected):
    assert parse_timestamp(text) == expected


def test_parse_timestamp_accepts_seven_digits_with_explicit_microsecond_truncation():
    assert parse_timestamp("2026-08-30T08:00:00.1234567") == datetime(
        2026, 8, 30, 8, 0, 0, 123456
    )


def test_format_timestamp_never_adds_timezone_suffix():
    value = datetime(2026, 8, 30, 8, 0, 0, 123000)
    assert format_timestamp(value) == "2026-08-30T08:00:00.123000"
    assert "Z" not in format_timestamp(value)
    assert "+08:00" not in format_timestamp(value)


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 30, 8, 0, tzinfo=timezone(timedelta(hours=8))),
    ],
)
def test_aware_datetime_is_rejected(value):
    with pytest.raises(DcsArgumentError):
        ensure_naive_datetime(value, field_name="start_time")


def test_timestamp_parser_rejects_timezone_text():
    with pytest.raises(Exception):
        parse_timestamp("2026-08-30T08:00:00+08:00")


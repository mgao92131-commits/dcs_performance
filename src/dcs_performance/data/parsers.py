"""Strict time and CSV parsers for the dcs-service V1 protocol."""

import csv
import io
import re
from datetime import datetime
from typing import Any, Iterable

from .errors import DcsArgumentError, DcsProtocolError
from .models import DcsEvent, HistorySample

HISTORY_COLUMNS = (
    "Timestamp",
    "Value",
    "DataType",
    "DeltaVStatus",
    "ArchiveStatus",
    "SequenceNo",
    "IsHistoryHole",
    "IsCRHole",
    "IsManuallyDeleted",
    "IsManuallyInserted",
)

EVENT_COLUMNS = (
    "DateTime",
    "FracSec",
    "Ord",
    "EventType",
    "EventSubType",
    "Category",
    "Area",
    "Node",
    "Unit",
    "Module",
    "ModuleDescription",
    "Attribute",
    "State",
    "EventLevel",
    "Desc1",
    "Desc2",
    "IsArchived",
)

_TIMESTAMP_RE = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d{1,7}))?$"
)


def ensure_naive_datetime(value: datetime, *, field_name: str) -> datetime:
    """Require a source-local naive ``datetime`` and never convert it."""

    if not isinstance(value, datetime):
        raise DcsArgumentError(
            f"{field_name} must be a datetime",
            code="invalid_request",
        )
    if value.tzinfo is not None:
        raise DcsArgumentError(
            f"{field_name} must be a naive source-local datetime",
            code="invalid_request",
        )
    return value


def parse_timestamp(value: str) -> datetime:
    """Parse a naive V1 timestamp with zero through seven fraction digits.

    Python stores microseconds (six digits).  For an input with seven digits,
    the seventh digit is deliberately truncated after the first six digits.
    Event cursor requests never reconstruct a cursor from this value; they use
    the exact ``X-DCS-Next-DateTime`` header text preserved separately.
    """

    if not isinstance(value, str):
        raise DcsProtocolError("timestamp must be text", code="invalid_timestamp")
    match = _TIMESTAMP_RE.fullmatch(value)
    if match is None:
        raise DcsProtocolError(
            f"invalid source-local timestamp: {value!r}",
            code="invalid_timestamp",
        )

    try:
        parsed = datetime.strptime(match.group("base"), "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise DcsProtocolError(
            f"invalid source-local timestamp: {value!r}",
            code="invalid_timestamp",
        ) from exc

    fraction = match.group("fraction")
    if fraction:
        microseconds = int(fraction[:6].ljust(6, "0"))
        parsed = parsed.replace(microsecond=microseconds)
    return parsed


def format_timestamp(value: datetime) -> str:
    """Format a naive datetime without ``Z`` or numeric timezone offsets."""

    ensure_naive_datetime(value, field_name="timestamp")
    return value.isoformat(timespec="auto")


def parse_bool(value: str, *, field_name: str = "boolean") -> bool:
    """Parse the protocol's strict lowercase boolean representation."""

    if value == "true":
        return True
    if value == "false":
        return False
    raise DcsProtocolError(
        f"{field_name} must be exactly true or false",
        code="invalid_boolean",
    )


def parse_history_csv(body: bytes | str) -> list[HistorySample]:
    """Parse and validate the complete fixed History CSV schema."""

    rows = _read_csv(body, HISTORY_COLUMNS, "History")
    samples: list[HistorySample] = []
    for row_number, row in rows:
        samples.append(
            HistorySample(
                timestamp=parse_timestamp(row["Timestamp"]),
                value=row["Value"],
                data_type=row["DataType"],
                delta_v_status=row["DeltaVStatus"],
                archive_status=row["ArchiveStatus"],
                sequence_no=_parse_int(row["SequenceNo"], "SequenceNo", row_number),
                is_history_hole=_parse_bool(
                    row["IsHistoryHole"], "IsHistoryHole", row_number
                ),
                is_cr_hole=_parse_bool(row["IsCRHole"], "IsCRHole", row_number),
                is_manually_deleted=_parse_bool(
                    row["IsManuallyDeleted"], "IsManuallyDeleted", row_number
                ),
                is_manually_inserted=_parse_bool(
                    row["IsManuallyInserted"], "IsManuallyInserted", row_number
                ),
            )
        )
    return samples


def parse_event_csv(body: bytes | str) -> list[DcsEvent]:
    """Parse and validate the complete fixed Event CSV schema."""

    rows = _read_csv(body, EVENT_COLUMNS, "Event")
    events: list[DcsEvent] = []
    for row_number, row in rows:
        archived = row["IsArchived"]
        events.append(
            DcsEvent(
                timestamp=parse_timestamp(row["DateTime"]),
                frac_sec=_parse_int(row["FracSec"], "FracSec", row_number),
                ord=_parse_int(row["Ord"], "Ord", row_number),
                event_type=row["EventType"],
                event_sub_type=row["EventSubType"],
                category=row["Category"],
                area=row["Area"],
                node=row["Node"],
                unit=row["Unit"],
                module=row["Module"],
                module_description=row["ModuleDescription"],
                attribute=row["Attribute"],
                state=row["State"],
                event_level=row["EventLevel"],
                desc1=row["Desc1"],
                desc2=row["Desc2"],
                is_archived=(
                    None
                    if archived == ""
                    else _parse_bool(archived, "IsArchived", row_number)
                ),
            )
        )
    return events


def _read_csv(
    body: bytes | str,
    expected_columns: Iterable[str],
    kind: str,
) -> list[tuple[int, dict[str, str]]]:
    if isinstance(body, bytes):
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DcsProtocolError(
                f"{kind} CSV is not valid UTF-8",
                code="csv_parse_error",
            ) from exc
    elif isinstance(body, str):
        text = body
    else:
        raise DcsProtocolError(
            f"{kind} CSV body must be bytes or text",
            code="csv_parse_error",
        )

    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        fieldnames = reader.fieldnames
        expected = list(expected_columns)
        if fieldnames != expected:
            raise DcsProtocolError(
                f"{kind} CSV header does not match the V1 schema",
                code="csv_schema_mismatch",
            )

        rows: list[tuple[int, dict[str, str]]] = []
        for row_number, row in enumerate(reader, start=2):
            if None in row or any(row[column] is None for column in expected):
                raise DcsProtocolError(
                    f"{kind} CSV row {row_number} does not match the V1 schema",
                    code="csv_schema_mismatch",
                )
            rows.append((row_number, {column: row[column] for column in expected}))
        return rows
    except csv.Error as exc:
        raise DcsProtocolError(
            f"{kind} CSV could not be parsed",
            code="csv_parse_error",
        ) from exc


def _parse_bool(value: str, field_name: str, row_number: int) -> bool:
    try:
        return parse_bool(value, field_name=field_name)
    except DcsProtocolError as exc:
        raise DcsProtocolError(
            f"{field_name} at CSV row {row_number} must be true or false",
            code=exc.code,
        ) from exc


def _parse_int(value: str, field_name: str, row_number: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise DcsProtocolError(
            f"{field_name} at CSV row {row_number} must be an integer",
            code="invalid_integer",
        ) from exc

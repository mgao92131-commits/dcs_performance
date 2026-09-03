import json
from datetime import datetime, timezone

import pytest

from dcs_performance.delivery.serializer import normalize_json_value, write_result_json


def test_strict_serializer_preserves_unicode_datetimes_and_posix_paths(tmp_path):
    path = tmp_path / "result.json"
    write_result_json(path, {
        "message": "中文",
        "when": datetime(2026, 9, 3, 8, 1, 2),
        "image": "images/foo.png",
        "nested": {"at": datetime(2026, 9, 3, tzinfo=timezone.utc)},
    })
    text = path.read_text(encoding="utf-8")
    assert "中文" in text and "\\u4e2d" not in text
    value = json.loads(text)
    assert value["when"] == "2026-09-03T08:01:02"
    assert value["nested"]["at"].endswith("+00:00")
    assert value["image"] == "images/foo.png"


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_values_are_rejected(value):
    with pytest.raises(ValueError, match="non-finite"):
        normalize_json_value({"value": value})


def test_unknown_business_type_is_rejected():
    with pytest.raises(TypeError, match="unsupported JSON value"):
        normalize_json_value({"value": object()})

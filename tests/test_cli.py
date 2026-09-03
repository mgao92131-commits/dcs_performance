import argparse
import pytest

from dcs_performance.cli import _local_datetime, main
from pathlib import Path
from types import SimpleNamespace


def test_list_rules_uses_metadata_and_returns_zero(capsys):
    assert main(["--list-rules"]) == 0

    output = capsys.readouterr().out
    assert "example_rule" in output
    assert "persistent_high_alarm" in output
    assert "持续高报考核" in output
    assert "enabled" in output


def test_run_command_returns_zero_and_prints_completed(monkeypatch, capsys, tmp_path):
    seen = {}
    delivered = SimpleNamespace(
        run_id="20260903T080000_20260903T200000_A",
        rule_count=1,
        point_count=2,
        event_count=0,
        total_score=0.0,
        result_json_path=tmp_path / "run" / "result.json",
        images_path=tmp_path / "run" / "images",
    )

    class Manager:
        def __init__(self, **kwargs):
            seen["manager"] = kwargs

        def deliver(self, shift, output, *, overwrite=False):
            assert shift.start_time.isoformat() == "2026-09-03T08:00:00"
            assert Path(output) == tmp_path
            assert overwrite is True
            return delivered

    def client(*args):
        seen["service_args"] = args
        return object()

    monkeypatch.setattr("dcs_performance.cli.DcsServiceClient", client)
    monkeypatch.setattr("dcs_performance.cli.DeliveryManager", Manager)
    assert main([
        "run", "--at", "2026-09-03T13:00:00", "--output", str(tmp_path),
        "--rules-dir", str(tmp_path / "rules"),
        "--service-url", "http://dcs.test", "--overwrite",
    ]) == 0
    output = capsys.readouterr().out
    assert "Assessment completed" in output
    assert delivered.run_id in output
    assert seen["service_args"] == ("http://dcs.test",)
    assert seen["manager"]["rules_dir"] == tmp_path / "rules"


def test_run_command_failure_is_nonzero(monkeypatch, capsys, tmp_path):
    class Manager:
        def __init__(self, **kwargs):
            pass

        def deliver(self, *args, **kwargs):
            raise RuntimeError("render exploded")

    monkeypatch.setattr("dcs_performance.cli.DcsServiceClient", lambda *args: object())
    monkeypatch.setattr("dcs_performance.cli.DeliveryManager", Manager)
    assert main(["run", "--at", "2026-09-03T13:00:00", "--output", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "Assessment completed" not in captured.out
    assert "render exploded" in captured.err


def test_at_rejects_timezone_aware_datetime():
    with pytest.raises(argparse.ArgumentTypeError, match="timezone offset"):
        _local_datetime("2026-09-03T13:00:00+08:00")

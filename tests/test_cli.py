from dcs_performance.cli import main


def test_list_rules_uses_metadata_and_returns_zero(capsys):
    assert main(["--list-rules"]) == 0

    output = capsys.readouterr().out
    assert "example_rule" in output
    assert "persistent_high_alarm" in output
    assert "持续高报考核" in output
    assert "enabled" in output

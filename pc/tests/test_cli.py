import pytest

from bodyctl.cli import build_parser, main

PLANNED = [
    "webcam",
    "device",
    "dashboard",
    "record",
    "replay",
    "latency-video",
    "synth",
    "trace",
    "profile",
    "launch-chiaki",
    "hil",
]


def test_help_lists_planned_commands(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for cmd in PLANNED:
        assert cmd in out


def test_profile_check_ok(capsys):
    assert main(["profile", "check", "config/profiles/gta5.yaml"]) == 0
    assert "ok" in capsys.readouterr().out


def test_profile_check_invalid(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("profile: x\nports: {}\nmodes: {}\n")
    assert main(["profile", "check", str(bad)]) == 1


def test_synth_and_trace_info(tmp_path, capsys):
    assert main(["synth", "-o", str(tmp_path), "--scenario", "jump"]) == 0
    path = tmp_path / "synth_jump.bptrace"
    assert path.exists()
    assert main(["trace", str(path)]) == 0
    out = capsys.readouterr().out
    assert "RAW" in out and "jump" in out


def test_unimplemented_commands_say_when(capsys):
    assert main(["hil", "--port", "COM9"]) == 2
    assert "M4" in capsys.readouterr().err


def test_parser_builds():
    build_parser()

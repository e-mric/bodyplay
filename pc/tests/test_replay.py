import re
from pathlib import Path

import pytest

from bodyctl.config.compile import BELT_PARAM_IDS, belt_params, flatten_gestures
from bodyctl.config.profile import load_profile
from bodyctl.tools import replay
from bodyctl.tools.synth import FIXTURES_DIR

PARAM_TABLE = Path(__file__).parents[2] / "firmware" / "lib" / "gestures" / "param_table.cpp"


def test_param_ids_match_firmware():
    c = {name: int(i) for i, name in re.findall(r'\{(\d+), "([\w.]+)"\}', PARAM_TABLE.read_text())}
    assert c == BELT_PARAM_IDS


def test_flatten_uses_profile_values():
    prof = load_profile("config/profiles/gta5.yaml")
    flat = flatten_gestures(prof)
    assert flat["jump.dip_g"] == pytest.approx(-0.30)
    assert flat["cadence_curve.2"] == 3.5
    assert len(belt_params(prof)) == len(BELT_PARAM_IDS)


def test_score_matching():
    dets = [replay.Detection(1_000_000, "jump", 0.7), replay.Detection(3_000_000, "jump", 0.7)]
    s = {x.event: x for x in replay.score(dets, {"jump": [1_020_000, 5_000_000]})}
    j = s["jump"]
    assert (j.labels, j.detected, j.missed, j.false_positives) == (2, 1, 1, 1)
    assert j.median_offset_ms == pytest.approx(-20)


def _binary():
    try:
        return replay.replay_binary()
    except (FileNotFoundError, OSError) as e:
        pytest.skip(f"replay tool unavailable: {e}")


@pytest.mark.parametrize("fixture", ["synth_jump.bptrace", "synth_run_2.5_noisy0.bptrace"])
def test_replay_fixture_end_to_end(fixture):
    from bodyctl.trace import Trace

    exe = _binary()
    prof = load_profile("config/profiles/gta5.yaml")
    path = FIXTURES_DIR / fixture
    cal, dets = replay.run(path, flatten_gestures(prof), binary=exe)
    labels: dict[str, list[int]] = {}
    for lb in Trace.read(path).labels():
        labels.setdefault(lb.event, []).append(lb.t_us)
    scores = replay.score(dets, labels, after_us=cal)
    for s in scores:
        assert s.missed == 0 and s.false_positives == 0, s


def test_committed_profile_fixture_is_fresh():
    """firmware/test/fixtures/gta5.bpprofile feeds the C++ parser tests; regenerate with
    `uv run bodyctl profile compile config/profiles/gta5.yaml -o firmware/test/fixtures/gta5.bpprofile`."""
    from bodyctl.config.compile import compile_profile

    fresh = compile_profile(load_profile("config/profiles/gta5.yaml"))
    assert (FIXTURES_DIR / "gta5.bpprofile").read_bytes() == fresh


def test_compiled_profile_layout():
    import struct

    from bodyctl.config.compile import PROFILE_MAGIC, compile_profile
    from bodyctl.protocol import crc8

    prof = load_profile("config/profiles/gta5.yaml")
    data = compile_profile(prof)
    assert data[:4] == PROFILE_MAGIC
    version, n_params, n_ports, n_modes, hold, release = struct.unpack_from("<BBBBHH", data, 4)
    assert (version, n_params, n_ports, n_modes, hold, release) == (1, len(BELT_PARAM_IDS), 8, 6, 30, 30)
    assert crc8(data[:-1]) == data[-1]


def test_firmware_default_profile_is_fresh():
    """firmware/lib/padmap/default_profile.h is what a freshly flashed node or hub boots with.

    Regenerate: uv run bodyctl profile compile config/profiles/gta5.yaml --c-header
    -o firmware/lib/padmap/default_profile.h
    """
    from bodyctl.config.compile import c_header, compile_profile

    path = Path(__file__).parents[2] / "firmware" / "lib" / "padmap" / "default_profile.h"
    fresh = c_header(
        compile_profile(load_profile("config/profiles/gta5.yaml")), source="config/profiles/gta5.yaml"
    )
    assert path.read_text(encoding="utf-8") == fresh


def test_plot_jump(tmp_path):
    pytest.importorskip("matplotlib")
    from bodyctl.tools.plot import plot_event

    out = plot_event(FIXTURES_DIR / "synth_jump.bptrace", tmp_path / "jump.png", [1.6668])
    assert out.exists() and out.stat().st_size > 10_000

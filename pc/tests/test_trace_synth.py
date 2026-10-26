import numpy as np
import pytest

from bodyctl.protocol import Kind, NodeId, Packet
from bodyctl.tools import synth
from bodyctl.trace import Label, RecordType, Trace, TraceWriter


def test_trace_round_trip(tmp_path):
    path = tmp_path / "t.bptrace"
    p = Packet(NodeId.BELT, 3, 1234, Kind.EVENT, 1, 200)
    with TraceWriter(path, {"source": "test"}) as w:
        w.packet(p, 2000)
        w.label(Label("jump", 1234, {"note": "x"}))
        w.video_mark(2500, 42, "clip.mp4")
    tr = Trace.read(path)
    assert tr.header["source"] == "test"
    assert tr.header["format"] == 1
    assert [r.type for r in tr.records] == [RecordType.FRAME, RecordType.LABEL, RecordType.VIDEO]
    assert list(tr.frames()) == [(2000, p)]
    assert tr.labels() == [Label("jump", 1234, {"note": "x"})]


def test_rejects_non_trace(tmp_path):
    path = tmp_path / "bad.bptrace"
    path.write_bytes(b"hello world")
    with pytest.raises(ValueError, match="magic"):
        Trace.read(path)


@pytest.mark.parametrize("name", list(synth.SCENARIOS))
@pytest.mark.parametrize("noisy", [False, True])
def test_every_scenario_writes_and_reads(tmp_path, name, noisy):
    sig = synth.build(name, noisy=noisy, seed=1)
    path = synth.write_trace(sig, tmp_path / f"{name}.bptrace", name, noisy, 1)
    tr = Trace.read(path)
    samples = list(tr.raw_samples())
    assert len(samples) == sig.n
    # Per-sample timestamps are evenly spaced at the ODR.
    ts = np.array([t for _, t, _ in samples])
    assert np.all(np.diff(ts) == synth.PERIOD_US)
    assert tr.header["scenario"] == name


def _accel_z_g(sig: synth.Signal) -> np.ndarray:
    return sig.accel[:, 2]


def test_still_reads_one_g():
    sig = synth.build("still")
    assert np.allclose(sig.accel.mean(axis=0), [0, 0, 1])
    assert np.allclose(sig.gyro, 0)


def test_jump_shape_and_labels():
    sig = synth.build("jump")
    ev = {lb.event: lb.t_us for lb in sig.labels}
    assert ev["jump_onset"] < ev["jump"] < ev["takeoff"] < ev["landing"]
    i = lambda t: int(round(t * synth.ODR_HZ / 1e6))  # noqa: E731
    a = _accel_z_g(sig)
    assert a[i(ev["jump_onset"]) : i(ev["jump"])].min() < 0.6  # dip below 1 g
    assert a[i(ev["jump"]) : i(ev["takeoff"])].max() > 2.0  # push-off
    flight = a[i(ev["takeoff"]) + 5 : i(ev["landing"]) - 5]
    assert np.allclose(flight, 0.0)  # free fall reads 0 g
    # The tell comes well before take-off: that's the latency the belt node buys.
    assert ev["takeoff"] - ev["jump_onset"] > 300_000


def test_long_dip_labels_squat_hold():
    sig = synth.build("jump_long_dip")
    events = [lb.event for lb in sig.labels]
    assert "squat_hold" in events
    assert events.index("squat_hold") < events.index("jump")


@pytest.mark.parametrize("cadence", [1.5, 2.5, 3.5])
def test_run_cadence(cadence):
    sig = synth.build(f"run_{cadence}")
    steps = [lb.t_us for lb in sig.labels if lb.event == "step"]
    intervals = np.diff(steps) / 1e6
    assert np.allclose(1 / intervals, cadence, rtol=0.01)
    assert abs(_accel_z_g(sig).mean() - 1.0) < 0.02  # impacts are zero-mean around gravity
    assert [lb.event for lb in sig.labels].count("run_stop") == 1


def test_noisy_is_tilted_but_keeps_gravity_magnitude():
    sig = synth.build("still", noisy=True, seed=3)
    g = sig.accel.mean(axis=0)
    assert abs(np.linalg.norm(g) - 1.0) < 0.01
    assert abs(g[0]) > 0.1  # 8 degree mount tilt leaks gravity into x


def test_generate_all(tmp_path):
    paths = synth.generate_all(tmp_path, noisy_seeds=1)
    assert len(paths) == 2 * len(synth.SCENARIOS)
    assert all(p.exists() and p.stat().st_size > 1000 for p in paths)

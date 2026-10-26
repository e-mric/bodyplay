"""Device tools against the simulated node (firmware env `nodesim`: the real NodeCore
speaking the CDC protocol over pipes). Skipped where PlatformIO isn't installed."""

import time

import pytest

from bodyctl.config.compile import compile_profile
from bodyctl.config.profile import load_profile
from bodyctl.output.device import DeviceError, open_link
from bodyctl.protocol import EventCode, Kind, Mode, NodeId
from bodyctl.tools import replay
from bodyctl.tools.synth import FIXTURES_DIR
from bodyctl.trace import Trace

JUMP = str(FIXTURES_DIR / "synth_jump.bptrace")


@pytest.fixture
def link():
    try:
        lk = open_link(sim=JUMP, realtime=True, loop=True)
    except (FileNotFoundError, OSError) as e:
        pytest.skip(f"node simulator unavailable: {e}")
    yield lk
    lk.close()


def test_hello(link):
    h = link.hello()
    assert h.node_id == NodeId.BELT
    assert h.payload == b"sim"


def test_push_profile_and_mode(link):
    data = compile_profile(load_profile("config/profiles/gta5.yaml"))
    chunks = []
    link.push_profile(data, lambda i, n: chunks.append((i, n)))
    assert chunks == [(1, 2), (2, 2)]
    link.set_mode(Mode.CAR)
    bad = bytearray(data)
    bad[40] ^= 0xFF
    with pytest.raises(DeviceError, match="rejected"):
        link.push_profile(bytes(bad))


def test_events_state_and_stats(link):
    link.push_profile(compile_profile(load_profile("config/profiles/gta5.yaml")))
    got = []
    link.subscribe(lambda r: got.append(r.packet))
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and not any(
        p.kind == Kind.EVENT and p.code == EventCode.JUMP for p in got
    ):
        time.sleep(0.05)
    kinds = {p.kind for p in got}
    assert Kind.STATE in kinds
    assert any(p.kind == Kind.EVENT and p.code == EventCode.JUMP for p in got)
    assert "sample->detect" in link.stats()


def test_record_then_replay(link, tmp_path):
    from bodyctl.tools.record import Recorder

    rec = Recorder(link, tmp_path / "rec.bptrace", {"node": 1})
    rec.start()
    deadline = time.monotonic() + 8
    labelled = False
    while time.monotonic() < deadline:
        if not labelled and any(name == "jump" for _, name in rec.events):
            rec.label("jump")  # what pressing `j` in the dashboard does
            labelled = True
        if labelled and time.monotonic() > deadline - 3:
            break
        time.sleep(0.02)
    rec.stop()
    tr = Trace.read(tmp_path / "rec.bptrace")
    assert sum(1 for _ in tr.raw_samples()) > 1666 * 3
    labels = [lb for lb in tr.device_labels() if lb.event == "jump"]
    assert labels, "label recorded"
    jump_events = [t for t, name in rec.events if name == "jump"]
    # The live label lands on the device clock, a little after the jump it marks.
    assert min(abs(labels[0].t_us - t) for t in jump_events) < 150_000
    # The recording replays through the same gesture code (calibration from its own start).
    try:
        exe = replay.replay_binary()
    except (FileNotFoundError, OSError) as e:
        pytest.skip(str(e))
    _, dets = replay.run(tmp_path / "rec.bptrace", binary=exe)
    assert any(d.event == "jump" for d in dets)


def test_dashboard_view_draws():
    pytest.importorskip("cv2")
    from bodyctl.output.device import Received
    from bodyctl.protocol import Packet, RawSample, StateField
    from bodyctl.tools.device_dashboard import DeviceView, draw

    view = DeviceView()
    raw = b"".join(RawSample((0, 0, 4096), (0, 0, 0)).pack() for _ in range(16))
    view.on_packet(Received(0, Packet(1, 0, 1000, Kind.RAW, value=(16, 600), payload=raw), b""))
    view.on_packet(Received(0, Packet(1, 1, 5000, Kind.EVENT, EventCode.JUMP, 200), b""))
    view.on_packet(Received(0, Packet(1, 2, 5000, Kind.STATE, StateField.CADENCE, value=(250, 1000)), b""))
    pad = bytes([0, 129, 0, 0, 0, 127, 0]) + (1 << 4).to_bytes(4, "little")
    view.on_packet(Received(0, Packet(1, 3, 5000, Kind.STATE, StateField.PAD, payload=pad), b""))
    assert view.pad["buttons"] == 16 and view.state["cadence"] == (250, 1000)
    img = draw(view, "on_foot", "rec.bptrace")
    assert img.shape == (520, 1000, 3)


def test_sdl_mapping_string():
    from bodyctl.output import sdl_mapping as sdl

    assert sdl.sdl_guid(0x2886, 0x0056) == "03000000862800005600000000000000"
    m = sdl.mapping()
    fields = m.rstrip(",").split(",")
    assert fields[0] == sdl.sdl_guid() and fields[1] == "Bodyplay Belt"
    assert "a:b0" in fields and "x:b4" in fields and "lefttrigger:+a3" in fields
    assert fields[-1].startswith("platform:")

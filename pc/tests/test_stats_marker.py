from bodyctl.output.marker import NullMarker, open_marker
from bodyctl.protocol import Command, FrameDecoder, Kind, NodeId, Packet
from bodyctl.tools.stats import StageStats


def test_stage_stats_format():
    s = StageStats()
    for us in range(1000, 101000, 1000):  # 1..100 ms
        s.add("capture->pose", us)
    summ = s.summary("capture->pose")
    assert summ["n"] == 100
    assert 49 < summ["p50_ms"] < 52
    assert s.summary("pose->gesture") is None
    text = s.format()
    assert text.startswith("capture->pose n=100 p50=")
    s.reset()
    assert s.format() == ""


def test_window_drops_old_samples():
    s = StageStats(window=10)
    for us in range(100):
        s.add("frame->output", us)
    assert s.summary("frame->output")["n"] == 10


def test_marker_frame_is_cmd_marker():
    p = Packet(NodeId.PC, 0, 0, Kind.CMD, Command.MARKER)
    (body,) = FrameDecoder().feed(p.frame())
    q = Packet.unpack(body)
    assert (q.kind, q.code) == (Kind.CMD, Command.MARKER)


def test_open_marker_without_port():
    m = open_marker(None)
    assert isinstance(m, NullMarker)
    m.pulse()
    m.close()

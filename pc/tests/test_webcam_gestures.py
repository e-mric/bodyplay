import numpy as np
import pytest
from posegen import FPS, Timeline

from bodyctl.config.profile import Gestures, load_profile
from bodyctl.gestures import webcam as W
from bodyctl.gestures.webcam import WebcamGestures, cadence_to_stick, deadzone_map

GTA5 = "config/profiles/gta5.yaml"


def run(tl: Timeline, with_world: bool = True):
    g = WebcamGestures(load_profile(GTA5).gestures)
    out = []
    states = []
    for p in tl.poses(with_world):
        for ev in g.update(p):
            out.append((ev.name, ev.t_us / 1e6))
        states.append(
            (g.state.run_stick, g.state.lean_stick, g.state.aiming, g.state.sprinting, g.state.cadence_hz)
        )
    return out, states, g


def names(events):
    return [n for n, _ in events]


def test_helpers():
    assert deadzone_map(3, 6, 25) == 0
    assert deadzone_map(-25, 6, 25) == -1
    assert deadzone_map(15.5, 6, 25) == pytest.approx(0.5)
    curve = [1.5, 2.5, 3.5]
    assert cadence_to_stick(0, curve) == 0
    assert cadence_to_stick(1.5, curve) == 0.5
    assert cadence_to_stick(4.0, curve) == 1.0


def test_standing_still_is_quiet():
    events, states, g = run(Timeline(4.0))
    assert events == []
    assert states[-1][0] == 0


def test_run_start_steps_and_stop():
    tl = Timeline(8.0)
    starts = tl.run(1.0, 5.0, cadence_hz=2.5)
    events, states, g = run(tl)
    ns = names(events)
    assert ns[0] == "run_start"
    # run_start fires on the first knee lift, within ~0.2 s of the movement starting.
    assert events[0][1] - starts[0] < 0.2
    assert ns.count("step") == len(starts)
    assert ns[-1] == "run_stop"
    mid = states[tl.idx(4.0)]
    assert mid[4] == pytest.approx(2.5, abs=0.25)
    assert mid[0] > 0.9  # forward stick near full at jog cadence
    assert states[-1][0] == 0


def test_sprint_flag_above_curve():
    tl = Timeline(6.0)
    tl.run(1.0, 5.0, cadence_hz=3.75)
    _, states, _ = run(tl)
    assert states[tl.idx(4.0)][3] is True
    tl2 = Timeline(6.0)
    tl2.run(1.0, 5.0, cadence_hz=2.0)
    _, states2, _ = run(tl2)
    assert not any(s[3] for s in states2)


def test_jump_fires_at_reversal_before_takeoff():
    tl = Timeline(4.0)
    rev = tl.jump(1.5)
    events, _, _ = run(tl)
    jumps = [t for n, t in events if n == "jump"]
    assert len(jumps) == 1
    assert rev - 1 / FPS <= jumps[0] <= rev + 2 / FPS
    assert "step" not in names(events)


def test_slow_squat_is_crouch_not_jump():
    tl = Timeline(5.0)
    tl.squat_hold(1.0, 1.0)
    events, _, _ = run(tl)
    assert "crouch" in names(events)
    assert "jump" not in names(events)


@pytest.mark.parametrize(("deg", "sign"), [(15, 1), (-15, -1)])
def test_lean(deg, sign):
    tl = Timeline(3.0)
    tl.lean(1.0, 2.5, deg)
    _, states, _ = run(tl)
    expect = (15 - 6) / (25 - 6)
    assert states[tl.idx(2.0)][1] == pytest.approx(sign * expect, abs=0.02)
    assert states[tl.idx(0.5)][1] == 0


def test_punch():
    tl = Timeline(3.0)
    tl.punch(1.5)
    events, _, _ = run(tl)
    punches = [t for n, t in events if n == "punch"]
    assert len(punches) == 1
    assert 1.5 <= punches[0] <= 1.5 + 4 / FPS
    assert "aim_start" not in names(events)


def test_aim_hold_and_release():
    tl = Timeline(4.0)
    tl.aim(1.0, 3.0)
    events, states, _ = run(tl)
    ns = names(events)
    assert ns == ["aim_start", "aim_end"]
    t_start = events[0][1]
    assert 1.1 <= t_start <= 1.1 + 2 / FPS  # after the 120 ms hold
    assert states[tl.idx(2.0)][2] is True


def test_aim_2d_fallback_without_world():
    tl = Timeline(4.0)
    tl.aim(1.0, 3.0)
    events, _, _ = run(tl, with_world=False)
    assert "aim_start" in names(events)


def test_person_lost_releases_aim_and_stops_run():
    g = WebcamGestures(Gestures())
    tl = Timeline(4.0)
    tl.aim(0.5, 4.0)
    tl.run(0.5, 4.0, 2.5)
    poses = tl.poses()
    for p in poses[: tl.idx(2.0)]:
        g.update(p)
    assert g.state.aiming and g.state.running
    evs = []
    t = poses[tl.idx(2.0)].t_us
    for k in range(60):
        evs += [e.name for e in g.update(W.Pose(t + k * 33333, np.zeros((33, 3))))]
    assert "aim_end" in evs and "run_stop" in evs
    assert not g.state.visible

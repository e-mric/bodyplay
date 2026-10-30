import numpy as np
import pytest
from posegen import TORSO_PX, Timeline

from bodyctl.config.profile import load_profile
from bodyctl.gestures import webcam as W
from bodyctl.gestures.webcam import WebcamGestures
from bodyctl.output.gamepad import DryRunBackend, Gamepad
from bodyctl.tier0 import Tier0
from bodyctl.tools import webcam_tune as wt
from bodyctl.tools.drill import POINTS, Cue, Recording, build_script

GTA5 = "config/profiles/gta5.yaml"
AIM_ELBOW, AIM_WRIST = np.array((-0.19, -0.48, -0.25)), np.array((-0.19, -0.48, -0.5))


def run(tl: Timeline):
    g = WebcamGestures(load_profile(GTA5).gestures)
    events, states = [], []
    for p in tl.poses():
        events += [(e.name, e.t_us / 1e6) for e in g.update(p)]
        states.append((g.state.aiming, g.state.wheel_open, g.state.wheel_x, g.state.wheel_y))
    return events, states


def recoil(tl: Timeline, t_s: float) -> None:
    """Aiming wrist kicks up ~0.3 torso lengths in two frames and settles back."""
    i0 = tl.idx(t_s)
    for k, dy in enumerate((-0.08, -0.16, -0.12, -0.06, -0.02)):
        tl._set_right_arm(i0 + k, AIM_ELBOW, AIM_WRIST + np.array((0.0, dy, 0.0)))


def test_recoil_flick_shoots_once_and_keeps_aiming():
    tl = Timeline(6.0)
    tl.aim(1.0, 4.5)
    recoil(tl, 2.5)
    events, states = run(tl)
    shots = [t for n, t in events if n == "shoot"]
    assert len(shots) == 1 and abs(shots[0] - 2.5) < 0.1
    aim = [(n, t) for n, t in events if n.startswith("aim")]
    assert [n for n, _ in aim] == ["aim_start", "aim_end"] and aim[1][1] > 4.4  # aim held through the flick
    assert "punch" not in [n for n, _ in events]


def test_no_shot_without_aiming():
    tl = Timeline(4.0)
    for k, y in enumerate((-0.3, -0.45, -0.48)):  # arm comes up fast but is never held to aim
        tl._set_right_arm(tl.idx(2.0) + k, AIM_ELBOW, np.array((-0.19, y, -0.5)))
    events, _ = run(tl)
    assert "shoot" not in [n for n, _ in events]


def wheel_timeline(point_left: bool = True) -> Timeline:
    tl = Timeline(6.0)
    sx, sy = tl.img[0, W.R_SHOULDER, :2]
    i0, i1 = tl.idx(1.0), tl.idx(4.0)
    tl.img[i0:i1, W.R_WRIST, :2] = (sx, sy - 0.45 * TORSO_PX)  # right hand over the shoulder
    tl.img[i0:i1, W.R_ELBOW, :2] = (sx - 20, sy - 0.1 * TORSO_PX)
    if point_left:
        lx, ly = tl.img[0, W.L_SHOULDER, :2]
        j0, j1 = tl.idx(2.0), tl.idx(3.5)
        tl.img[j0:j1, W.L_WRIST, :2] = (lx + 0.8 * TORSO_PX, ly)  # your left = image right
        tl.img[j0:j1, W.L_ELBOW, :2] = (lx + 0.4 * TORSO_PX, ly)
    return tl


def test_wheel_opens_points_and_closes():
    tl = wheel_timeline()
    events, states = run(tl)
    names = [n for n, _ in events]
    assert names.count("wheel_open") == 1 and names.count("wheel_close") == 1
    t_open = next(t for n, t in events if n == "wheel_open")
    t_close = next(t for n, t in events if n == "wheel_close")
    assert 1.0 < t_open < 1.3 and 4.0 < t_close < 4.3
    assert "punch" not in names  # reaching and pointing aren't punches
    _, open_, x, y = states[tl.idx(2.5)]
    assert open_ and x == pytest.approx(-1, abs=0.05) and y == pytest.approx(0, abs=0.05)  # stick left
    _, open_, x, y = states[tl.idx(1.5)]
    assert open_ and (x, y) == (0, 0)  # left arm resting: no selection


class Clock:
    t = 0

    def __call__(self) -> int:
        return self.t


def drive(app: Tier0, clock: Clock, tl: Timeline, extra_frames: int = 0):
    """Feed the timeline on its own clock (press timing needs simulated time)."""
    reports = {}
    for p in tl.poses():
        clock.t = p.t_us
        app.step(p.t_us, p, t_pose_us=p.t_us)
        reports[p.t_us] = app.gamepad.report
    for i in range(tl.n, tl.n + extra_frames):  # let a last press finish
        clock.t = tl.t_us(i)
        app.engine.apply(app.gestures.state, [], clock.t)
    return reports


def tier0_with_backend():
    prof = load_profile(GTA5)
    backend, clock = DryRunBackend(), Clock()
    return (
        Tier0(prof, Gamepad(backend, prof.press.hold_ms, prof.press.release_ms), clock=clock),
        backend,
        clock,
    )


def test_wheel_holds_l1_and_drives_the_right_stick():
    app, backend, clock = tier0_with_backend()
    tl = wheel_timeline()
    reports = drive(app, clock, tl)
    at = reports[tl.t_us(tl.idx(2.5))]
    assert "l1" in at.buttons and at.right[0] == pytest.approx(-1, abs=0.05)
    end = reports[tl.t_us(tl.n - 1)]
    assert "l1" not in end.buttons and end.right == (0.0, 0.0)


def test_crouch_and_stand_tap_cover():
    tl = Timeline(6.0)
    tl.squat_hold(1.0, 1.5)
    events, _ = run(tl)
    names = [n for n, _ in events]
    assert names.index("crouch") < names.index("stand")
    app, backend, clock = tier0_with_backend()
    drive(app, clock, tl, extra_frames=10)
    r1 = [("r1" in r.buttons) for _, r in backend.history]
    taps = sum(1 for a, b in zip([False, *r1], r1, strict=False) if b and not a)
    assert taps == 2  # into cover, out of cover


def test_drill_scores_shoot_and_wheel():
    labels = [st.label for st in build_script(["shoot", "wheel", "crouch"], 2)]
    assert labels.count("shoot") == 4 and labels.count("wheel_open") == 2
    assert "stand_up" in labels and sum(lb in POINTS for lb in labels) == 2

    tl = wheel_timeline()
    t = np.array([tl.t_us(i) for i in range(tl.n)], np.int64)
    cues = [Cue(lb, int(a * 1e6), int(b * 1e6)) for lb, a, b in
            (("still", 0, 1.0), ("wheel_open", 1.0, 2.0), ("point_left", 2.0, 3.5), ("gap", 3.5, 4.0),
             ("wheel_close", 4.0, 6.0))]  # fmt: skip
    rec = Recording({}, t, tl.img.astype(np.float32), tl.world.astype(np.float32), cues)
    sc = wt.score(rec, load_profile(GTA5).gestures)
    assert sc.events["wheel_open"].hits == 1 and sc.events["wheel_close"].hits == 1
    assert sc.wheel["point_left"] > 0.9
    assert "wheel pointing" in wt.format_scores(sc)


def wings_timeline(roll_px: float = 0.0, up_px: float = 0.0) -> Timeline:
    """Arms spread wide from 1 s to 4 s; roll_px lowers your left hand and raises your
    right one, up_px raises both."""
    tl = Timeline(6.0)
    i0, i1 = tl.idx(1.0), tl.idx(4.0)
    lx, ly = tl.img[0, W.L_SHOULDER, :2]
    rx, ry = tl.img[0, W.R_SHOULDER, :2]
    j0, j1 = tl.idx(2.0), tl.idx(3.5)  # the roll / pitch part
    for a, b, r, u in ((i0, j0, 0, 0), (j0, j1, roll_px, up_px), (j1, i1, 0, 0)):
        tl.img[a:b, W.L_WRIST, :2] = (lx + 1.1 * TORSO_PX, ly + r - u)  # your left = image right
        tl.img[a:b, W.L_ELBOW, :2] = (lx + 0.55 * TORSO_PX, ly + r / 2 - u / 2)
        tl.img[a:b, W.R_WRIST, :2] = (rx - 1.1 * TORSO_PX, ry - r - u)
        tl.img[a:b, W.R_ELBOW, :2] = (rx - 0.55 * TORSO_PX, ry - r / 2 - u / 2)
    return tl


def fly_states(tl: Timeline):
    g = WebcamGestures(load_profile(GTA5).gestures)
    events, states = [], []
    for p in tl.poses():
        events += [e.name for e in g.update(p)]
        states.append((g.state.flying, g.state.fly_roll, g.state.fly_pitch))
    return events, states


def test_wings_fly_roll_and_pitch():
    events, states = fly_states(wings_timeline(roll_px=120))
    assert events.count("fly_start") == 1 and events.count("fly_end") == 1
    assert "punch" not in events  # spreading the arms isn't a punch
    flying, roll, pitch = states[Timeline(6.0).idx(2.7)]
    assert flying and roll < -0.5 and pitch == 0  # your left hand down: roll left
    flying, roll, pitch = states[Timeline(6.0).idx(1.7)]
    assert flying and roll == 0 and pitch == 0  # wings level

    _, states = fly_states(wings_timeline(up_px=0.45 * TORSO_PX))
    flying, roll, pitch = states[Timeline(6.0).idx(2.7)]
    assert flying and pitch < -0.5 and roll == 0  # hands up: nose up = stick back


def test_wings_drive_the_left_stick_only_in_flight_modes():
    tl = wings_timeline(roll_px=120)
    i = tl.idx(2.7)
    app, _, clock = tier0_with_backend()
    on_foot = drive(app, clock, tl)
    assert on_foot[tl.t_us(i)].left == (0.0, 0.0)  # on foot the wings do nothing
    app, _, clock = tier0_with_backend()
    app.engine.set_mode("plane", 0)
    flying = drive(app, clock, tl)
    x, y = flying[tl.t_us(i)].left
    assert x < -0.5 and y == 0  # rule 4 exception: the body flies
    assert flying[tl.t_us(tl.n - 1)].left == (0.0, 0.0)  # landed: neutral again


def test_drill_scores_flight():
    labels = [st.label for st in build_script(["fly"], 4)]
    assert labels.count("wings") == 4 and {"roll_left", "roll_right", "nose_up", "nose_down"} <= set(labels)
    tl = wings_timeline(roll_px=120)
    t = np.array([tl.t_us(i) for i in range(tl.n)], np.int64)
    cues = [Cue(lb, int(a * 1e6), int(b * 1e6)) for lb, a, b in
            (("still", 0, 1.0), ("wings", 1.0, 2.0), ("roll_left", 2.0, 3.5), ("gap", 3.5, 4.0),
             ("land", 4.0, 6.0))]  # fmt: skip
    rec = Recording({}, t, tl.img.astype(np.float32), tl.world.astype(np.float32), cues)
    sc = wt.score(rec, load_profile(GTA5).gestures)
    assert sc.events["fly_start"].hits == 1 and sc.events["fly_end"].hits == 1
    assert sc.fly["roll_left"] > 0.9
    assert "flight: roll left" in wt.format_scores(sc)

"""Fixes from the full left30 drill (2026-11-04): standing up isn't a jump, running arms
don't punch, either arm aims and shoots, a guarding fist doesn't aim."""

import numpy as np
from posegen import _IMG, TORSO_M, TORSO_PX, Timeline

from bodyctl.config.profile import load_profile
from bodyctl.gestures import webcam as W
from bodyctl.gestures.webcam import WebcamGestures

GTA5 = "config/profiles/gta5.yaml"


def names(tl: Timeline, with_world: bool = True) -> list[tuple[str, float]]:
    g = WebcamGestures(load_profile(GTA5).gestures)
    return [(e.name, e.t_us / 1e6) for p in tl.poses(with_world) for e in g.update(p)]


def test_standing_up_fast_from_a_crouch_is_not_a_jump():
    tl = Timeline(5.0)
    i0, i1, i2 = tl.idx(1.0), tl.idx(1.3), tl.idx(2.5)
    k = np.arange(i1 - i0)
    tl.shift_upper(i0, i1, 0.25 * TORSO_PX * k / len(k))  # down in 0.3 s
    tl.shift_upper(i1, i2, np.full(i2 - i1, 0.25 * TORSO_PX))  # hold
    n = tl.idx(0.2)
    up = 0.25 * TORSO_PX * (1 - np.arange(1, n + 1) / n) - 0.04 * TORSO_PX * np.sin(
        np.pi * np.arange(1, n + 1) / n
    )
    tl.shift_upper(i2, i2 + n, up)  # up in 0.2 s (1.25 torso/s), overshooting a little
    ev = [n for n, _ in names(tl)]
    assert "crouch" in ev and "stand" in ev
    assert "jump" not in ev


def test_a_jump_still_fires_after_standing_up():
    tl = Timeline(6.0)
    tl.squat_hold(1.0, 1.0)
    t_jump = tl.jump(4.5)
    jumps = [t for n, t in names(tl) if n == "jump"]
    assert len(jumps) == 1 and abs(jumps[0] - t_jump) < 0.3


def test_running_arms_dont_punch_but_a_punch_after_stopping_does():
    tl = Timeline(8.0)
    tl.run(1.0, 4.0, 2.5)
    tl.punch(2.5)  # mid-run: arm swing territory
    tl.punch(6.5)  # stopped by then
    punches = [t for n, t in names(tl) if n == "punch"]
    assert len(punches) == 1 and abs(punches[0] - 6.5) < 0.2


def set_left_arm(tl: Timeline, i: int, elbow_w, wrist_w) -> None:
    """Mirror of Timeline._set_right_arm: your left is image right and world +x."""
    tl.world[i, W.L_ELBOW], tl.world[i, W.L_WRIST] = elbow_w, wrist_w
    sx, sy = _IMG[W.L_SHOULDER]
    for j, p in ((W.L_ELBOW, elbow_w), (W.L_WRIST, wrist_w)):
        tl.img[i, j, 0] = sx + (p[0] - 0.18) * TORSO_PX / TORSO_M
        tl.img[i, j, 1] = sy + (p[1] + 0.5) * TORSO_PX / TORSO_M


def test_left_arm_aims_and_shoots():
    tl = Timeline(6.0)
    elbow, wrist = np.array((0.19, -0.48, -0.25)), np.array((0.19, -0.48, -0.5))
    for i in range(tl.idx(1.0), tl.idx(4.5)):
        set_left_arm(tl, i, elbow, wrist)
    i0 = tl.idx(2.5)
    for k, dy in enumerate((-0.08, -0.16, -0.12, -0.06, -0.02)):  # recoil
        set_left_arm(tl, i0 + k, elbow, wrist + np.array((0.0, dy, 0.0)))
    ev = names(tl)
    assert [n for n, _ in ev if n.startswith("aim")] == ["aim_start", "aim_end"]
    shots = [t for n, t in ev if n == "shoot"]
    assert len(shots) == 1 and abs(shots[0] - 2.5) < 0.1


def test_2d_guarding_fist_does_not_aim_but_a_level_arm_does():
    sx, sy = _IMG[W.R_SHOULDER]
    guard, aim = Timeline(3.0), Timeline(3.0)
    for i in range(guard.idx(0.5), guard.n):
        guard.img[i, W.R_ELBOW, :2] = (sx - 10, sy + 0.5 * TORSO_PX)  # elbow down
        guard.img[i, W.R_WRIST, :2] = (sx + 10, sy - 0.05 * TORSO_PX)  # fist at the chin
        aim.img[i, W.R_ELBOW, :2] = (sx - 0.15 * TORSO_PX, sy)  # arm level, toward the camera
        aim.img[i, W.R_WRIST, :2] = (sx - 0.3 * TORSO_PX, sy - 0.02 * TORSO_PX)
    assert "aim_start" not in [n for n, _ in names(guard, with_world=False)]
    assert "aim_start" in [n for n, _ in names(aim, with_world=False)]


def test_losing_the_body_lets_go_of_the_lean():
    tl = Timeline(3.0)
    tl.lean(0.5, 3.0, 30)
    g = WebcamGestures(load_profile(GTA5).gestures)
    for p in tl.poses()[: tl.idx(2.0)]:
        g.update(p)
    assert g.state.lean_stick > 0.5
    g.update(None)  # camera loses you
    assert g.state.lean_stick == 0.0 and g.state.lean_deg == 0.0

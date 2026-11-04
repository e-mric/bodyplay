"""Push-ups (side-on plank) and pull-ups (hanging from a bar) on synthetic 2D poses."""

import numpy as np
from posegen import TORSO_PX, Timeline, standing
from test_shoot_wheel_cover import tier0_with_backend

from bodyctl.config.profile import load_profile
from bodyctl.gestures import webcam as W
from bodyctl.gestures.webcam import Pose, WebcamGestures
from bodyctl.tools import webcam_tune as wt
from bodyctl.tools.drill import Cue, Recording, build_script

GTA5 = "config/profiles/gta5.yaml"
ARM = 75.0  # push-up upper arm and forearm, px
BAR_Y, HANG_ARM = 30.0, 60.0  # pull-up bar height and upper arm / forearm, px
STANDING_EVENTS = {"jump", "punch", "run_start", "aim_start", "shoot", "wheel_open", "fly_start"}


def plank(h: float) -> np.ndarray:
    """Side view, head to the image left, hands on the floor at x=200: shoulders h px
    above the wrists (2 ARM = straight arms), body pivoting on the feet."""
    img = np.zeros((W.NUM_LANDMARKS, 3))
    img[:, :2], img[:, 2] = (360, 400), 0.99
    wrist = np.array((200.0, 440.0))
    shoulder = wrist - (0, h)
    elbow = wrist + (np.sqrt(ARM**2 - (h / 2) ** 2), -h / 2)  # elbows point back, toward the feet
    ankle = np.array((520.0, 440.0))
    hip = (shoulder + ankle) / 2
    pts = {W.NOSE: shoulder - (30, 10), W.L_SHOULDER: shoulder, W.L_ELBOW: elbow, W.L_WRIST: wrist,
           W.L_HIP: hip, W.L_KNEE: (hip + ankle) / 2, W.L_ANKLE: ankle}  # fmt: skip
    for i, p in pts.items():
        img[i, :2] = p
    for left, right in ((W.L_SHOULDER, W.R_SHOULDER), (W.L_ELBOW, W.R_ELBOW), (W.L_WRIST, W.R_WRIST),
                        (W.L_HIP, W.R_HIP), (W.L_KNEE, W.R_KNEE), (W.L_ANKLE, W.R_ANKLE)):  # fmt: skip
        img[right, :2], img[right, 2] = img[left, :2] + (0, 3), 0.3  # far side, hidden behind
    return img


def hang(h: float) -> np.ndarray:
    """Facing the camera, hands on a bar at BAR_Y above each shoulder: shoulders h px
    below the bar (2 HANG_ARM = dead hang), the body moving with them."""
    img, _ = standing()
    dy = BAR_Y + h - img[W.L_SHOULDER, 1]
    body = [W.NOSE, W.L_SHOULDER, W.R_SHOULDER, W.L_HIP, W.R_HIP, W.L_KNEE, W.R_KNEE, W.L_ANKLE, W.R_ANKLE]
    img[body, 1] += dy
    for sh, el, wr, out in (
        (W.L_SHOULDER, W.L_ELBOW, W.L_WRIST, 1),
        (W.R_SHOULDER, W.R_ELBOW, W.R_WRIST, -1),
    ):
        sx = img[sh, 0]
        img[wr, :2] = (sx, BAR_Y)
        img[el, :2] = (sx + out * np.sqrt(HANG_ARM**2 - (h / 2) ** 2), BAR_Y + h / 2)
    return img


class Frames:
    def __init__(self) -> None:
        self.imgs: list[np.ndarray] = []

    def hold(self, img: np.ndarray, s: float) -> None:
        self.imgs += [img.copy() for _ in range(int(s * 30))]

    def blend(self, a: np.ndarray, b: np.ndarray, s: float) -> None:
        n = int(s * 30)
        self.imgs += [a + (b - a) * (k + 1) / n for k in range(n)]

    def reps(self, make, top: float, bottom: float, n: int, rep_s: float = 1.6, rest_s: float = 0.4) -> None:
        for _ in range(n):
            k = np.arange(int(rep_s * 30))
            self.imgs += [
                make(bottom + (top - bottom) * (0.5 + 0.5 * np.cos(2 * np.pi * x / len(k)))) for x in k
            ]
            self.hold(make(top), rest_s)

    def poses(self) -> list[Pose]:
        return [Pose(int(round(i * 1e6 / 30)), img) for i, img in enumerate(self.imgs)]


def pushup_set(n: int = 5) -> Frames:
    """Stand 1 s, get down 1.5 s, plank 0.5 s, n push-ups (2 s each), get up 1.5 s, stand 4.5 s."""
    f, stand = Frames(), standing()[0]
    f.hold(stand, 1.0)
    f.blend(stand, plank(2 * ARM), 1.5)
    f.hold(plank(2 * ARM), 0.5)
    f.reps(plank, 2 * ARM, 80.0, n)
    f.blend(plank(2 * ARM), stand, 1.5)
    f.hold(stand, 4.5)
    return f


def pullup_set(n: int = 4) -> Frames:
    """Stand 1 s, hands up to the bar 0.6 s, hang 0.8 s, n pull-ups, hands down 0.6 s, stand 4 s."""
    f, stand = Frames(), standing()[0]
    f.hold(stand, 1.0)
    f.blend(stand, hang(2 * HANG_ARM), 0.6)
    f.hold(hang(2 * HANG_ARM), 0.8)
    f.reps(hang, 2 * HANG_ARM, 40.0, n)
    f.blend(hang(2 * HANG_ARM), stand, 0.6)
    f.hold(stand, 4.0)
    return f


def run(frames: Frames):
    g = WebcamGestures(load_profile(GTA5).gestures)
    events, reps = [], []
    for p in frames.poses():
        events += [(e.name, e.t_us / 1e6) for e in g.update(p)]
        reps.append((g.state.workout, g.state.workout_reps))
    return events, reps


def test_pushups_count_each_rep_and_nothing_else():
    events, reps = run(pushup_set(5))
    names = [n for n, _ in events]
    assert names.count("pushup") == 5
    assert names.count("workout_start") == 1 and names.count("workout_end") == 1
    t_start = next(t for n, t in events if n == "workout_start")
    t_end = next(t for n, t in events if n == "workout_end")
    assert 1.5 < t_start < 3.0 and 13.0 < t_end < 14.5
    # Getting down and up isn't a jump or a punch. Kneeling down can still count as one
    # running step before the plank shows (run_start, then run_stop): the drill measures it.
    assert not (STANDING_EVENTS - {"run_start"}) & set(names)
    assert names.count("crouch") == names.count("stand")  # cover taps cancel out
    assert ("pushup", 5) in reps and reps[-1] == ("", 5)


def test_pullups_count_each_rep_without_opening_the_wheel():
    events, reps = run(pullup_set(4))
    names = [n for n, _ in events]
    assert names.count("pullup") == 4
    assert names.count("workout_start") == 1 and names.count("workout_end") == 1
    assert not STANDING_EVENTS & set(names)  # the body rising isn't a jump; both hands up isn't the wheel
    assert ("pullup", 4) in reps


def test_standing_gestures_come_back_after_a_set():
    f = pushup_set(2)
    tl = Timeline(3.0)
    tl.jump(1.0)
    f.imgs += list(tl.img)
    events, _ = run(f)
    jumps = [t for n, t in events if n == "jump"]
    assert len(jumps) == 1 and jumps[0] > len(f.imgs) / 30 - 2.5


def test_wheel_still_points_up():
    """The wheel's top slot raises the left hand above the shoulder, like a hang: the open
    wheel keeps it a wheel."""
    tl = Timeline(5.0)
    sx, sy = tl.img[0, W.R_SHOULDER, :2]
    lx, ly = tl.img[0, W.L_SHOULDER, :2]
    tl.img[30:120, W.R_WRIST, :2] = (sx, sy - 0.6 * TORSO_PX)
    tl.img[30:120, W.R_ELBOW, :2] = (sx - 20, sy - 0.2 * TORSO_PX)
    tl.img[60:105, W.L_WRIST, :2] = (lx, ly - 0.8 * TORSO_PX)
    tl.img[60:105, W.L_ELBOW, :2] = (lx, ly - 0.4 * TORSO_PX)
    g = WebcamGestures(load_profile(GTA5).gestures)
    ys, names = [], []
    for p in tl.poses(with_world=False):
        names += [e.name for e in g.update(p)]
        ys.append(g.state.wheel_y)
    assert "workout_start" not in names and names.count("wheel_open") == 1
    assert max(ys) > 0.9  # pointing at the top slot


def test_each_rep_taps_cross():
    app, backend, clock = tier0_with_backend()
    poses = pushup_set(3).poses()
    for p in poses:
        clock.t = p.t_us
        app.step(p.t_us, p, t_pose_us=p.t_us)
    for k in range(1, 10):  # let the last press finish
        clock.t = poses[-1].t_us + k * 33_333
        app.engine.apply(app.gestures.state, [], clock.t)
    cross = [("cross" in r.buttons) for _, r in backend.history]
    assert sum(1 for a, b in zip([False, *cross], cross, strict=False) if b and not a) == 3


def test_drill_scores_pushups():
    labels = [st.label for st in build_script(["pushup", "pullup"], 3)]
    assert labels.count("plank") == 1 and labels.count("pushup") == 3 and labels.count("get_up") == 1
    assert labels.count("hang") == 1 and labels.count("pullup") == 3 and labels.count("drop") == 1

    f = pushup_set(5)
    img = np.array(f.imgs, np.float32)
    t = np.array([p.t_us for p in f.poses()], np.int64)
    cues = [Cue("still", 0, 1_000_000), Cue("plank", 1_000_000, 3_000_000)]
    cues += [Cue("pushup", int((3 + 2 * k) * 1e6), int((5 + 2 * k) * 1e6)) for k in range(5)]
    cues += [Cue("get_up", 13_000_000, 17_000_000)]
    rec = Recording({}, t, img, np.full_like(img, np.nan), cues)
    sc = wt.score(rec, load_profile(GTA5).gestures)
    assert sc.events["pushup"].hits == 5 and sc.events["pushup"].false == 0
    assert sc.events["workout_start"].hits == 1 and sc.events["workout_end"].hits == 1


def test_set_survives_the_camera_losing_the_hands():
    f = pushup_set(5)
    for i in range(int(6.0 * 30), int(7.0 * 30)):  # a second without hands or hips mid-set
        f.imgs[i] = f.imgs[i].copy()
        f.imgs[i][[W.L_WRIST, W.R_WRIST, W.L_HIP, W.R_HIP], 2] = 0.1
    events, _ = run(f)
    names = [n for n, _ in events]
    assert names.count("workout_start") == 1 and names.count("workout_end") == 1

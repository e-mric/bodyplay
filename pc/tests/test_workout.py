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


GTA6 = "config/profiles/gta6.yaml"


def fitness_reports(frames: Frames, exercise: str = ""):
    from bodyctl.mapping import BindingEngine
    from bodyctl.output.gamepad import DryRunBackend, Gamepad

    prof = load_profile(GTA6)
    gp = Gamepad(DryRunBackend(), prof.press.hold_ms, prof.press.release_ms)
    engine = BindingEngine(prof, gp, mode="fitness")
    g = WebcamGestures(prof.gestures, fitness=True, exercise=exercise)
    out = []
    for p in frames.poses():
        events = g.update(p)
        engine.apply(g.state, events, p.t_us)
        out.append((p.t_us / 1e6, gp.report, g.state.workout_progress))
    return out


def test_mimed_pullups_pausing_at_the_chest_stay_one_set():
    """No bar: arms up, pull the hands down to the chest, pause, back up. The hands
    dip under the shoulders at the top; only dropping the arms ends the set."""
    chest = -0.25 * TORSO_PX  # hands this far above the shoulders: under them
    f, stand = Frames(), standing()[0]
    f.hold(stand, 1.0)
    f.blend(stand, hang(2 * HANG_ARM), 0.6)
    f.hold(hang(2 * HANG_ARM), 0.8)
    for _ in range(4):
        f.blend(hang(2 * HANG_ARM), hang(chest), 0.7)
        f.hold(hang(chest), 1.0)
        f.blend(hang(chest), hang(2 * HANG_ARM), 0.7)
        f.hold(hang(2 * HANG_ARM), 0.4)
    f.blend(hang(2 * HANG_ARM), stand, 0.6)
    f.hold(stand, 4.0)
    names = [n for n, _ in run(f)[0]]
    assert names.count("pullup") == 4
    assert names.count("workout_start") == 1 and names.count("workout_end") == 1


def gradual(values: list[float]) -> bool:
    """A press that starts partway and climbs all the way down (GTA 6's L2 + R2, by hand)."""
    return bool(values) and values[0] < 0.5 and max(values) == 1.0 and values[1] > values[0]


def press_times(rows) -> list[float]:
    return [t for (_, a, _), (t, b, _) in zip(rows, rows[1:], strict=False) if b.l2 >= 0.5 > a.l2]


def release_times(rows) -> list[float]:
    return [t for (_, a, _), (t, b, _) in zip(rows, rows[1:], strict=False) if a.l2 >= 0.5 > b.l2]


def test_gta6_pullups_press_up_hold_at_the_top_release_going_down():
    rows = fitness_reports(pullup_set(3))
    pressed = [(t, r, p) for t, r, p in rows if r.l2 > 0]
    assert pressed and all(r.l2 == r.r2 for _, r, _ in pressed)  # both triggers together
    assert gradual([r.l2 for _, r, _ in pressed])  # GTA 6: pressed down over ~0.5 s, as by hand
    tops = [r for _, r, p in rows if p >= 1.0]
    assert tops and all(r.l2 == 1.0 for r in tops)  # held at the top (the game shows DESCENDRE)
    # Released only while going down: every pressed->released edge happens below the top.
    releases = [p for (_, a, _), (_, b, p) in zip(rows, rows[1:], strict=False) if a.l2 > 0 and b.l2 == 0]
    assert releases and all(p < 1.0 for p in releases)
    # Pressed again from the bottom for every rep: one held press per rep. Coming down off
    # the bar at the end bends the elbows for a blink (the dismount, a known extra).
    held = [t1 - t0 for t0, t1 in zip(press_times(rows), release_times(rows), strict=False) if t1 - t0 >= 0.2]
    assert len(held) == 3
    circles = [t for (_, a, _), (t, b, _) in zip(rows, rows[1:], strict=False)
               if "circle" in b.buttons and "circle" not in a.buttons]  # fmt: skip
    assert len(circles) == 1  # let go of the bar once, at the end (RELACHER)
    # X is mashed from the first rep to the end of the set, ready for when the character
    # tires at the end (the game ignores it before), and stops when the set does.
    crosses = [t for (_, a, _), (t, b, _) in zip(rows, rows[1:], strict=False)
               if "cross" in b.buttons and "cross" not in a.buttons]  # fmt: skip
    first_rep = next(t for t, r, p in rows if p >= 1.0)
    assert len(crosses) > 10 and first_rep <= crosses[0] < first_rep + 0.5 and crosses[-1] < circles[0]


def test_gta6_stuck_rep_mashes_x():
    f, stand = Frames(), standing()[0]
    f.hold(stand, 1.0)
    f.blend(stand, hang(2 * HANG_ARM), 0.6)
    f.hold(hang(2 * HANG_ARM), 0.8)
    f.blend(hang(2 * HANG_ARM), hang(101.0), 0.6)  # pulling up, elbows to ~115 deg...
    f.hold(hang(101.0), 2.0)  # ...and stuck halfway
    f.blend(hang(101.0), hang(2 * HANG_ARM), 0.5)
    rows = fitness_reports(f)
    stuck = [r for t, r, _ in rows if 3.6 < t < 4.9]
    presses = sum(
        1 for a, b in zip(stuck, stuck[1:], strict=False) if "cross" in b.buttons and "cross" not in a.buttons
    )
    assert presses >= 5  # mashing at ~8 Hz
    assert all(r.l2 == 1.0 for r in stuck)  # still holding the triggers


def test_gta5_workout_still_taps():
    prof = load_profile("config/profiles/gta5.yaml")
    assert prof.tier0.workout.style == "tap" and prof.tier0.workout.modes == ["on_foot"]


def swing_pose(height: float) -> np.ndarray:
    """Standing, both hands together on a kettlebell handle: height 0 = at the knees
    (bottom of the swing), 1 = at shoulder height (top)."""
    img, _ = standing()
    knee_y, sh_y = img[W.L_KNEE, 1], img[W.L_SHOULDER, 1]
    y = knee_y + height * (sh_y - knee_y)
    img[W.L_WRIST, :2], img[W.R_WRIST, :2] = (330, y), (310, y)
    img[W.L_ELBOW, :2], img[W.R_ELBOW, :2] = (345, (y + sh_y) / 2), (295, (y + sh_y) / 2)
    return img


def swing_set(n: int = 3) -> Frames:
    f, stand = Frames(), standing()[0]
    f.hold(stand, 1.0)
    f.blend(stand, swing_pose(0.0), 0.5)  # grab it
    f.hold(swing_pose(0.0), 0.8)
    f.reps(swing_pose, 0.0, 1.0, n)  # from the bottom: hands up to the chest and back, 1.6 s each
    f.blend(swing_pose(0.0), stand, 0.5)  # put it down
    f.hold(stand, 2.0)
    return f


def test_kettlebell_swings_count_only_in_fitness_mode():
    f = swing_set(3)
    events, _ = run(f)  # normal play: hands together in front are nothing special
    assert not {"workout_start", "kettlebell"} & {n for n, _ in events}
    rows = fitness_reports(f)
    pressed = [(r, p) for _, r, p in rows if r.l2 > 0]
    assert pressed and gradual([r.l2 for r, _ in pressed])  # down over ~0.5 s, to the end (GTA 6)
    starts = sum(1 for (_, a, _), (_, b, _) in zip(rows, rows[1:], strict=False) if b.l2 >= 0.5 > a.l2)
    assert starts == 3  # one swing up per rep (SOULEVER), held at the top, released going down
    circles = sum(1 for (_, a, _), (_, b, _) in zip(rows, rows[1:], strict=False)
                  if "circle" in b.buttons and "circle" not in a.buttons)  # fmt: skip
    assert circles == 1  # put it down (POSER)


def test_kettlebell_reps_and_tier0_fitness_mode():
    from bodyctl.output.gamepad import DryRunBackend, Gamepad
    from bodyctl.tier0 import Tier0

    prof = load_profile(GTA6)
    app = Tier0(prof, Gamepad(DryRunBackend(), 30, 30), clock=lambda: 0)
    app.engine.set_mode("fitness", 0)
    names = [e.name for p in swing_set(4).poses() for e in app.step(p.t_us, p, t_pose_us=p.t_us).events]
    assert names.count("workout_start") == 1 and names.count("kettlebell") == 4


def test_gta6_walking_off_mid_set_ends_it():
    """Put the kettlebell down and walk out of view: the set ends (circle) and the
    triggers let go, though no frame ever shows you out of the posture."""
    from bodyctl.mapping import BindingEngine
    from bodyctl.output.gamepad import DryRunBackend, Gamepad

    f, stand = Frames(), standing()[0]
    f.hold(stand, 1.0)
    f.blend(stand, swing_pose(0.0), 0.5)
    f.hold(swing_pose(0.0), 0.8)
    f.reps(swing_pose, 0.0, 1.0, 2)
    f.blend(swing_pose(0.0), swing_pose(0.7), 0.3)  # swinging up when the camera loses you
    prof = load_profile(GTA6)
    gp = Gamepad(DryRunBackend(), prof.press.hold_ms, prof.press.release_ms)
    engine = BindingEngine(prof, gp, mode="fitness")
    g = WebcamGestures(prof.gestures, fitness=True)
    names, t = [], 0
    for p in f.poses():
        events = g.update(p)
        engine.apply(g.state, events, p.t_us)
        names += [e.name for e in events]
        t = p.t_us
    assert g.state.workout == "kettlebell" and gp.report.l2 > 0
    circle = False
    for k in range(1, 91):  # 3 s with nobody in view
        t += 33_333
        events = g.update(None, t)
        engine.apply(g.state, events, t)
        names += [e.name for e in events]
        circle |= "circle" in gp.report.buttons
        if k == 30:
            assert g.state.workout == "kettlebell"  # a 1 s dropout carries the set on
    assert names.count("workout_end") == 1 and circle and gp.report.l2 == 0


def edges(rows, button: str) -> int:
    return sum(1 for (_, a, _), (_, b, _) in zip(rows, rows[1:], strict=False)
               if button in b.buttons and button not in a.buttons)  # fmt: skip


def presses(rows) -> int:
    """Real trigger presses (past half pressure)."""
    return sum(1 for (_, a, _), (_, b, _) in zip(rows, rows[1:], strict=False) if b.l2 >= 0.5 > a.l2)


def squat_pose(depth: float) -> np.ndarray:
    """Bar on the back (hands at shoulder height), hips `depth` px lower: the knees bend."""
    img, _ = standing()
    img[: W.L_KNEE, 1] += depth
    img[W.L_WRIST, :2], img[W.R_WRIST, :2] = (395, 150 + depth), (245, 150 + depth)
    img[W.L_ELBOW, :2], img[W.R_ELBOW, :2] = (410, 200 + depth), (230, 200 + depth)
    return img


def test_gta6_squats_unrack_then_press_each_rise():
    f, stand = Frames(), standing()[0]
    f.hold(stand, 1.0)
    f.blend(stand, squat_pose(0), 0.5)  # hands up to the bar
    f.hold(squat_pose(0), 1.0)
    f.reps(squat_pose, 0.0, 70.0, 3, rep_s=2.0)  # down and back up, 3 times
    f.blend(squat_pose(0), stand, 0.5)  # hands down: rack it
    f.hold(stand, 1.5)
    rows = fitness_reports(f, "squat")
    t_start = next(t for t, r, _ in rows if r.l2 > 0.9)
    assert t_start < 2.6  # unracked as the set starts, before the first squat (from 2.5 s)
    assert presses(rows) == 3 + 1  # the unrack, then one press per rise
    assert edges(rows, "circle") == 1  # RANGER
    g = WebcamGestures(load_profile(GTA6).gestures, fitness=True, exercise="squat")
    assert [e.name for p in f.poses() for e in g.update(p)].count("squat") == 3


def deadlift_pose(bend: float) -> np.ndarray:
    """Holding the bar with straight arms: bend 0 = upright, 1 = hinged (shoulders lowered
    90 px toward the hips, bar at the shins)."""
    img, _ = standing()
    d = 90 * bend
    for j in (W.NOSE, W.L_SHOULDER, W.R_SHOULDER):
        img[j, 1] += d
    img[W.L_ELBOW, 1] += 1.5 * d
    img[W.R_ELBOW, 1] += 1.5 * d
    img[W.L_WRIST, 1] += 1.8 * d
    img[W.R_WRIST, 1] += 1.8 * d
    return img


def test_gta6_deadlifts_press_on_the_way_up():
    f, stand = Frames(), standing()[0]
    f.hold(stand, 1.0)
    f.blend(stand, deadlift_pose(1.0), 1.0)  # hinge down to the bar
    f.hold(deadlift_pose(1.0), 1.0)
    f.reps(deadlift_pose, 1.0, 0.0, 3, rep_s=2.0)  # starting at the bottom: up, down
    let_go = deadlift_pose(1.0)
    let_go[[W.L_WRIST, W.R_WRIST], 1] = 160  # bar down at the bottom, hands up off it: POSER
    f.blend(deadlift_pose(1.0), let_go, 0.3)
    f.hold(let_go, 1.0)
    f.blend(let_go, stand, 0.8)
    f.hold(stand, 1.5)
    rows = fitness_reports(f, "deadlift")
    assert presses(rows) == 3
    assert edges(rows, "circle") == 1
    pressed = [(r, p) for _, r, p in rows if r.l2 > 0]
    assert gradual([r.l2 for r, _ in pressed])


def bench_pose(h: float) -> np.ndarray:
    """Lying side-on, arms pressing up: hands h px above the shoulders (150 = locked out)."""
    img, _ = standing()
    pts = {W.NOSE: (170, 395), W.L_SHOULDER: (200, 400), W.R_SHOULDER: (200, 402), W.L_HIP: (350, 410),
           W.R_HIP: (350, 412), W.L_KNEE: (450, 420), W.R_KNEE: (450, 422), W.L_ANKLE: (520, 470),
           W.R_ANKLE: (520, 472), W.L_WRIST: (200, 400 - h), W.R_WRIST: (200, 402 - h),
           W.L_ELBOW: (235, 400 - h / 2), W.R_ELBOW: (235, 402 - h / 2)}  # fmt: skip
    for j, xy in pts.items():
        img[j, :2] = xy
    return img


def test_gta6_bench_unrack_press_up_rack():
    f, stand = Frames(), standing()[0]
    f.hold(stand, 1.0)
    f.blend(stand, bench_pose(150), 1.0)  # lie down, arms up under the bar
    f.hold(bench_pose(150), 1.0)
    f.reps(bench_pose, 150.0, 40.0, 3, rep_s=2.0)
    f.blend(bench_pose(150), stand, 1.0)  # sit up: rack it
    f.hold(stand, 1.5)
    rows = fitness_reports(f, "bench")
    assert presses(rows) == 3 + 1  # unrack, then one press per rep
    assert edges(rows, "circle") == 1


def test_gta6_punching_bag_mashes_square_only_while_hitting():
    f, stand = Frames(), standing()[0]
    guard = stand.copy()
    guard[W.L_WRIST, :2], guard[W.R_WRIST, :2] = (350, 120), (290, 120)
    guard[W.L_ELBOW, :2], guard[W.R_ELBOW, :2] = (380, 200), (260, 200)
    f.hold(guard, 1.5)
    for _ in range(6):  # quick jabs: the left hand shoots out 90 px in 0.1 s and back
        jab = guard.copy()
        jab[W.L_WRIST, :2] = (440, 120)
        f.blend(guard, jab, 0.1)
        f.blend(jab, guard, 0.15)
    f.hold(guard, 2.0)  # still: no hitting
    rows = fitness_reports(f, "bag")
    squares = [t for (_, a, _), (t, b, _) in zip(rows, rows[1:], strict=False)
               if "square" in b.buttons and "square" not in a.buttons]  # fmt: skip
    assert len(squares) >= 6 and all(1.4 < t < 3.3 for t in squares)  # while jabbing (1.5-3.0 s), not after
    assert edges(rows, "circle") == 0 and not any(r.l2 for _, r, _ in rows)  # no rep triggers, no POSER
    assert edges(rows, "cross") == 0  # no reps to struggle on


def test_gta6_fitness_mode_ignores_play_moves():
    from posegen import Timeline

    from bodyctl.output.gamepad import DryRunBackend, Gamepad
    from bodyctl.tier0 import Tier0

    tl = Timeline(8.0)
    tl.run(1.0, 4.0, 3.0)
    tl.jump(5.0)
    tl.punch(6.5)
    prof = load_profile(GTA6)
    app = Tier0(prof, Gamepad(DryRunBackend(), 30, 30), clock=lambda: 0)
    app.engine.set_mode("fitness", 0)
    names = []
    for p in tl.poses():
        names += [e.name for e in app.step(p.t_us, p, t_pose_us=p.t_us).events]
        r = app.gamepad.report
        assert r.left == (0.0, 0.0) and r.right == (0.0, 0.0) and not r.buttons and r.l2 == r.r2 == 0.0
    assert not {"run_start", "jump", "punch"} & set(names)  # the gym: play moves are not even looked for


def test_gta6_pullups_from_behind_on_a_real_recording():
    """Real pull-ups on a doorway bar (RTMPose, 2026-11-26), filmed from behind and below:
    stairs leave no room in front. Seen from there the torso looks long next to the arms,
    and at the top the pose model puts the wrists down by the elbows. The set starts when
    the hands reach the bar (13.5 s), every cued pull-up counts, and letting go ends it."""
    from pathlib import Path

    from bodyctl.output.gamepad import DryRunBackend, Gamepad
    from bodyctl.tier0 import Tier0

    rec = Recording.load(Path(__file__).parent / "fixtures" / "pullup_back.npz")
    app = Tier0(load_profile(GTA6), Gamepad(DryRunBackend(), 30, 30), clock=lambda: 0)
    app.engine.set_mode("fitness", 0)
    app.gestures.exercise = "pullup"
    events = []
    for t, p in zip(rec.t_us, rec.poses(), strict=True):
        events += [(e.name, (t - rec.t_us[0]) / 1e6) for e in app.step(int(t), p, t_pose_us=int(t)).events]
    names = [n for n, _ in events]
    assert names.count("workout_start") == 1 and names.count("workout_end") == 1
    start = next(s for n, s in events if n == "workout_start")
    assert 12.5 < start < 15.0  # hands on the bar at about 13 s
    # Five pull-ups, counted on the video. Letting go folds the elbows like one more.
    reps = [s for n, s in events if n == "pullup"]
    cues = (14.1, 17.6, 21.1, 24.6, 28.1)
    assert len(reps) in (5, 6) and all(c - 1.0 < r < c + 3.5 for c, r in zip(cues, reps, strict=False))


def test_gta6_squats_and_bag_on_a_real_recording():
    """Real drill (RTMPose, 2026-11-27, camera in front): six squats with the hands at the
    shoulders, then six alternating punches at the bag, then moving around with the guard
    down. Every squat counts though the pose reads the hips high for a moment at some tops;
    every punch counts, and arms dropping to the sides afterwards mostly don't."""
    from pathlib import Path

    from bodyctl.tools.webcam_tune import score

    rec = Recording.load(Path(__file__).parent / "fixtures" / "squat_boxing_front.npz")
    scores = score(rec, load_profile(GTA6).gestures).events
    assert (scores["squat"].hits, scores["squat"].expected, scores["squat"].false) == (6, 6, 0)
    assert (scores["bag_hit"].hits, scores["bag_hit"].expected) == (6, 6) and scores["bag_hit"].false <= 2


def test_gta6_kettlebell_on_real_recordings():
    """Real swings (RTMPose, 2026-11-21) from the front and from 45 degrees: one set
    starting in the grab position (hinged, hands at the shins), L2 + R2 pressed on each
    swing up, circle when the hands let go, and nothing from play while in the gym."""
    from pathlib import Path

    from bodyctl.output.gamepad import DryRunBackend, Gamepad
    from bodyctl.tier0 import Tier0

    fixtures = Path(__file__).parent / "fixtures"
    for name, swings in (("front", 12), ("right45", 9)):  # counted on the video, top by top
        rec = Recording.load(fixtures / f"kettlebell_{name}.npz")
        app = Tier0(load_profile(GTA6), Gamepad(DryRunBackend(), 30, 30), clock=lambda: 0)
        app.engine.set_mode("fitness", 0)
        app.gestures.exercise = "kettlebell"
        names, l2, circles, circle = [], [], 0, False
        for t, p in zip(rec.t_us, rec.poses(), strict=True):
            app.clock = lambda t=t: int(t)  # the triggers press down over time
            names += [e.name for e in app.step(int(t), p, t_pose_us=int(t)).events]
            r = app.gamepad.report
            l2.append(r.l2)
            circles += "circle" in r.buttons and not circle
            circle = "circle" in r.buttons
        assert names.count("workout_start") == 1 and names.count("workout_end") == 1, name
        assert names.count("kettlebell") == swings, name
        assert set(names) == {"workout_start", "workout_end", "kettlebell"}, name
        ups = sum(1 for a, b in zip(l2, l2[1:], strict=False) if b >= 0.5 > a)
        assert abs(ups - swings) <= 1, (name, ups)
        assert circles == 1, name

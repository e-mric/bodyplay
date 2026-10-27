"""Guided webcam drill (Tier 0): the screen tells you when to run, jump, crouch,
lean, punch or aim, N times, while every frame's pose landmarks are recorded
together with those cues. `bodyctl webcam-tune` replays the recordings through
the gesture logic and searches the profile's thresholds for the ones that fit
you and your camera. Running the same drill again with the tuned profile is the
test: the drill shows live what was detected and scores itself at the end.

Recordings are .npz files in data/drills/ (git-ignored): per-frame capture time,
image and world landmarks (NaN when no person was found), the cues, and a header.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from bodyctl.gestures import webcam as W
from bodyctl.gestures.webcam import NUM_LANDMARKS, Pose

# Cue label -> (event it expects, seconds after the cue it may arrive). Detections may
# also come up to EARLY_S before the cue: the countdown lets you anticipate.
EXPECT: dict[str, tuple[str, float]] = {
    "run": ("run_start", 2.0),
    "stop": ("run_stop", 3.0),
    "jump": ("jump", 1.5),
    "crouch": ("crouch", 2.0),
    "punch_left": ("punch", 1.2),
    "punch_right": ("punch", 1.2),
    "aim": ("aim_start", 1.5),
    "aim_down": ("aim_end", 1.5),
}
LEAN = {"lean_left": -1, "lean_right": 1}  # sign of lean_deg expected while held
EARLY_S = 0.3
MOVES = ("run", "jump", "crouch", "lean", "punch", "aim")

# Where the camera sits relative to you while you face the screen: yaw in degrees,
# + = camera off to your right. Record drills at several so gestures work from any of them.
ANGLES = {"front": 0.0, "right30": 30.0, "left30": -30.0, "right45": 45.0, "left45": -45.0}
ANGLE_TOL_DEG = 12.0
ROLL_TOL_DEG = 5.0
HEIGHT_RANGE = (0.45, 0.78)  # nose-to-ankle height / frame height; leaves room for raised hands
PITCH_RANGE = (0.85, 1.15)  # image vs 3D torso:leg ratio; outside = camera low or high
NOSE_TO_KNEE = 0.73  # nose-to-knee / nose-to-ankle, average adult proportions

# Landmarks the setup screen looks for. Gestures need all but the feet (knees count
# running steps); the feet only make the distance and camera-height checks exact.
OPTIONAL_PARTS = ("feet",)
FRAMING = {
    "head": (W.NOSE,),
    "shoulders": (W.L_SHOULDER, W.R_SHOULDER),
    "hips": (W.L_HIP, W.R_HIP),
    "knees": (W.L_KNEE, W.R_KNEE),
    "feet": (W.L_ANKLE, W.R_ANKLE),
}


@dataclass
class Step:
    label: str
    text: str
    seconds: float

    @property
    def is_action(self) -> bool:
        return self.label in EXPECT or self.label in LEAN


@dataclass
class Cue:
    label: str
    t_us: int
    end_us: int


def _reps(move: str, reps: int) -> list[Step]:
    s: list[Step] = []
    for i in range(reps):
        if move == "run":
            s += [Step("run", "RUN in place!", 5.0), Step("stop", "STOP - stand still", 4.0)]
        elif move == "jump":
            s += [Step("jump", "JUMP!", 1.5), Step("gap", "stand still", 2.0)]
        elif move == "crouch":
            s += [Step("crouch", "CROUCH and hold", 2.0), Step("gap", "stand up slowly", 2.5)]
        elif move == "lean":
            side = "left" if i % 2 == 0 else "right"
            s += [
                Step(f"lean_{side}", f"LEAN to YOUR {side.upper()} and hold", 2.5),
                Step("center", "back to the middle", 2.0),
            ]
        elif move == "punch":
            side = "right" if i % 2 == 0 else "left"
            s += [
                Step(f"punch_{side}", f"PUNCH with your {side.upper()} arm!", 1.2),
                Step("gap", "guard up, hold still", 2.0),
            ]
        elif move == "aim":
            s += [Step("aim", "AIM and hold", 2.5), Step("aim_down", "arm DOWN", 2.5)]
        else:
            raise ValueError(f"unknown move {move!r}; one of {MOVES}")
    return s


HOW = {
    "run": "RUN in place: lift your knees, alternate legs",
    "jump": "JUMP: small knee bend, push up fast",
    "crouch": "CROUCH: bend your knees and hold",
    "lean": "LEAN: tilt your shoulders sideways, feet planted",
    "punch": "PUNCH: fast and straight ahead, at the screen",
    "aim": "AIM: RIGHT arm straight ahead, at the screen",
}


def build_script(moves: list[str], reps: int) -> list[Step]:
    """Stand still, then each move `reps` times with a heads-up first, then a stretch
    of natural movement with no actions (to count false detections)."""
    s = [Step("still", "Stand still, arms down", 5.0)]
    for m in moves:
        s.append(Step("ready", "Next: " + HOW[m], 4.0))
        s += _reps(m, reps)
    s += [
        Step("shift", "Move naturally: shift your weight, look around. No actions!", 8.0),
        Step("still", "Stand still", 3.0),
    ]
    return s


def cue_windows(cues: list[Cue]) -> list[tuple[str, int, int, Cue]]:
    """(expected event, earliest, latest, cue) for every cue that expects an event."""
    out = []
    for c in cues:
        if c.label in EXPECT:
            name, after_s = EXPECT[c.label]
            out.append((name, c.t_us - int(EARLY_S * 1e6), c.t_us + int(after_s * 1e6), c))
    return out


# --- recording ------------------------------------------------------------------


@dataclass
class Recording:
    header: dict
    t_us: np.ndarray  # (N,) capture times
    img: np.ndarray  # (N, 33, 3) pixel x, y, visibility; NaN = no person
    world: np.ndarray  # (N, 33, 3) metres; NaN = unavailable
    cues: list[Cue]

    def poses(self) -> list[Pose | None]:
        out: list[Pose | None] = []
        for t, im, wo in zip(self.t_us, self.img, self.world, strict=True):
            if np.isnan(im[0, 0]):
                out.append(None)
            else:
                world = None if np.isnan(wo[0, 0]) else wo.astype(float)
                out.append(Pose(int(t), im.astype(float), world))
        return out

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            t_us=self.t_us,
            img=self.img,
            world=self.world,
            header=np.array(json.dumps(self.header)),
            cues=np.array(json.dumps([asdict(c) for c in self.cues])),
        )
        return path

    @classmethod
    def load(cls, path: str | Path) -> Recording:
        with np.load(path, allow_pickle=False) as z:
            return cls(
                header=json.loads(str(z["header"])),
                t_us=z["t_us"],
                img=z["img"],
                world=z["world"],
                cues=[Cue(**c) for c in json.loads(str(z["cues"]))],
            )


class RecordingBuilder:
    def __init__(self, header: dict):
        self.header = header
        self._t: list[int] = []
        self._img: list[np.ndarray] = []
        self._world: list[np.ndarray] = []
        self.cues: list[Cue] = []
        self._nan = np.full((NUM_LANDMARKS, 3), np.nan, np.float32)

    def __len__(self) -> int:
        return len(self._t)

    def add(self, t_us: int, pose: Pose | None) -> None:
        self._t.append(t_us)
        self._img.append(self._nan if pose is None else pose.img.astype(np.float32))
        world = None if pose is None else pose.world
        self._world.append(self._nan if world is None else world.astype(np.float32))

    def cue(self, label: str, t_us: int) -> None:
        if self.cues:
            self.cues[-1].end_us = t_us
        self.cues.append(Cue(label, t_us, t_us))

    def build(self, end_us: int | None = None) -> Recording:
        if self.cues:
            self.cues[-1].end_us = end_us if end_us is not None else (self._t[-1] if self._t else 0)
        shape = (0, NUM_LANDMARKS, 3)
        return Recording(
            self.header,
            np.array(self._t, np.int64),
            np.stack(self._img) if self._img else np.zeros(shape, np.float32),
            np.stack(self._world) if self._world else np.zeros(shape, np.float32),
            self.cues,
        )


def drill_path(out_dir: str | Path, moves: list[str], angle: str = "front") -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(out_dir) / f"drill_{'+'.join(moves)}_{angle}_{stamp}.npz"


def missing_parts(pose: Pose | None, min_visibility: float = 0.5) -> list[str]:
    if pose is None:
        return list(FRAMING)
    return [k for k, idx in FRAMING.items() if any(pose.img[i, 2] < min_visibility for i in idx)]


def required_missing(missing: list[str]) -> list[str]:
    return [m for m in missing if m not in OPTIONAL_PARTS]


# --- camera placement -----------------------------------------------------------


@dataclass
class View:
    yaw_deg: float | None = None  # + = camera off to your right (shoulder line in 3D)
    roll_deg: float | None = None  # body axis tilt from vertical in the image: camera roll
    height_frac: float | None = None  # nose-to-ankle height / frame height
    pitch_ratio: float | None = None  # < 1: camera low, looking up; > 1: high, looking down
    from_knees: bool = False  # feet out of view: height and pitch estimated from the knees


def measure_view(pose: Pose | None, frame_h: int, min_visibility: float = 0.5) -> View:
    v = View()
    if pose is None:
        return v
    xy, vis = pose.img[:, :2], pose.img[:, 2]

    def seen(*idx: int) -> bool:
        return all(vis[i] >= min_visibility for i in idx)

    def mid(p: np.ndarray, a: int, b: int) -> np.ndarray:
        return (p[a] + p[b]) / 2

    if seen(W.L_SHOULDER, W.R_SHOULDER, W.L_HIP, W.R_HIP):
        sh, hip = mid(xy, W.L_SHOULDER, W.R_SHOULDER), mid(xy, W.L_HIP, W.R_HIP)
        # Lowest pair in view: ankles, else knees (heights scaled up to the ankles).
        pair, scale = None, 1.0
        if seen(W.L_ANKLE, W.R_ANKLE):
            pair = (W.L_ANKLE, W.R_ANKLE)
        elif seen(W.L_KNEE, W.R_KNEE):
            pair, scale, v.from_knees = (W.L_KNEE, W.R_KNEE), 1 / NOSE_TO_KNEE, True
        low = mid(xy, *pair) if pair else hip
        axis = sh - low
        v.roll_deg = float(np.degrees(np.arctan2(axis[0], -axis[1])))
        if pair and seen(W.NOSE):
            v.height_frac = float(scale * (max(xy[pair[0], 1], xy[pair[1], 1]) - xy[W.NOSE, 1]) / frame_h)
        if pair and pose.world is not None:
            w = pose.world
            torso_w = np.linalg.norm(mid(w, W.L_SHOULDER, W.R_SHOULDER) - mid(w, W.L_HIP, W.R_HIP))
            legs_w = np.linalg.norm(mid(w, W.L_HIP, W.R_HIP) - mid(w, *pair))
            torso_i, legs_i = np.linalg.norm(sh - hip), np.linalg.norm(hip - low)
            if min(torso_w, legs_w, torso_i, legs_i) > 1e-6:
                v.pitch_ratio = float((torso_i / legs_i) / (torso_w / legs_w))
    if pose.world is not None and seen(W.L_SHOULDER, W.R_SHOULDER):
        s = pose.world[W.L_SHOULDER] - pose.world[W.R_SHOULDER]
        # Turning to your left brings your right shoulder toward the camera (z negative):
        # the camera is then off to your right.
        v.yaw_deg = float(np.degrees(np.arctan2(s[2], s[0])))
    return v


def summarize_views(views: list[View]) -> dict[str, float | None]:
    """Median of each field over frames where you stood still (stored in the header)."""
    out: dict[str, float | None] = {}
    for name in ("yaw_deg", "roll_deg", "height_frac", "pitch_ratio"):
        vals = [getattr(v, name) for v in views if getattr(v, name) is not None]
        out[name] = round(float(np.median(vals)), 3) if vals else None
    return out


class ViewSmoother:
    """Exponential average per field, so the on-screen guidance doesn't flicker."""

    def __init__(self, alpha: float = 0.15):
        self.alpha = alpha
        self.view = View()

    def update(self, v: View) -> View:
        for name in ("yaw_deg", "roll_deg", "height_frac", "pitch_ratio"):
            new, old = getattr(v, name), getattr(self.view, name)
            if new is not None:
                setattr(self.view, name, new if old is None else old + self.alpha * (new - old))
        self.view.from_knees = v.from_knees
        return self.view


def describe_yaw(yaw: float) -> str:
    if abs(yaw) < 5:
        return "camera straight ahead"
    return f"camera {abs(yaw):.0f} deg to your {'right' if yaw > 0 else 'left'}"


def view_checks(view: View, missing: list[str], target_yaw: float) -> tuple[list[tuple[str, bool]], bool]:
    """Guidance lines (text, ok) and whether the placement is good enough to start."""
    need = required_missing(missing)
    if need:
        # The other readings are noise until the body is in view: show just this.
        return [("not in view: " + ", ".join(need), False)], False
    lines = [("whole body in view" if not missing else "head to knees in view (feet optional)", True)]
    ready = True
    lo, hi = HEIGHT_RANGE
    h = view.height_frac
    if h is not None:
        ok = lo <= h <= hi
        hint = "distance ok" if ok else ("step back" if h > hi else "come closer")
        how = "estimated from your knees" if view.from_knees else f"you fill {h:.0%} of the height"
        lines.append((f"{hint} ({how})", ok))
        ready &= ok
    if view.roll_deg is not None:
        ok = abs(view.roll_deg) <= ROLL_TOL_DEG
        r = view.roll_deg
        text = f"camera level ({r:+.0f} deg)" if ok else f"camera tilted {r:+.0f} deg: straighten it"
        lines.append((text, ok))
        ready &= ok
    if view.yaw_deg is not None:
        ok = abs(view.yaw_deg - target_yaw) <= ANGLE_TOL_DEG
        now = describe_yaw(view.yaw_deg)
        lines.append((f"angle ok: {now}" if ok else f"{now} - want {describe_yaw(target_yaw)}", ok))
        ready &= ok
    p = view.pitch_ratio
    if p is not None and not PITCH_RANGE[0] <= p <= PITCH_RANGE[1]:
        where = "low, looking up" if p < PITCH_RANGE[0] else "high, looking down"
        lines.append((f"tip: camera is {where}; chest height works best", False))
    return lines, ready


# --- live drill -------------------------------------------------------------------


class _Beeper:
    """Short tone at each action cue; silently off if there's no audio device."""

    def __init__(self, enabled: bool):
        self._sd = None
        if not enabled:
            return
        try:
            import sounddevice as sd

            self._sr = 44100
            t = np.arange(int(0.12 * self._sr)) / self._sr
            self._tone = (0.3 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
            self._sd = sd
        except Exception:
            self._sd = None

    def __call__(self) -> None:
        if self._sd is None:
            return
        try:
            self._sd.play(self._tone, self._sr)
        except Exception:
            self._sd = None


def run_drill(
    moves: list[str],
    reps: int,
    profile_path: str | Path,
    camera_index: int = 0,
    size: tuple[int, int] = (640, 480),
    fps: int = 30,
    model: str = "lite",
    out_dir: str | Path = "data/drills",
    video: bool = False,
    sound: bool = True,
    angle: str = "front",
) -> Path | None:
    import cv2

    from bodyctl.config.profile import load_profile
    from bodyctl.gestures.webcam import WebcamGestures
    from bodyctl.sensors.webcam_pose import Camera, PoseEstimator, ensure_model
    from bodyctl.tools import drill_view as V
    from bodyctl.tools.webcam_tune import format_scores, match_cues, score

    prof = load_profile(profile_path)
    script = build_script(moves, reps)
    target_yaw = ANGLES[angle]
    path = drill_path(out_dir, moves, angle)
    estimator = PoseEstimator(ensure_model(model))
    cam = Camera(camera_index, size[0], size[1], fps)
    rec = RecordingBuilder(
        {
            "source": "webcam-drill",
            "moves": moves,
            "reps": reps,
            "angle": angle,
            "target_yaw_deg": target_yaw,
            "camera": {"index": camera_index, "size": list(cam.size), "fps": cam.fps},
            "model": model,
            "profile": Path(profile_path).as_posix(),
            "created": datetime.now().isoformat(timespec="seconds"),
            "complete": False,
        }
    )
    gestures = WebcamGestures(prof.gestures)
    beep = _Beeper(sound)
    writer = None
    if video:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(path.with_suffix(".mp4")), fourcc, cam.fps or fps, cam.size)
    window = "bodyplay drill"
    V.open_window(window)
    show_skeleton = True
    hud = V.Hud(size=cam.size)
    quality: deque[float] = deque(maxlen=50)
    last_t: int | None = None
    total_s = sum(s.seconds for s in script)
    print(f"drill: {', '.join(moves)} x{reps} ({total_s:.0f} s), profile {profile_path}")
    print("in the drill window: q quits (the recording so far is kept), f fullscreen, s skeleton")

    views: list[View] = []

    def frame() -> tuple[int, np.ndarray, Pose | None]:
        nonlocal last_t
        t, f = cam.read()
        pose = estimator.detect(f, t)
        if last_t is not None and t > last_t:
            hud.fps += 0.1 * (1e6 / (t - last_t) - hud.fps)
        last_t = t
        hud.parts = V.part_visibility(pose)
        quality.append(V.frame_quality(hud.parts))
        hud.quality = quality
        return t, f, pose

    def keys(img: np.ndarray) -> int:
        nonlocal show_skeleton
        key = V.show(window, img)
        if key == ord("f"):
            V.toggle_fullscreen(window)
        elif key == ord("s"):
            show_skeleton = not show_skeleton
        return key

    try:
        # 1. Camera placement: guide until everything checks out for 2 s, then count down.
        smooth = ViewSmoother()
        good_since = None
        countdown_from = None
        while True:
            t, f, pose = frame()
            view = smooth.update(measure_view(pose, f.shape[0]))
            checks, ready = view_checks(view, missing_parts(pose), target_yaw)
            if not ready:
                good_since = countdown_from = None
                title = f"Camera setup: {describe_yaw(target_yaw)}"
                style = "setup"
            else:
                good_since = good_since or t
                if countdown_from is None and t - good_since > 2e6:
                    countdown_from = t
                title = "Good - face the screen, hold still"
                style = "ready"
            if countdown_from is not None:
                left = 3 - (t - countdown_from) / 1e6
                if left <= 0:
                    break
                title = f"Starting in {left:.0f}..."
            sub = "move the camera, not your feet: always face the screen   (space: start anyway)"
            panel = V.Panel("CAMERA SETUP", checks=checks)
            key = keys(V.render(f, pose, title, sub, style, None, panel, hud, show_skeleton))
            if key == ord("q"):
                return None
            if key == ord(" "):
                break

        # 2. The drill itself, recorded.
        detections: list[tuple[int, str, bool]] = []
        hud.recording = True
        started: int | None = None
        i, t0, step_end = -1, 0, 0
        expected: set[str] = set()
        while True:
            t, f, pose = frame()
            if t >= step_end:
                i += 1
                if i == len(script):
                    break
                step = script[i]
                t0, step_end = t, t + int(step.seconds * 1e6)
                rec.cue(step.label, t0)
                if step.is_action:
                    beep()
                expected = {EXPECT[step.label][0]} if step.label in EXPECT else set()
                if i and script[i - 1].label in EXPECT:
                    expected.add(EXPECT[script[i - 1].label][0])  # its window runs into this step
                nxt = script[i + 1].text if i + 1 < len(script) else "done"
            rec.add(t, pose)
            started = started or t
            hud.elapsed_s, hud.frames = (t - started) / 1e6, len(rec)
            if step.label in ("still", "gap", "center", "ready"):
                views.append(measure_view(pose, f.shape[0]))
            if writer is not None:
                writer.write(f)
            for e in gestures.update(pose):
                if e.name != "step":
                    detections.append((t, e.name, e.name in expected))
            missing = required_missing(missing_parts(pose))
            sub = f"next: {nxt}" + (f"   OUT OF VIEW: {', '.join(missing)}" if missing else "")
            style = "action" if step.is_action else "idle"
            progress = (i + (t - t0) / (step.seconds * 1e6)) / len(script)
            panel = V.Panel(
                rep_header(script, i),
                state=gestures.state,
                events=[((t - td) / 1e6, n, ok) for (td, n, ok) in reversed(detections)],
                tally=match_cues(rec.cues, [(td, n) for (td, n, _) in detections]),
            )
            key = keys(V.render(f, pose, step.text, sub, style, progress, panel, hud, show_skeleton))
            if key == ord("q"):
                raise KeyboardInterrupt
        rec.header["complete"] = True
    except KeyboardInterrupt:
        print("stopped early")
    finally:
        if writer is not None:
            writer.release()
        cam.close()
        estimator.close()

    if not len(rec):
        cv2.destroyAllWindows()
        return None
    recording = rec.build()
    recording.header["frames"] = len(rec)
    recording.header["view"] = summarize_views(views)
    recording.save(path)
    report = format_scores(score(recording, prof.gestures))
    print(report)
    print(f"saved {len(rec)} frames, {len(recording.cues)} cues -> {path}")
    V.show(window, V.report_image(report, recording.header["complete"]), 15000)
    cv2.destroyAllWindows()
    return path


START_LABELS = ("run", "jump", "crouch", "lean_left", "lean_right", "punch_left", "punch_right", "aim")


def rep_header(script: list[Step], i: int) -> str:
    total = sum(st.label in START_LABELS for st in script)
    done = sum(st.label in START_LABELS for st in script[: i + 1])
    if done == 0:
        return "GET READY"
    if done == total and not script[i].is_action and script[i].label not in EXPECT:
        return "ALMOST DONE"
    return f"REP {done} / {total}"

"""Score webcam drill recordings against their cues, and search the Tier 0 gesture
thresholds that fit them (`bodyctl webcam-tune`).

A detection counts as a hit when it lands in its cue's window (see drill.EXPECT);
every other detection is a false one. The search tunes one group of thresholds at
a time on all recordings together, maximising per-move F1 (see move_score), and writes the
result to a local profile (data/profiles/, git-ignored) so the committed profile
stays untouched until values are proven.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

from bodyctl.config.profile import Gestures, LeanParams, Profile
from bodyctl.gestures import webcam as W
from bodyctl.gestures.webcam import Pose, WebcamGestures, deadzone_map
from bodyctl.tools.drill import FLY, LEAN, POINTS, SPEEDS, Cue, Recording, cue_windows

BASE_PROFILE = Path("config/profiles/gta5.yaml")
TUNED_DIR = Path("data/profiles")
DRILLS_DIR = Path("data/drills")

# Tuned in this order, each group starting from the best values so far: (events it is
# scored on, grid). Aim ignores a moving arm (punch_speed / 4), so punch also scores aim.
# Smoothing comes first and is scored on every event (None): it changes all of them.
# Its score also loses F1_PER_100MS for every 100 ms of median detection delay, so
# heavier smoothing has to remove false detections to be worth its lag.
F1_PER_100MS = 0.03
GROUPS: dict[str, tuple[tuple[str, ...] | None, dict[str, list[float]]]] = {
    "smooth": (
        None,
        {
            "webcam.smooth_min_cutoff_hz": [0.0, 0.5, 1.0, 2.0, 4.0],
            "webcam.smooth_beta": [0.0, 0.1, 0.3, 1.0],
        },
    ),
    "run": (("run_start", "run_stop"), {"webcam.knee_lift": [0.05, 0.07, 0.09, 0.12, 0.15, 0.18, 0.22]}),
    "jump": (
        ("jump", "crouch", "stand"),
        {
            "webcam.jump_dip": [0.03, 0.04, 0.05, 0.07, 0.09],
            "webcam.jump_rise_speed": [0.5, 0.65, 0.8, 1.0, 1.3],
            "jump.crouch_hold_ms": [300, 450, 600],
        },
    ),
    "punch": (
        ("punch", "aim_start", "aim_end"),
        {
            "webcam.punch_speed": [2.0, 2.5, 3.0, 4.0, 5.0, 6.0],
            "webcam.punch_elbow_deg": [100, 120, 140, 155],
        },
    ),
    "aim": (
        ("aim_start", "aim_end"),
        {
            "webcam.aim_forward": [0.2, 0.3, 0.4, 0.5],
            "webcam.aim_height": [0.25, 0.35, 0.5],
            "webcam.aim_hold_ms": [80, 120, 200],
            "webcam.aim_release_ms": [0, 200, 400],
        },
    ),
    "shoot": (
        ("shoot",),
        {"webcam.shoot_flick_speed": [0.8, 1.0, 1.5, 2.0, 3.0], "webcam.shoot_min_conf": [0.3, 0.5, 0.7]},
    ),
    "fly": (
        ("fly_start", "fly_end"),
        {"webcam.fly_span": [1.5, 2.0, 2.5, 3.0], "webcam.fly_hold_ms": [150, 300, 500]},
    ),
    "wheel": (
        ("wheel_open", "wheel_close"),
        {"webcam.wheel_reach": [0.1, 0.2, 0.3, 0.45], "webcam.wheel_hold_ms": [80, 150, 250]},
    ),
}
LEAN_SETTLE_US = 700_000  # ignore the first 0.7 s of a lean or a return to the middle


def pose_family(label: str) -> str:
    """rtmpose-m -> rtmpose, mediapipe-lite -> mediapipe: thresholds are tuned per model family."""
    return label.split("-")[0]


def recording_pose(rec: Recording) -> str:
    """Pose model a drill was recorded with (drills before --pose existed used MediaPipe)."""
    return rec.header.get("pose") or f"mediapipe-{rec.header.get('model', 'lite')}"


def tuned_profile(pose: str) -> Path:
    return TUNED_DIR / f"gta5_tuned_{pose_family(pose)}.yaml"


def default_profile(pose: str = "mediapipe") -> Path:
    tuned = tuned_profile(pose)
    return tuned if tuned.exists() else BASE_PROFILE


# --- parameters -------------------------------------------------------------------


def get_param(g: Gestures, key: str) -> float:
    """`section.name` inside gestures, e.g. webcam.knee_lift or jump.crouch_hold_ms."""
    sec, _, name = key.partition(".")
    value = getattr(getattr(g, sec, None), name, None)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise KeyError(key)
    return value


def set_params(g: Gestures, values: dict[str, float]) -> Gestures:
    by_section: dict[str, dict[str, float]] = {}
    for key, v in values.items():
        cur = get_param(g, key)
        sec, _, name = key.partition(".")
        by_section.setdefault(sec, {})[name] = type(cur)(v)
    return g.model_copy(
        update={sec: getattr(g, sec).model_copy(update=vals) for sec, vals in by_section.items()}
    )


# --- replay and scoring -------------------------------------------------------------


@dataclass
class Replay:
    events: list[tuple[int, str]]  # (frame time, name), steps included
    lean_deg: np.ndarray  # per frame; NaN when the person isn't visible
    cadence_hz: np.ndarray  # per frame; 0 when not running
    run_stick: np.ndarray  # per frame, 0..1
    sprinting: np.ndarray  # per frame, bool
    wheel: np.ndarray  # per frame (open, x, y): weapon wheel and where it points
    fly: np.ndarray  # per frame (flying, roll, pitch)


def replay(rec: Recording, gestures: Gestures, poses: list[Pose | None] | None = None) -> Replay:
    poses = poses if poses is not None else rec.poses()
    g = WebcamGestures(gestures)
    events: list[tuple[int, str]] = []
    n = len(poses)
    lean = np.full(n, np.nan)
    cadence, stick, sprint = np.zeros(n), np.zeros(n), np.zeros(n, bool)
    wheel, fly = np.zeros((n, 3)), np.zeros((n, 3))
    for i, (t, p) in enumerate(zip(rec.t_us, poses, strict=True)):
        events.extend((int(t), e.name) for e in g.update(p))
        st = g.state
        if st.visible:
            lean[i] = st.lean_deg
        cadence[i], stick[i], sprint[i] = st.cadence_hz, st.run_stick, st.sprinting
        wheel[i] = (st.wheel_open, st.wheel_x, st.wheel_y)
        fly[i] = (st.flying, st.fly_roll, st.fly_pitch)
    return Replay(events, lean, cadence, stick, sprint, wheel, fly)


@dataclass
class EventScore:
    expected: int = 0
    hits: int = 0
    false: int = 0
    delays_ms: list[float] = field(default_factory=list)

    def add(self, other: EventScore) -> None:
        self.expected += other.expected
        self.hits += other.hits
        self.false += other.false
        self.delays_ms += other.delays_ms


def match(rec: Recording, events: list[tuple[int, str]]) -> dict[str, EventScore]:
    return match_cues(rec.cues, events)


def match_cues(cues: list[Cue], events: list[tuple[int, str]]) -> dict[str, EventScore]:
    """Hits per cue window, everything else false (the drill also uses this live)."""
    windows = cue_windows(cues)
    used = [False] * len(windows)
    out: dict[str, EventScore] = {}
    for name, *_ in windows:
        out.setdefault(name, EventScore()).expected += 1
    for t, name in events:
        if name == "step":
            continue
        s = out.setdefault(name, EventScore())
        for k, (wname, lo, hi, cue) in enumerate(windows):
            if not used[k] and wname == name and lo <= t <= hi:
                used[k] = True
                s.hits += 1
                s.delays_ms.append((t - cue.t_us) / 1000)
                break
        else:
            s.false += 1
    return out


@dataclass
class LeanStats:
    hold_deg: float  # median lean toward the cued side while holding (negative = reversed)
    neutral_deg: float  # 98th percentile of |lean| while standing straight
    hold_ok: float  # share of hold frames at half stick or more, on the right side
    neutral_ok: float  # share of standing-straight frames at zero stick


def lean_stats(recs: list[Recording], replays: list[Replay], params: LeanParams) -> LeanStats | None:
    hold, neutral = [], []
    for rec, rp in zip(recs, replays, strict=True):
        ok = ~np.isnan(rp.lean_deg)
        for c in rec.cues:
            sel = ok & (rec.t_us >= c.t_us + LEAN_SETTLE_US) & (rec.t_us < c.end_us)
            if c.label in LEAN:
                hold.append(LEAN[c.label] * rp.lean_deg[sel])
            elif c.label in ("still", "center"):
                neutral.append(np.abs(rp.lean_deg[sel]))
    hold_a = np.concatenate(hold) if hold else np.zeros(0)
    if not len(hold_a):
        return None
    neutral_a = np.concatenate(neutral) if neutral else np.zeros(0)

    def stick(d: np.ndarray) -> np.ndarray:
        return np.array([deadzone_map(x, params.deadzone_deg, params.full_deg) for x in d])

    return LeanStats(
        hold_deg=float(np.median(hold_a)),
        neutral_deg=float(np.percentile(neutral_a, 98)) if len(neutral_a) else math.nan,
        hold_ok=float(np.mean(stick(hold_a) >= 0.5)),
        neutral_ok=float(np.mean(stick(neutral_a) == 0)) if len(neutral_a) else math.nan,
    )


LEAN_MAX_SWAY_DEG = 15.0  # standing "sway" above this is measurement noise, not posture


def suggest_lean(st: LeanStats | None, current: LeanParams) -> dict[str, float]:
    """Deadzone just above standing sway, full deflection at 80 % of a held lean. No
    suggestion when lean isn't measurable (reversed, or noise beyond LEAN_MAX_SWAY_DEG,
    e.g. shoulders overlapping with the camera off to one side)."""
    if st is None or st.hold_deg <= 0 or st.neutral_deg > LEAN_MAX_SWAY_DEG:
        return {}
    dz = float(math.ceil(st.neutral_deg + 1)) if not math.isnan(st.neutral_deg) else current.deadzone_deg
    full = float(max(dz + 4, round(0.8 * st.hold_deg)))
    return {"lean.deadzone_deg": dz, "lean.full_deg": full}


SPEED_SETTLE_US = 1_500_000  # cadence needs a few steps before it means anything
# Stick range that counts as the right speed (sprint: the sprint taps must run).
SPEED_BAND = {"walk": (0.25, 0.75), "jog": (0.85, 1.0)}


@dataclass
class SpeedStats:
    cadence_hz: dict[str, float]  # median steps/s while moving, per speed cue
    on_target: dict[str, float]  # share of frames at the right speed


def speed_stats(recs: list[Recording], replays: list[Replay]) -> SpeedStats | None:
    cad: dict[str, list[np.ndarray]] = {k: [] for k in SPEEDS}
    ok: dict[str, list[np.ndarray]] = {k: [] for k in SPEEDS}
    for rec, rp in zip(recs, replays, strict=True):
        for c in rec.cues:
            if c.label not in SPEEDS:
                continue
            sel = (rec.t_us >= c.t_us + SPEED_SETTLE_US) & (rec.t_us < c.end_us)
            moving = sel & (rp.cadence_hz > 0)
            cad[c.label].append(rp.cadence_hz[moving])
            if c.label == "sprint":
                ok[c.label].append(rp.sprinting[sel])
            else:
                lo, hi = SPEED_BAND[c.label]
                ok[c.label].append((rp.run_stick[sel] >= lo) & (rp.run_stick[sel] <= hi) & ~rp.sprinting[sel])
    seen = [k for k in SPEEDS if ok[k]]
    if not seen:
        return None

    def med(xs: list[np.ndarray]) -> float:
        a = np.concatenate(xs) if xs else np.zeros(0)
        return float(np.median(a)) if len(a) else math.nan

    return SpeedStats(
        cadence_hz={k: med(cad[k]) for k in seen},
        on_target={k: float(np.mean(np.concatenate(ok[k]))) if ok[k] else math.nan for k in seen},
    )


def suggest_cadence(st: SpeedStats | None, current: list[float]) -> list[float] | None:
    """cadence_curve from your own pace: half stick at your walk, full at your jog, sprint
    halfway between jog and sprint. None unless all three were measured in that order."""
    if st is None or any(k not in st.cadence_hz or math.isnan(st.cadence_hz[k]) for k in SPEEDS):
        return None
    walk, jog, sprint = (st.cadence_hz[k] for k in SPEEDS)
    if not walk < jog < sprint or jog - walk < 0.3:
        return None
    curve = [round(walk, 2), round(jog, 2), round((jog + sprint) / 2, 2)]
    return None if curve == list(current) else curve


POINT_SETTLE_US = 800_000  # time to swing the arm to the cued direction
POINT_TOL_DEG = 22.5  # half a slot of an 8-slot wheel


def wheel_stats(recs: list[Recording], replays: list[Replay]) -> dict[str, float] | None:
    """Share of frames, per cued direction, with the wheel open and the left hand within
    half a slot of the cue."""
    hits: dict[str, list[np.ndarray]] = {}
    for rec, rp in zip(recs, replays, strict=True):
        for c in rec.cues:
            if c.label not in POINTS:
                continue
            sel = (rec.t_us >= c.t_us + POINT_SETTLE_US) & (rec.t_us < c.end_us)
            open_, x, y = rp.wheel[sel, 0] > 0, rp.wheel[sel, 1], rp.wheel[sel, 2]
            err = (np.degrees(np.arctan2(y, x)) - POINTS[c.label] + 180) % 360 - 180
            pointing = (x != 0) | (y != 0)
            hits.setdefault(c.label, []).append(open_ & pointing & (np.abs(err) <= POINT_TOL_DEG))
    if not hits:
        return None
    return {k: float(np.mean(np.concatenate(v))) for k, v in hits.items()}


FLY_SETTLE_US = 700_000


def fly_stats(recs: list[Recording], replays: list[Replay]) -> dict[str, float] | None:
    """Share of frames, per flight cue, flying with that axis at half stick or more the
    cued way."""
    hits: dict[str, list[np.ndarray]] = {}
    for rec, rp in zip(recs, replays, strict=True):
        for c in rec.cues:
            if c.label not in FLY:
                continue
            axis, sign = FLY[c.label]
            sel = (rec.t_us >= c.t_us + FLY_SETTLE_US) & (rec.t_us < c.end_us)
            value = rp.fly[sel, 1 if axis == "roll" else 2]
            hits.setdefault(c.label, []).append((rp.fly[sel, 0] > 0) & (sign * value >= 0.5))
    if not hits:
        return None
    return {k: float(np.mean(np.concatenate(v))) for k, v in hits.items()}


@dataclass
class Score:
    events: dict[str, EventScore]
    lean: LeanStats | None
    steps_per_s: float | None  # step rate inside run cues
    speed: SpeedStats | None  # walk / jog / sprint cues
    wheel: dict[str, float] | None  # weapon wheel: on-target share per direction
    fly: dict[str, float] | None  # flight: on-target share per control
    no_person: float  # share of frames
    feet_hidden: float  # share of frames with a person but no ankles


def _as_list(recs: Recording | list[Recording]) -> list[Recording]:
    return [recs] if isinstance(recs, Recording) else recs


def score_events(
    recs: list[Recording], poses: list[list[Pose | None]], gestures: Gestures
) -> dict[str, EventScore]:
    total: dict[str, EventScore] = {}
    for rec, ps in zip(recs, poses, strict=True):
        for name, s in match(rec, replay(rec, gestures, ps).events).items():
            total.setdefault(name, EventScore()).add(s)
    return total


def score(recs: Recording | list[Recording], gestures: Gestures) -> Score:
    recs = _as_list(recs)
    replays = [replay(r, gestures) for r in recs]
    total: dict[str, EventScore] = {}
    steps, run_s = 0, 0.0
    frames = no_person = feet_hidden = 0
    for rec, rp in zip(recs, replays, strict=True):
        for name, s in match(rec, rp.events).items():
            total.setdefault(name, EventScore()).add(s)
        for c in rec.cues:
            if c.label == "run":
                run_s += (c.end_us - c.t_us) / 1e6
                steps += sum(1 for t, n in rp.events if n == "step" and c.t_us <= t <= c.end_us)
        frames += len(rec.t_us)
        person = ~np.isnan(rec.img[:, 0, 0])
        no_person += int((~person).sum())
        ankles = rec.img[person][:, [W.L_ANKLE, W.R_ANKLE], 2]
        feet_hidden += int((ankles.min(axis=1) < gestures.webcam.min_visibility).sum()) if len(ankles) else 0
    return Score(
        events=total,
        lean=lean_stats(recs, replays, gestures.lean),
        speed=speed_stats(recs, replays),
        wheel=wheel_stats(recs, replays),
        fly=fly_stats(recs, replays),
        steps_per_s=steps / run_s if run_s else None,
        no_person=no_person / frames if frames else 0.0,
        feet_hidden=feet_hidden / frames if frames else 0.0,
    )


def format_scores(sc: Score) -> str:
    lines = [f"{'event':<11}{'hits':>8}{'false':>7}   after cue (p50)"]
    for name in sorted(sc.events, key=lambda n: (-sc.events[n].expected, n)):
        s = sc.events[name]
        hits = f"{s.hits}/{s.expected}" if s.expected else "-"
        delay = f"{np.median(s.delays_ms):.0f} ms" if s.delays_ms else ""
        lines.append(f"{name:<11}{hits:>8}{s.false:>7}   {delay}")
    if sc.steps_per_s is not None:
        lines.append(f"steps while running: {sc.steps_per_s:.1f}/s")
    if sc.speed is not None:
        parts = [
            f"{k} {sc.speed.cadence_hz[k]:.1f}/s ({sc.speed.on_target[k]:.0%} at {k} speed)"
            for k in sc.speed.cadence_hz
        ]
        lines.append("speed: " + ", ".join(parts))
    if sc.wheel:
        mean = float(np.mean(list(sc.wheel.values())))
        worst = min(sc.wheel, key=sc.wheel.get)
        where = worst.removeprefix("point_")
        lines.append(f"wheel pointing: {mean:.0%} on target (worst: {where} {sc.wheel[worst]:.0%})")
    if sc.fly:
        lines.append("flight: " + ", ".join(f"{k.replace('_', ' ')} {v:.0%}" for k, v in sc.fly.items()))
    if sc.lean is not None:
        lines.append(
            f"lean: held {sc.lean.hold_deg:+.1f} deg ({sc.lean.hold_ok:.0%} at half stick+), "
            f"straight {sc.lean.neutral_deg:.1f} deg p98 ({sc.lean.neutral_ok:.0%} at zero)"
        )
        if sc.lean.hold_deg < 0:
            lines.append("lean is REVERSED: is the camera image mirrored?")
        elif sc.lean.neutral_deg > LEAN_MAX_SWAY_DEG:
            lines.append("lean is unreliable here (noisy shoulder line): lean thresholds left unchanged")
    lines.append(f"framing: no person {sc.no_person:.0%} of frames, feet hidden {sc.feet_hidden:.0%}")
    return "\n".join(lines)


def format_per_angle(recs: list[Recording], gestures: Gestures) -> str:
    """One line per camera angle, to show which placements still need work."""
    by_angle: dict[str, list[Recording]] = {}
    for r in recs:
        by_angle.setdefault(r.header.get("angle", "front"), []).append(r)
    lines = []
    for angle, rs in sorted(by_angle.items()):
        ev = score(rs, gestures).events.values()
        hits, n, false = sum(s.hits for s in ev), sum(s.expected for s in ev), sum(s.false for s in ev)
        lines.append(f"  {angle:<8} {hits}/{n} hits, {false} false  ({len(rs)} recording(s))")
    return "\n".join(lines)


# --- search -------------------------------------------------------------------------


@dataclass
class GroupResult:
    group: str
    before: tuple[int, int, int]  # hits, expected, false
    after: tuple[int, int, int]
    changes: dict[str, tuple[float, float]]  # key -> (old, new)


def f1(hits: int, expected: int, false: int) -> float:
    """Harmonic mean of recall (hits / expected) and precision (hits / all detections):
    2 hits / (expected + hits + false). Detecting nothing scores 0, so it never wins."""
    total = expected + hits + false
    return 2 * hits / total if total else 0.0


def move_score(sc: dict[str, EventScore], events: tuple[str, ...] | None) -> float:
    """Mean F1 over the cued moves in `events` (None = all), so every move counts the same:
    trading one move away for fewer false detections elsewhere doesn't pay. False
    detections of uncued events (e.g. crouch in a jump drill) are shared out among them."""
    names = list(sc) if events is None else [e for e in events if e in sc]
    cued = [e for e in names if sc[e].expected]
    if not cued:
        return 0.0
    share = sum(sc[e].false for e in names if not sc[e].expected) / len(cued)
    return float(np.mean([f1(sc[e].hits, sc[e].expected, sc[e].false + share) for e in cued]))


def search(
    recs: list[Recording], gestures: Gestures, progress: Callable[[str], None] = print, passes: int = 2
) -> tuple[Gestures, list[GroupResult]]:
    """Tune one group at a time, in GROUPS order. A second pass lets early groups (above
    all smoothing) adapt to the thresholds the later ones settled on."""
    poses = [r.poses() for r in recs]
    cued = {w[0] for r in recs for w in cue_windows(r.cues)}
    best = gestures
    results: list[GroupResult] = []
    for n_pass in range(1, passes + 1):
        for group, (events, grid) in GROUPS.items():
            if events is not None and not cued & set(events):
                continue
            lag = events is None  # the smoothing group also pays for added delay

            def objective(g: Gestures, events=events, lag=lag) -> tuple[int, int, int, float]:
                """(hits, expected, false) for the report, and the score to maximise."""
                sc = score_events(recs, poses, g)
                pick = list(sc.values()) if events is None else [sc[e] for e in events if e in sc]
                delays = [d for s in pick for d in s.delays_ms]
                penalty = F1_PER_100MS * float(np.median(delays)) / 100 if lag and delays else 0.0
                return (
                    sum(s.hits for s in pick),
                    sum(s.expected for s in pick),
                    sum(s.false for s in pick),
                    move_score(sc, events) - penalty,
                )

            keys = list(grid)
            current = {k: get_param(best, k) for k in keys}
            h0, n0, f0, score0 = objective(best)
            before = (h0, n0, f0)
            top = ((score0, -f0, 0.0), current, before)
            combos = list(itertools.product(*(grid[k] for k in keys)))
            progress(f"tuning {group} (pass {n_pass}/{passes}): {len(combos)} combinations")
            for combo in combos:
                vals = dict(zip(keys, combo, strict=True))
                h, n, f, score_ = objective(set_params(best, vals))
                dist = sum(abs(v - current[k]) / (max(grid[k]) - min(grid[k])) for k, v in vals.items())
                cand = ((score_, -f, -dist), vals, (h, n, f))
                if cand[0] > top[0]:
                    top = cand
            best = set_params(best, top[1])
            changes = {k: (current[k], get_param(best, k)) for k in keys if get_param(best, k) != current[k]}
            name = group if n_pass == 1 else f"{group} (pass {n_pass})"
            results.append(GroupResult(name, before, top[2], changes))
    return best, results


def format_search(results: list[GroupResult], lean: dict[str, tuple[float, float]]) -> str:
    lines = []
    for r in results:
        b, a = r.before, r.after
        lines.append(f"{r.group}: {b[0]}/{b[1]} hits {b[2]} false -> {a[0]}/{a[1]} hits {a[2]} false")
        for k, (old, new) in r.changes.items():
            lines.append(f"  {k}: {old:g} -> {new:g}")
        if not r.changes:
            lines.append("  (current values are already the best found)")
    if lean:
        lines.append("lean:")
        for k, (old, new) in lean.items():
            lines.append(f"  {k}: {old:g} -> {new:g}")
    return "\n".join(lines)


def write_profile(base: Path, gestures: Gestures, out: Path, sources: list[Path]) -> Path:
    """The base profile with every tunable threshold set from `gestures`."""
    data = yaml.safe_load(base.read_text(encoding="utf-8"))
    keys = {k for _, grid in GROUPS.values() for k in grid} | {"lean.deadzone_deg", "lean.full_deg"}
    g = data.setdefault("gestures", {})
    for key in sorted(keys):
        sec, _, name = key.partition(".")
        g.setdefault(sec, {})[name] = get_param(gestures, key)
    g["cadence_curve"] = list(gestures.cadence_curve)
    Profile.model_validate(data)
    out.parent.mkdir(parents=True, exist_ok=True)
    head = (
        f"# Tuned by `bodyctl webcam-tune` on {datetime.now():%Y-%m-%d %H:%M} from {base.as_posix()}\n"
        f"# using {len(sources)} drill recording(s). Local to this PC (data/ is git-ignored).\n"
        f"# Play: uv run bodyctl webcam --profile {out.as_posix()}\n"
    )
    out.write_text(head + yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n")
    return out

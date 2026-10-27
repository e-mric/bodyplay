"""Score webcam drill recordings against their cues, and search the Tier 0 gesture
thresholds that fit them (`bodyctl webcam-tune`).

A detection counts as a hit when it lands in its cue's window (see drill.EXPECT);
every other detection is a false one. The search tunes one group of thresholds at
a time on all recordings together, maximising hits - 2 x false, and writes the
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
from bodyctl.tools.drill import LEAN, Cue, Recording, cue_windows

BASE_PROFILE = Path("config/profiles/gta5.yaml")
TUNED_PROFILE = Path("data/profiles/gta5_tuned.yaml")
DRILLS_DIR = Path("data/drills")

# Tuned in this order, each group starting from the best values so far: (events it is
# scored on, grid). Aim ignores a moving arm (punch_speed / 4), so punch also scores aim.
GROUPS: dict[str, tuple[tuple[str, ...], dict[str, list[float]]]] = {
    "run": (("run_start", "run_stop"), {"webcam.knee_lift": [0.05, 0.07, 0.09, 0.12, 0.15, 0.18, 0.22]}),
    "jump": (
        ("jump", "crouch"),
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
        },
    ),
}
LEAN_SETTLE_US = 700_000  # ignore the first 0.7 s of a lean or a return to the middle


def default_profile() -> Path:
    return TUNED_PROFILE if TUNED_PROFILE.exists() else BASE_PROFILE


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


def replay(rec: Recording, gestures: Gestures, poses: list[Pose | None] | None = None) -> Replay:
    poses = poses if poses is not None else rec.poses()
    g = WebcamGestures(gestures)
    events: list[tuple[int, str]] = []
    lean = np.full(len(poses), np.nan)
    for i, (t, p) in enumerate(zip(rec.t_us, poses, strict=True)):
        events.extend((int(t), e.name) for e in g.update(p))
        if g.state.visible:
            lean[i] = g.state.lean_deg
    return Replay(events, lean)


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


def suggest_lean(st: LeanStats | None, current: LeanParams) -> dict[str, float]:
    """Deadzone just above standing sway, full deflection at 80 % of a held lean."""
    if st is None or st.hold_deg <= 0:
        return {}
    dz = float(math.ceil(st.neutral_deg + 1)) if not math.isnan(st.neutral_deg) else current.deadzone_deg
    full = float(max(dz + 4, round(0.8 * st.hold_deg)))
    return {"lean.deadzone_deg": dz, "lean.full_deg": full}


@dataclass
class Score:
    events: dict[str, EventScore]
    lean: LeanStats | None
    steps_per_s: float | None  # step rate inside run cues
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
    if sc.lean is not None:
        lines.append(
            f"lean: held {sc.lean.hold_deg:+.1f} deg ({sc.lean.hold_ok:.0%} at half stick+), "
            f"straight {sc.lean.neutral_deg:.1f} deg p98 ({sc.lean.neutral_ok:.0%} at zero)"
        )
        if sc.lean.hold_deg < 0:
            lines.append("lean is REVERSED: is the camera image mirrored?")
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


def search(
    recs: list[Recording], gestures: Gestures, progress: Callable[[str], None] = print
) -> tuple[Gestures, list[GroupResult]]:
    poses = [r.poses() for r in recs]
    cued = {w[0] for r in recs for w in cue_windows(r.cues)}
    best = gestures
    results: list[GroupResult] = []
    for group, (events, grid) in GROUPS.items():
        if events[0] not in cued:
            continue

        def objective(g: Gestures, events=events) -> tuple[int, int, int]:
            sc = score_events(recs, poses, g)
            pick = [sc[e] for e in events if e in sc]
            return sum(s.hits for s in pick), sum(s.expected for s in pick), sum(s.false for s in pick)

        keys = list(grid)
        current = {k: get_param(best, k) for k in keys}
        before = objective(best)
        top = ((before[0] - 2 * before[2], -before[2], 0.0), current, before)
        combos = list(itertools.product(*(grid[k] for k in keys)))
        progress(f"tuning {group}: {len(combos)} combinations on {len(recs)} recording(s)")
        for combo in combos:
            vals = dict(zip(keys, combo, strict=True))
            h, n, f = objective(set_params(best, vals))
            dist = sum(abs(v - current[k]) / (max(grid[k]) - min(grid[k])) for k, v in vals.items())
            cand = ((h - 2 * f, -f, -dist), vals, (h, n, f))
            if cand[0] > top[0]:
                top = cand
        best = set_params(best, top[1])
        changes = {k: (current[k], get_param(best, k)) for k in keys if get_param(best, k) != current[k]}
        results.append(GroupResult(group, before, top[2], changes))
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
    Profile.model_validate(data)
    out.parent.mkdir(parents=True, exist_ok=True)
    head = (
        f"# Tuned by `bodyctl webcam-tune` on {datetime.now():%Y-%m-%d %H:%M} from {base.as_posix()}\n"
        f"# using {len(sources)} drill recording(s). Local to this PC (data/ is git-ignored).\n"
        f"# Play: uv run bodyctl webcam --profile {out.as_posix()}\n"
    )
    out.write_text(head + yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n")
    return out

"""Replay a .bptrace through the firmware gesture library and score it.

The gesture code is C++ (firmware/lib/gestures, hard rule 5); this wraps the
native `replay` build of it, so the PC never re-implements IMU logic.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FIRMWARE_DIR = Path(__file__).parents[3] / "firmware"

# Detector event -> the synthetic/recorded label it should match.
LABEL_FOR_EVENT = {
    "jump": "jump",
    "crouch": "squat_hold",
    "step": "step",
    "run_start": "run_start",
    "run_stop": "run_stop",
}


@dataclass
class Detection:
    t_us: int
    event: str
    confidence: float


def find_pio() -> str | None:
    exe = shutil.which("pio") or shutil.which("platformio")
    if exe:
        return exe
    home = Path.home() / ".platformio" / "penv"
    for cand in (home / "bin" / "pio", home / "Scripts" / "pio.exe"):
        if cand.exists():
            return str(cand)
    return None


def replay_binary(build: bool = True) -> Path:
    name = "program.exe" if os.name == "nt" else "program"
    path = FIRMWARE_DIR / ".pio" / "build" / "replay" / name
    if build:
        pio = find_pio()
        if pio is None:
            if path.exists():
                return path
            raise FileNotFoundError(
                "PlatformIO not found; install it (docs/setup.md) to build the replay tool"
            )
        subprocess.run([pio, "run", "-e", "replay", "-s"], cwd=FIRMWARE_DIR, check=True)
    if not path.exists():
        raise FileNotFoundError(f"replay tool not built: {path}")
    return path


def run(
    trace: str | Path,
    params: dict[str, float] | None = None,
    forward: str = "+x",
    calib_ms: int = 800,
    binary: Path | None = None,
) -> tuple[int, list[Detection]]:
    exe = binary or replay_binary()
    cmd = [str(exe), str(trace), "--forward", forward, "--calib-ms", str(calib_ms)]
    for k, v in (params or {}).items():
        cmd += ["--set", f"{k}={v}"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"replay failed ({res.returncode})")
    calibrated_at = 0
    out: list[Detection] = []
    for line in res.stdout.splitlines():
        d = json.loads(line)
        if "calibrated_at_us" in d:
            calibrated_at = d["calibrated_at_us"]
        else:
            out.append(Detection(d["t_us"], d["event"], d["confidence"]))
    return calibrated_at, out


@dataclass
class Score:
    event: str
    labels: int
    detected: int
    missed: int
    false_positives: int
    median_offset_ms: float | None

    @property
    def rate(self) -> float:
        return self.detected / self.labels if self.labels else float("nan")


def score(
    detections: list[Detection],
    labels: dict[str, list[int]],
    window_ms: tuple[float, float] = (-150.0, 150.0),
    after_us: int = 0,
) -> list[Score]:
    """Greedy one-to-one matching of each event type to its labels within the window.
    Labels before `after_us` (e.g. during calibration) are ignored."""
    out = []
    for event, label_name in LABEL_FOR_EVENT.items():
        ts = sorted(t for t in labels.get(label_name, []) if t >= after_us)
        ds = sorted(d.t_us for d in detections if d.event == event)
        if not ts and not ds:
            continue
        used = set()
        offsets = []
        for t in ts:
            best = None
            for j, d in enumerate(ds):
                if j in used:
                    continue
                off = (d - t) / 1000
                if window_ms[0] <= off <= window_ms[1] and (best is None or abs(off) < abs(best[1])):
                    best = (j, off)
            if best:
                used.add(best[0])
                offsets.append(best[1])
        out.append(
            Score(
                event,
                len(ts),
                len(offsets),
                len(ts) - len(offsets),
                len(ds) - len(used),
                float(np.median(offsets)) if offsets else None,
            )
        )
    return out


def format_scores(scores: list[Score]) -> str:
    lines = [f"{'event':<10} {'labels':>6} {'found':>6} {'missed':>6} {'false+':>6} {'median ms':>10}"]
    for s in scores:
        med = f"{s.median_offset_ms:+.1f}" if s.median_offset_ms is not None else "-"
        lines.append(
            f"{s.event:<10} {s.labels:>6} {s.detected:>6} {s.missed:>6} {s.false_positives:>6} {med:>10}"
        )
    return "\n".join(lines)


def main_print(trace: Path, params: dict[str, float], forward: str, calib_ms: int, show_events: bool) -> int:
    from bodyctl.trace import Trace

    calibrated_at, dets = run(trace, params, forward, calib_ms)
    tr = Trace.read(trace)
    labels: dict[str, list[int]] = {}
    for lb in tr.labels():
        labels.setdefault(lb.event, []).append(lb.t_us)
    if show_events:
        for d in dets:
            print(f"{d.t_us / 1e6:10.4f}s {d.event:<10} {d.confidence:.2f}")
    print(f"{trace.name}: calibrated at {calibrated_at / 1e6:.2f} s, {len(dets)} events")
    if labels:
        print(format_scores(score(dets, labels, after_us=calibrated_at)))
    sys.stdout.flush()
    return 0

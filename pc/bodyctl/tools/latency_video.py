"""End-to-end latency from a high-speed clip: body tell -> marker LED -> TV.

Film one shot with three regions of interest (ROIs):

- **tell**: bright tape on the belt (or a thumb marker for the DualSense baseline).
  Signal = vertical centroid of the brightest pixels, so the onset is when the
  body starts moving.
- **led**: the marker LED that the output stage (PC, node or hub) lights for
  20 ms on every output change. Signal = brightness of the brightest pixels.
- **screen**: part of the TV where the action shows (the character). Signal =
  frame-to-frame change.

Onsets are the first frame where a signal leaves its 0.5 s baseline by more
than k standard deviations for two frames in a row. Each LED flash anchors a
trial; without an LED ROI (DualSense baseline) tell onsets anchor instead.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

ROI_NAMES = ("tell", "led", "screen")


@dataclass(frozen=True)
class Roi:
    x: int
    y: int
    w: int
    h: int

    @classmethod
    def parse(cls, text: str) -> Roi:
        parts = [int(v) for v in text.split(",")]
        if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
            raise ValueError(f"ROI must be x,y,w,h with positive size, got {text!r}")
        return cls(*parts)

    def crop(self, frame: np.ndarray) -> np.ndarray:
        return frame[self.y : self.y + self.h, self.x : self.x + self.w]


@dataclass
class Settings:
    k_sigma: float = 6.0
    baseline_ms: float = 500.0
    tell_lead_max_ms: float = 600.0  # longest plausible tell -> LED (Tier 0 is the slowest)
    screen_lag_max_ms: float = 500.0  # longest plausible LED -> screen
    min_gap_ms: float = 1000.0  # trials are >= 3 s apart; anything closer is one trial
    hold_frames: int = 2  # a deviation must last this many frames
    bright_fraction: float = 0.05  # top fraction of LED-ROI pixels averaged as the LED


@dataclass
class Trial:
    trial: int
    tell_frame: int | None
    led_frame: int | None
    screen_frame: int | None
    tell_to_led_ms: float | None
    led_to_screen_ms: float | None
    tell_to_screen_ms: float | None


# --- per-frame signals ----------------------------------------------------------


def tell_signal(gray: np.ndarray) -> float:
    """Vertical centroid (pixels) of the bright tape: pixels above halfway from the ROI's
    median to its maximum, so every tape pixel counts and the centroid barely jitters."""
    med, top = float(np.median(gray)), float(gray.max())
    if top - med < 20:
        return float("nan")
    ys = np.nonzero(gray > (med + top) / 2)[0]
    return float(ys.mean())


def led_signal(gray: np.ndarray, fraction: float) -> float:
    k = max(1, int(gray.size * fraction))
    return float(np.partition(gray.ravel(), -k)[-k:].mean())


def extract_signals(
    video: str | Path, rois: dict[str, Roi], settings: Settings
) -> tuple[dict[str, np.ndarray], float]:
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise OSError(f"can't open video {video}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    out: dict[str, list[float]] = {name: [] for name in rois}
    prev_screen: np.ndarray | None = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if "tell" in rois:
            out["tell"].append(tell_signal(rois["tell"].crop(gray)))
        if "led" in rois:
            out["led"].append(led_signal(rois["led"].crop(gray), settings.bright_fraction))
        if "screen" in rois:
            cur = rois["screen"].crop(gray).astype(np.float32)
            out["screen"].append(0.0 if prev_screen is None else float(np.abs(cur - prev_screen).mean()))
            prev_screen = cur
    cap.release()
    return {k: np.asarray(v, dtype=float) for k, v in out.items()}, fps


# --- onset detection ------------------------------------------------------------


def detect_onset(
    sig: np.ndarray,
    search: tuple[int, int],
    baseline: tuple[int, int],
    k_sigma: float,
    hold_frames: int = 2,
    floor: float = 1e-3,
) -> int | None:
    """First frame in `search` that leaves the baseline window by > k sigma for hold_frames."""
    b0, b1 = max(0, baseline[0]), max(0, baseline[1])
    base = sig[b0:b1]
    base = base[~np.isnan(base)]
    if len(base) < 5:
        return None
    mu = float(np.median(base))
    sd = float(np.std(base))
    thresh = max(k_sigma * sd, floor)
    s0, s1 = max(0, search[0]), min(len(sig), search[1])
    run = 0
    for i in range(s0, s1):
        if abs(sig[i] - mu) > thresh:
            run += 1
            if run >= hold_frames:
                return i - hold_frames + 1
        else:
            run = 0
    return None


def led_onsets(led: np.ndarray, min_gap: int) -> list[int]:
    """Rising edges of the LED brightness, halfway between its dark and lit levels."""
    lo, hi = np.percentile(led, 10), np.percentile(led, 99.5)
    if hi - lo < 10:  # never lit
        return []
    on = led > (lo + hi) / 2
    edges = np.nonzero(on[1:] & ~on[:-1])[0] + 1
    out: list[int] = []
    for e in edges:
        if not out or e - out[-1] >= min_gap:
            out.append(int(e))
    return out


def tell_onsets(tell: np.ndarray, base_len: int, min_gap: int, settings: Settings) -> list[int]:
    """Movement onsets without an LED: each one starts after a quiet baseline."""
    out: list[int] = []
    i = base_len
    while i < len(tell):
        hit = detect_onset(
            tell, (i, len(tell)), (i - base_len, i), settings.k_sigma, settings.hold_frames, 0.5
        )
        if hit is None:
            break
        out.append(hit)
        i = hit + min_gap
    return out


def analyze(signals: dict[str, np.ndarray], fps: float, settings: Settings) -> list[Trial]:
    ms = 1000.0 / fps
    frames = lambda t_ms: int(round(t_ms / ms))  # noqa: E731
    base = frames(settings.baseline_ms)
    lead = frames(settings.tell_lead_max_ms)
    lag = frames(settings.screen_lag_max_ms)
    gap = frames(settings.min_gap_ms)
    k, hold = settings.k_sigma, settings.hold_frames

    trials: list[Trial] = []
    if "led" in signals:
        for n, led in enumerate(led_onsets(signals["led"], gap), start=1):
            tell = scr = None
            if "tell" in signals:
                tell = detect_onset(
                    signals["tell"], (led - lead, led + 1), (led - lead - base, led - lead), k, hold, 0.5
                )
            if "screen" in signals:
                scr = detect_onset(signals["screen"], (led, led + lag), (led - base, led), k, hold, 0.5)
            trials.append(_trial(n, tell, led, scr, ms))
    elif "tell" in signals:
        for n, tell in enumerate(tell_onsets(signals["tell"], base, gap, settings), start=1):
            scr = None
            if "screen" in signals:
                scr = detect_onset(
                    signals["screen"], (tell, tell + lead + lag), (tell - base, tell), k, hold, 0.5
                )
            trials.append(_trial(n, tell, None, scr, ms))
    return trials


def _trial(n: int, tell: int | None, led: int | None, scr: int | None, ms: float) -> Trial:
    def d(a: int | None, b: int | None) -> float | None:
        return None if a is None or b is None else round((b - a) * ms, 2)

    return Trial(n, tell, led, scr, d(tell, led), d(led, scr), d(tell, scr))


# --- reporting ------------------------------------------------------------------


def summarize(trials: list[Trial]) -> dict[str, dict[str, float]]:
    out = {}
    for col in ("tell_to_led_ms", "led_to_screen_ms", "tell_to_screen_ms"):
        vals = np.array([getattr(t, col) for t in trials if getattr(t, col) is not None])
        if len(vals):
            out[col] = {
                "n": int(len(vals)),
                "median": float(np.median(vals)),
                "p90": float(np.percentile(vals, 90)),
                "p99": float(np.percentile(vals, 99)),
                "min": float(vals.min()),
                "max": float(vals.max()),
            }
    return out


def write_csv(trials: list[Trial], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(trials[0]).keys()) if trials else ["trial"])
        w.writeheader()
        for t in trials:
            w.writerow(asdict(t))


def write_histogram(trials: list[Trial], path: Path, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cols = [
        c
        for c in ("tell_to_led_ms", "led_to_screen_ms", "tell_to_screen_ms")
        if any(getattr(t, c) is not None for t in trials)
    ]
    fig, axes = plt.subplots(1, max(1, len(cols)), figsize=(4 * max(1, len(cols)), 3.2), squeeze=False)
    for ax, col in zip(axes[0], cols, strict=False):
        vals = [getattr(t, col) for t in trials if getattr(t, col) is not None]
        ax.hist(vals, bins=min(20, max(5, len(vals) // 2)), color="#3b6fd8")
        med = float(np.median(vals))
        ax.axvline(med, color="#d8473b", linewidth=1.5)
        ax.set_title(
            f"{col.removesuffix('_ms').replace('_', ' ')}\nmedian {med:.1f} ms (n={len(vals)})", fontsize=9
        )
        ax.set_xlabel("ms")
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# --- ROIs -----------------------------------------------------------------------


def rois_path(video: Path) -> Path:
    return video.with_suffix(video.suffix + ".rois.json")


def load_rois(path: Path) -> dict[str, Roi]:
    data = json.loads(path.read_text())
    return {k: Roi(**v) for k, v in data.items()}


def save_rois(path: Path, rois: dict[str, Roi]) -> None:
    path.write_text(json.dumps({k: asdict(v) for k, v in rois.items()}, indent=2))


def select_rois_interactive(video: Path, names: tuple[str, ...]) -> dict[str, Roi]:
    """Draw each ROI on the first frame (Enter to accept, c to skip)."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise OSError(f"can't read a frame from {video}")
    rois = {}
    for name in names:
        x, y, w, h = cv2.selectROI(f"Select {name} ROI (Enter = ok, c = skip)", frame, showCrosshair=True)
        cv2.destroyAllWindows()
        if w > 0 and h > 0:
            rois[name] = Roi(int(x), int(y), int(w), int(h))
    return rois


def run(
    video: str | Path,
    rois: dict[str, Roi],
    settings: Settings | None = None,
    out_dir: str | Path | None = None,
    fps_override: float | None = None,
) -> tuple[list[Trial], dict[str, dict[str, float]], Path]:
    settings = settings or Settings()
    video = Path(video)
    signals, fps = extract_signals(video, rois, settings)
    fps = fps_override or fps
    if not fps or math.isnan(fps):
        raise ValueError("video has no frame rate; pass --fps")
    trials = analyze(signals, fps, settings)
    out = Path(out_dir) if out_dir else video.parent
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"{video.stem}_latency.csv"
    write_csv(trials, csv_path)
    if trials:
        write_histogram(trials, out / f"{video.stem}_latency.png", f"{video.name} @ {fps:.0f} fps")
    return trials, summarize(trials), csv_path

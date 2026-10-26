"""Per-stage latency stats for the PC pipeline (Tier 0), same stages and text
format as firmware/lib/latency so the dashboard can show both side by side."""

from __future__ import annotations

from collections import deque

import numpy as np

STAGES = ("capture->pose", "pose->gesture", "gesture->output", "frame->output")


class StageStats:
    """Keeps the last `window` samples per stage (microseconds)."""

    def __init__(self, stages: tuple[str, ...] = STAGES, window: int = 2000):
        self._d: dict[str, deque[float]] = {s: deque(maxlen=window) for s in stages}

    def add(self, stage: str, us: float) -> None:
        self._d.setdefault(stage, deque(maxlen=2000)).append(us)

    def summary(self, stage: str) -> dict[str, float] | None:
        vals = self._d.get(stage)
        if not vals:
            return None
        a = np.fromiter(vals, float)
        return {
            "n": float(len(a)),
            "p50_ms": float(np.percentile(a, 50)) / 1000,
            "p99_ms": float(np.percentile(a, 99)) / 1000,
            "max_ms": float(a.max()) / 1000,
        }

    def format(self) -> str:
        lines = []
        for stage in self._d:
            s = self.summary(stage)
            if s:
                lines.append(
                    f"{stage} n={int(s['n'])} p50={s['p50_ms']:.1f} "
                    f"p99={s['p99_ms']:.1f} max={s['max_ms']:.1f} ms"
                )
        return "\n".join(lines)

    def reset(self) -> None:
        for d in self._d.values():
            d.clear()

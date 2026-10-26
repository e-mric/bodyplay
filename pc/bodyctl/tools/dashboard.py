"""Tier 0 dashboard: camera frame with skeleton, live events, cadence, lean, aim,
gamepad state and per-stage latency, drawn with OpenCV.

The top-left square flashes white for one frame whenever the marker would
pulse: the fallback output marker when no marker XIAO is connected.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from bodyctl.gestures.webcam import BodyState, GestureEvent, Pose
from bodyctl.output.gamepad import Report

# BlazePose skeleton edges (subset that reads well at a glance).
EDGES = [
    (11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24),
    (23, 25), (25, 27), (24, 26), (26, 28), (0, 11), (0, 12),
]  # fmt: skip

WHITE = (255, 255, 255)
GREEN = (80, 220, 120)
AMBER = (40, 180, 255)
GREY = (150, 150, 150)
RED = (60, 60, 230)


@dataclass
class Dashboard:
    window: str = "bodyplay"
    flash_corner: bool = True
    events: deque[tuple[int, GestureEvent]] = field(default_factory=lambda: deque(maxlen=8))
    _flash: bool = False

    def note_events(self, events: list[GestureEvent], now_us: int) -> None:
        for e in events:
            if e.name != "step":
                self.events.appendleft((now_us, e))

    def flash(self) -> None:
        self._flash = True

    def draw(
        self,
        frame: np.ndarray,
        pose: Pose | None,
        state: BodyState,
        report: Report,
        mode: str,
        stats_text: str,
        fps: float,
        now_us: int,
    ) -> np.ndarray:
        import cv2

        img = frame.copy()
        if pose is not None:
            pts = pose.img
            for a, b in EDGES:
                if pts[a, 2] > 0.5 and pts[b, 2] > 0.5:
                    pa = (int(pts[a, 0]), int(pts[a, 1]))
                    pb = (int(pts[b, 0]), int(pts[b, 1]))
                    cv2.line(img, pa, pb, GREEN, 2, cv2.LINE_AA)
        h, w = img.shape[:2]
        panel = np.zeros((h, 300, 3), np.uint8)
        y = 24

        def text(s: str, color=WHITE, scale=0.5) -> None:
            nonlocal y
            cv2.putText(panel, s, (10, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)
            y += int(22 * scale / 0.5)

        def bar(label: str, v: float, signed: bool = False) -> None:
            nonlocal y
            cv2.putText(panel, label, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, GREY, 1, cv2.LINE_AA)
            x0, x1 = 110, 285
            cv2.rectangle(panel, (x0, y - 11), (x1, y + 1), (60, 60, 60), 1)
            if signed:
                mid = (x0 + x1) // 2
                end = int(mid + v * (x1 - mid))
                cv2.rectangle(panel, (min(mid, end), y - 10), (max(mid, end), y), AMBER, -1)
            else:
                cv2.rectangle(panel, (x0, y - 10), (int(x0 + max(0.0, v) * (x1 - x0)), y), AMBER, -1)
            y += 20

        text(f"mode {mode}   {fps:4.0f} fps", AMBER if mode == "paused" else WHITE, 0.6)
        text("person " + ("tracked" if state.visible else "NOT FOUND"), GREEN if state.visible else RED)
        text(f"cadence {state.cadence_hz:4.2f} steps/s" + ("  SPRINT" if state.sprinting else ""))
        bar("run", state.run_stick)
        bar("lean", state.lean_stick, signed=True)
        text("aiming" if state.aiming else "not aiming", GREEN if state.aiming else GREY)
        y += 4
        text("gamepad", GREY)
        btns = " ".join(sorted(report.buttons)) or "-"
        text(f"  {btns}")
        (lx, ly), (rx, ry) = report.left, report.right
        text(f"  L ({lx:+.2f},{ly:+.2f})  R ({rx:+.2f},{ry:+.2f})")
        text(f"  L2 {report.l2:.2f}  R2 {report.r2:.2f}")
        y += 4
        text("events", GREY)
        for t, e in list(self.events)[:6]:
            age = (now_us - t) / 1e6
            text(f"  {e.name:<10} {e.confidence:.2f}  {age:4.1f}s ago", WHITE if age < 1 else GREY)
        y += 4
        text("latency", GREY)
        for line in stats_text.splitlines():
            text("  " + line, WHITE, 0.4)
        y = h - 12
        text("q quit  m mode  p pause  r reset stats", GREY, 0.4)
        out = np.hstack([img, panel])
        if self.flash_corner:
            color = WHITE if self._flash else (0, 0, 0)
            cv2.rectangle(out, (0, 0), (40, 40), color, -1)
        self._flash = False
        return out

    def show(self, image: np.ndarray) -> int:
        import cv2

        cv2.imshow(self.window, image)
        return cv2.waitKey(1) & 0xFF

    def close(self) -> None:
        import cv2

        cv2.destroyAllWindows()

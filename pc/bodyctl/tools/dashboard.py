"""Tier 0 dashboard: the drill window's look for live play. Camera with the skeleton,
the detected moves, body state, gamepad and per-stage latency, drawn by drill_view.

The top-left square flashes white for one frame whenever the marker would
pulse: the fallback output marker when no marker XIAO is connected.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from bodyctl.gestures.webcam import BodyState, GestureEvent, Pose
from bodyctl.output.gamepad import Report

FRESH_S = 0.6  # a detection this recent titles the window
FLASH_PX = 30  # corner marker square; the title starts just right of it


@dataclass
class Dashboard:
    window: str = "bodyplay"
    flash_corner: bool = True
    rect: tuple[int, int, int, int] | None = None  # window x, y, w, h; None = ~90 % of the screen
    topmost: bool = False
    pad_kind: str = "xbox"  # controller picture: xbox or dualsense
    events: deque[tuple[int, GestureEvent]] = field(default_factory=lambda: deque(maxlen=8))
    _flash: bool = False
    _opened: bool = False
    _t0_us: int | None = None
    _frames: int = 0
    _quality: deque[float] = field(default_factory=deque)

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
        from bodyctl.tools import drill_view as V

        if self._t0_us is None:
            self._t0_us = now_us
            self._quality = deque(maxlen=V.SIGNAL_COLS * V.SIGNAL_ROWS)
        self._frames += 1
        parts = V.part_visibility(pose)
        self._quality.append(V.frame_quality(parts))
        h, w = frame.shape[:2]
        hud = V.Hud(
            fps=fps,
            size=(w, h),
            elapsed_s=(now_us - self._t0_us) / 1e6,
            frames=self._frames,
            parts=parts,
            quality=self._quality,
            tag="LIVE",
        )
        recent = [((now_us - t) / 1e6, e.name, True) for t, e in self.events]
        fresh = bool(recent) and recent[0][0] < FRESH_S
        mode_name = mode.replace("_", " ").upper()
        if mode == "paused":
            title, style = "PAUSED", "setup"
        elif fresh:
            title, style = recent[0][1].replace("_", " ").upper() + "!", "action"
        else:
            title, style = mode_name, "idle"
        sub = [f"MODE {mode_name}"] + ([] if state.visible else ["OUT OF VIEW"])
        sub.append("m mode  p pause  r reset  q quit")
        latency = [line.strip() for line in stats_text.splitlines() if line.startswith("frame->output")]
        panel = V.Panel(
            header=f"LIVE  {mode_name}",
            state=state,
            events=recent,
            pad=latency,
            report=report,
            pad_kind=self.pad_kind,
        )
        out = V.render(frame, pose, title, "   ".join(sub), style, None, panel, hud)
        if self.flash_corner:
            out[:FLASH_PX, :FLASH_PX] = 255 if self._flash else 0
        self._flash = False
        return out

    def show(self, image: np.ndarray) -> int:
        from bodyctl.tools import drill_view as V

        if not self._opened:
            V.open_window(self.window, self.rect, self.topmost)
            self._opened = True
        return V.show(self.window, image)

    def close(self) -> None:
        import cv2

        cv2.destroyAllWindows()

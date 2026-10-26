"""Tier 0 main loop: webcam -> pose -> gestures -> profile bindings -> virtual pad.

Runs on the Windows play PC next to chiaki-ng (vgamepad backend), or anywhere
with the dry-run backend for development.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass

import numpy as np

from bodyctl.config.profile import Profile
from bodyctl.gestures.webcam import GestureEvent, Pose, WebcamGestures
from bodyctl.mapping import BindingEngine
from bodyctl.output.gamepad import Gamepad
from bodyctl.output.marker import MultiMarker
from bodyctl.sensors.webcam_pose import now_us
from bodyctl.tools.stats import StageStats

MODE_CYCLE = ("on_foot", "car", "heli", "swim", "qte")


@dataclass
class FrameResult:
    t_capture_us: int
    pose: Pose | None
    events: list[GestureEvent]


class Tier0:
    """Glue with no I/O of its own, so tests can drive it with synthetic poses."""

    def __init__(self, profile: Profile, gamepad: Gamepad, clock: Callable[[], int] = now_us):
        self.profile = profile
        self.gestures = WebcamGestures(profile.gestures)
        self.engine = BindingEngine(profile, gamepad)
        self.gamepad = gamepad
        self.stats = StageStats()
        self.clock = clock
        self._paused_from: str | None = None

    def step(self, t_capture_us: int, pose: Pose | None, t_pose_us: int | None = None) -> FrameResult:
        t_pose_us = t_pose_us if t_pose_us is not None else self.clock()
        events = self.gestures.update(pose)
        t_gesture = self.clock()
        self.engine.apply(self.gestures.state, events, t_gesture)
        t_out = self.clock()
        self.stats.add("capture->pose", t_pose_us - t_capture_us)
        self.stats.add("pose->gesture", t_gesture - t_pose_us)
        self.stats.add("gesture->output", t_out - t_gesture)
        self.stats.add("frame->output", t_out - t_capture_us)
        return FrameResult(t_capture_us, pose, events)

    @property
    def mode(self) -> str:
        return self.engine.mode

    def cycle_mode(self) -> None:
        modes = [m for m in MODE_CYCLE if self.profile.mode_for(m) is not None]
        i = modes.index(self.mode) if self.mode in modes else -1
        self.engine.set_mode(modes[(i + 1) % len(modes)], self.clock())

    def toggle_pause(self) -> None:
        if self.mode == "paused" and self._paused_from:
            self.engine.set_mode(self._paused_from, self.clock())
            self._paused_from = None
        elif self.profile.mode_for("paused") is not None:
            self._paused_from = self.mode
            self.engine.set_mode("paused", self.clock())


def run_webcam(
    profile: Profile,
    gamepad: Gamepad,
    camera_index: int = 0,
    size: tuple[int, int] = (640, 480),
    fps: int = 60,
    model: str = "lite",
    show: bool = True,
) -> int:
    from bodyctl.sensors.webcam_pose import Camera, PoseEstimator, ensure_model
    from bodyctl.tools.dashboard import Dashboard

    with ExitStack() as cleanup:
        cleanup.callback(gamepad.close)
        estimator = PoseEstimator(ensure_model(model))
        cleanup.callback(estimator.close)
        cam = Camera(camera_index, size[0], size[1], fps)
        cleanup.callback(cam.close)
        print(f"camera {camera_index}: {cam.size[0]}x{cam.size[1]} @ {cam.fps:.0f} fps; Ctrl+C or q to quit")
        app = Tier0(profile, gamepad)
        dash = Dashboard() if show else None
        if dash:
            cleanup.callback(dash.close)
            # Every marker pulse also flashes the dashboard corner (fallback marker).
            gamepad.marker = MultiMarker(gamepad.marker, _FlashOnly(dash))
        fps_est = 0.0
        last_t = None
        try:
            for t_cap, frame in _frames(cam):
                pose = estimator.detect(frame, t_cap)
                res = app.step(t_cap, pose)
                if last_t is not None:
                    fps_est += 0.1 * (1e6 / max(1, t_cap - last_t) - fps_est)
                last_t = t_cap
                if dash:
                    dash.note_events(res.events, t_cap)
                    img = dash.draw(
                        frame,
                        pose,
                        app.gestures.state,
                        gamepad.report,
                        app.mode,
                        app.stats.format(),
                        fps_est,
                        t_cap,
                    )
                    key = dash.show(img)
                    if key == ord("q"):
                        break
                    if key == ord("m"):
                        app.cycle_mode()
                    elif key == ord("p"):
                        app.toggle_pause()
                    elif key == ord("r"):
                        app.stats.reset()
                else:
                    for e in res.events:
                        if e.name != "step":
                            print(f"{t_cap / 1e6:10.3f}s {e.name} ({e.confidence:.2f})")
        except KeyboardInterrupt:
            pass
        finally:
            print(app.stats.format())
            gamepad.release_all(now_us())
            gamepad.update(now_us())
        return 0


class _FlashOnly:
    def __init__(self, dash) -> None:
        self._dash = dash

    def pulse(self) -> None:
        self._dash.flash()

    def close(self) -> None:
        pass


def _frames(cam) -> Iterator[tuple[int, np.ndarray]]:
    while True:
        yield cam.read()

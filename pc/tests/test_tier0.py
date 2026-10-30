import numpy as np
import pytest
from posegen import Timeline

from bodyctl.config.profile import load_profile
from bodyctl.output.gamepad import DryRunBackend, Gamepad
from bodyctl.tier0 import Tier0


class Clock:
    def __init__(self):
        self.t = 0

    def __call__(self) -> int:
        return self.t


class CountingMarker:
    def __init__(self):
        self.n = 0

    def pulse(self):
        self.n += 1

    def close(self):
        pass


@pytest.fixture
def app():
    prof = load_profile("config/profiles/gta5.yaml")
    backend = DryRunBackend()
    marker = CountingMarker()
    clock = Clock()
    gp = Gamepad(backend, prof.press.hold_ms, prof.press.release_ms, marker)
    return Tier0(prof, gp, clock=clock), backend, marker, clock


def drive(app, clock, tl):
    for p in tl.poses():
        clock.t = p.t_us + 15_000  # pretend pose inference took 15 ms
        app.step(p.t_us, p, t_pose_us=p.t_us + 12_000)


def test_run_then_jump_end_to_end(app):
    tier0, backend, marker, clock = app
    tl = Timeline(9.0)
    tl.run(1.0, 4.0, cadence_hz=2.5)
    rev = tl.jump(6.0)
    drive(tier0, clock, tl)
    reports = backend.history
    forward = [r.left[1] for _, r in reports]
    assert max(forward) > 0.9
    assert reports[-1][1].left == (0.0, 0.0)  # stopped
    square = [t for t, r in reports if "square" in r.buttons]
    assert len(square) == 1
    assert abs(square[0] / 1e6 - rev) < 0.1
    assert marker.n == 2  # run start + jump
    text = tier0.stats.format()
    assert "frame->output" in text and "capture->pose n=" in text


def test_mode_cycle_and_pause(app):
    tier0, *_ = app
    assert tier0.mode == "on_foot"
    tier0.cycle_mode()
    assert tier0.mode == "car"
    tier0.toggle_pause()
    assert tier0.mode == "paused"
    tier0.toggle_pause()
    assert tier0.mode == "car"


def test_no_person_keeps_pad_neutral(app):
    tier0, backend, _, clock = app
    for k in range(30):
        clock.t = k * 33_333
        tier0.step(clock.t, None)
    assert not backend.history


def test_dashboard_draws():
    pytest.importorskip("cv2")
    from bodyctl.gestures.webcam import BodyState, GestureEvent
    from bodyctl.output.gamepad import Report
    from bodyctl.tools.dashboard import Dashboard

    tl = Timeline(0.1)
    pose = tl.poses()[0]
    dash = Dashboard()
    dash.note_events([GestureEvent("jump", 0, 0.9)], 0)
    dash.flash()
    frame = np.zeros((480, 640, 3), np.uint8)
    img = dash.draw(frame, pose, BodyState(visible=True), Report(), "on_foot", "a n=1 p50=1.0", 30.0, 500_000)
    assert img.shape == (480, 940, 3)
    assert img[5, 5].tolist() == [255, 255, 255]  # corner flash on this frame
    img2 = dash.draw(frame, pose, BodyState(), Report(), "on_foot", "", 30.0, 600_000)
    assert img2[5, 5].tolist() == [0, 0, 0]  # and off on the next


@pytest.mark.parametrize("failure", ["model", "camera", "read"])
def test_startup_and_capture_failures_close_acquired_resources(monkeypatch, failure):
    from bodyctl.sensors import webcam_pose
    from bodyctl.tier0 import run_webcam

    closed = []

    class Backend(DryRunBackend):
        def close(self):
            closed.append("pad")

    class Estimator:
        def __init__(self, path):
            if failure == "model":
                raise OSError("model failed")

        def close(self):
            closed.append("model")

    class Camera:
        size = (640, 480)
        fps = 30

        def __init__(self, *args):
            if failure == "camera":
                raise OSError("camera failed")

        def read(self):
            raise OSError("read failed")

        def close(self):
            closed.append("camera")

    monkeypatch.setattr(webcam_pose, "ensure_model", lambda _: "unused.task")
    monkeypatch.setattr(webcam_pose, "PoseEstimator", Estimator)
    monkeypatch.setattr(webcam_pose, "Camera", Camera)
    profile = load_profile("config/profiles/gta5.yaml")
    with pytest.raises(OSError, match=f"{failure} failed"):
        run_webcam(profile, Gamepad(Backend(), 30, 30), show=False, pose="mediapipe")
    expected = {"model": ["pad"], "camera": ["model", "pad"], "read": ["camera", "model", "pad"]}
    assert closed == expected[failure]

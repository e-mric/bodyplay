import numpy as np
import pytest
from posegen import Timeline

from bodyctl.config.profile import load_profile
from bodyctl.gestures import webcam as W
from bodyctl.gestures.webcam import OneEuro, WebcamGestures
from bodyctl.sensors import rtmpose
from bodyctl.tools import webcam_tune as wt
from bodyctl.tools.drill import Recording

GTA5 = "config/profiles/gta5.yaml"


def halpe_from(pose_img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Inverse of rtmpose.to_blaze for the points Halpe has: a fake RTMPose output."""
    kpts, scores = np.zeros((26, 2)), np.zeros(26)
    for h, b in rtmpose.HALPE_TO_BLAZE.items():
        kpts[h], scores[h] = pose_img[b, :2], 0.8
    return kpts, scores


def test_halpe_maps_onto_blazepose_layout():
    img = Timeline(0.1).poses()[0].img
    out = rtmpose.to_blaze(*halpe_from(img))
    for b in (W.NOSE, W.L_SHOULDER, W.R_SHOULDER, W.L_WRIST, W.R_HIP, W.L_KNEE, W.R_ANKLE):
        np.testing.assert_allclose(out[b, :2], img[b, :2])
        assert out[b, 2] == pytest.approx(0.8)
    assert out[1, 2] == 0  # BlazePose-only landmark (inner eye): not visible


def test_box_follows_keypoints_and_drops_when_lost():
    kpts, scores = halpe_from(Timeline(0.1).poses()[0].img)
    box = rtmpose.next_box(kpts, scores, 640, 480)
    assert box is not None
    assert box[0] < kpts[:17, 0].min() and box[2] > kpts[:17, 0].max()
    assert box[1] < kpts[0, 1] and box[3] <= 480
    scores[:17] = 0.1
    assert rtmpose.next_box(kpts, scores, 640, 480) is None


class FakeDetector:
    def __init__(self):
        self.calls = 0

    def __call__(self, frame):
        self.calls += 1
        return np.array([[100.0, 50.0, 500.0, 470.0], [10.0, 10.0, 60.0, 60.0]])


class FakePose:
    def __init__(self, img):
        self.out = halpe_from(img)
        self.boxes = []

    def __call__(self, frame, bboxes):
        self.boxes.append(bboxes[0])
        return [self.out[0]], [self.out[1]]


def test_estimator_detects_once_then_tracks():
    img = Timeline(0.1).poses()[0].img
    det, pose = FakeDetector(), FakePose(img)
    est = rtmpose.RTMPoseEstimator(det, pose)
    frame = np.zeros((480, 640, 3), np.uint8)
    for i in range(rtmpose.DET_EVERY + 1):
        p = est.detect(frame, i * 33_000)
    assert det.calls == 2  # first frame, then again after DET_EVERY frames
    assert pose.boxes[0] == [100.0, 50.0, 500.0, 470.0]  # the largest person
    assert pose.boxes[1] != pose.boxes[0]  # then the box from the keypoints
    assert p.world is None and p.img[W.L_KNEE, 2] == pytest.approx(0.8)


def test_one_euro_calms_jitter_but_follows_moves():
    rng = np.random.default_rng(0)
    f = OneEuro(min_cutoff_hz=1.0, beta=0.3, d_cutoff_hz=1.0)
    still = [f(i * 33_333, np.array([100.0 + rng.normal(0, 3)]), 100.0)[0] for i in range(90)]
    assert np.std(np.diff(still[30:])) < 0.5 * 3 * np.sqrt(2)  # well under the raw jitter
    f = OneEuro(min_cutoff_hz=1.0, beta=0.3, d_cutoff_hz=1.0)
    for i in range(30):
        f(i * 33_333, np.array([100.0]), 100.0)
    out = [f((30 + i) * 33_333, np.array([150.0]), 100.0)[0] for i in range(6)]  # half-torso step
    assert out[-1] > 140  # within 200 ms


def test_smoothing_off_by_default_and_tunable():
    tl = Timeline(6.0)
    rev = tl.jump(3.0)
    g = load_profile(GTA5).gestures
    assert g.webcam.smooth_min_cutoff_hz == 0  # off: existing behaviour unchanged

    def jump_time(gestures):
        wg = WebcamGestures(gestures)
        return next(e.t_us / 1e6 for p in tl.poses() for e in wg.update(p) if e.name == "jump")

    t_raw = jump_time(g)
    t_smooth = jump_time(wt.set_params(g, {"webcam.smooth_min_cutoff_hz": 1.0, "webcam.smooth_beta": 0.3}))
    assert abs(t_raw - rev) < 0.1
    assert 0 <= t_smooth - t_raw <= 0.1  # at most ~3 frames later


def test_pose_family_and_profiles(tmp_path, monkeypatch):
    assert wt.pose_family("rtmpose-m") == "rtmpose"
    old = Recording({"model": "heavy"}, np.zeros(0, np.int64), np.zeros((0, 33, 3)), np.zeros((0, 33, 3)), [])
    assert wt.recording_pose(old) == "mediapipe-heavy"
    monkeypatch.setattr(wt, "TUNED_DIR", tmp_path)
    assert wt.default_profile("rtmpose-m") == wt.BASE_PROFILE
    (tmp_path / "gta5_tuned_rtmpose.yaml").write_text("x")
    assert wt.default_profile("rtmpose-m") == tmp_path / "gta5_tuned_rtmpose.yaml"
    assert wt.default_profile("mediapipe-lite") == wt.BASE_PROFILE

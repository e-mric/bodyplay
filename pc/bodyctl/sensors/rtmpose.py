"""Tier 0 input on an NVIDIA GPU: RTMPose-m (body + feet, Halpe 26 keypoints) via rtmlib
and ONNX Runtime, as an alternative to MediaPipe.

Compared on the same recorded drill (data/drills, Oct 2026), it caught 30 of 36 cued
moves against 20 for MediaPipe lite, kept the knees tracked in 98 % of frames
(MediaPipe: 79 %) and ran in ~4.5 ms per frame on an RTX 5080.

Top-down, like MediaPipe: a person detector (YOLOX-tiny) finds you, then the box
follows your previous keypoints; the detector reruns every DET_EVERY frames or when
tracking drops. RTMPose gives image keypoints only (no 3D), so punch and aim use their
2D checks. Models are fetched by rtmlib on first use into ~/.cache/rtmlib.
"""

from __future__ import annotations

import numpy as np

from bodyctl.gestures import webcam as W
from bodyctl.gestures.webcam import NUM_LANDMARKS, Pose

_ONNX = "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
DETECTOR = _ONNX + "yolox_tiny_8xb8-300e_humanart-6f3252f9.zip"
POSE_M = _ONNX + "rtmpose-m_simcc-body7_pt-body7-halpe26_700e-256x192-4d3e73dd_20230605.zip"

# Halpe 26 index -> BlazePose 33 index ("left" is the person's left in both). Halpe adds
# head, neck and mid-hip (17-19, unused) and feet: toes (20-23) and heels (24-25).
HALPE_TO_BLAZE = {
    0: W.NOSE, 1: 2, 2: 5, 3: 7, 4: 8,
    5: W.L_SHOULDER, 6: W.R_SHOULDER, 7: W.L_ELBOW, 8: W.R_ELBOW, 9: W.L_WRIST, 10: W.R_WRIST,
    11: W.L_HIP, 12: W.R_HIP, 13: W.L_KNEE, 14: W.R_KNEE, 15: W.L_ANKLE, 16: W.R_ANKLE,
    20: 31, 21: 32, 24: 29, 25: 30,
}  # fmt: skip
DET_EVERY = 30
TRACK_MIN_SCORE = 0.3  # keypoints above this score shape the next box
TRACK_MIN_POINTS = 6  # fewer confident keypoints = lost, detect again


def available() -> bool:
    """rtmlib installed and ONNX Runtime can use CUDA."""
    try:
        import onnxruntime as ort
        import rtmlib  # noqa: F401
    except ImportError:
        return False
    return "CUDAExecutionProvider" in ort.get_available_providers()


def to_blaze(kpts: np.ndarray, scores: np.ndarray) -> np.ndarray:
    """(26, 2) keypoints + (26,) scores -> (33, 3) BlazePose-layout img array; scores
    become visibility, clipped to 0..1; landmarks Halpe lacks get visibility 0."""
    img = np.zeros((NUM_LANDMARKS, 3))
    for h, b in HALPE_TO_BLAZE.items():
        img[b] = (kpts[h, 0], kpts[h, 1], min(1.0, max(0.0, float(scores[h]))))
    return img


def next_box(kpts: np.ndarray, scores: np.ndarray, width: int, height: int) -> np.ndarray | None:
    """Box for the next frame from this frame's confident body keypoints (first 17), with
    room around the head and feet; None when too few are confident."""
    good = scores[:17] > TRACK_MIN_SCORE
    if good.sum() < TRACK_MIN_POINTS:
        return None
    pts = kpts[:17][good]
    (x0, y0), (x1, y1) = pts.min(axis=0), pts.max(axis=0)
    w, h = x1 - x0, y1 - y0
    return np.array(
        [
            max(0.0, x0 - 0.2 * w),
            max(0.0, y0 - 0.25 * h),
            min(width, x1 + 0.2 * w),
            min(height, y1 + 0.15 * h),
        ]
    )


class RTMPoseEstimator:
    """Same interface as webcam_pose.PoseEstimator: detect(frame, t_us) -> Pose | None."""

    def __init__(self, detector=None, pose=None, device: str = "cuda"):
        if detector is None or pose is None:
            import onnxruntime as ort
            from rtmlib import YOLOX, RTMPose

            if hasattr(ort, "preload_dlls"):  # CUDA/cuDNN from the nvidia-* wheels
                ort.preload_dlls()
            detector = detector or YOLOX(
                DETECTOR, model_input_size=(416, 416), backend="onnxruntime", device=device
            )
            pose = pose or RTMPose(POSE_M, model_input_size=(192, 256), backend="onnxruntime", device=device)
        self._det = detector
        self._pose = pose
        self._box: np.ndarray | None = None
        self._n = 0

    def _detect(self, frame: np.ndarray) -> np.ndarray | None:
        boxes = self._det(frame)
        if boxes is None or len(boxes) == 0:
            return None
        boxes = np.asarray(boxes, float)
        return boxes[np.argmax((boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1]))]  # largest person

    def detect(self, frame_bgr: np.ndarray, t_us: int) -> Pose | None:
        if self._box is None or self._n % DET_EVERY == 0:
            self._box = self._detect(frame_bgr)
        self._n += 1
        if self._box is None:
            return None
        kpts, scores = self._pose(frame_bgr, bboxes=[self._box.tolist()])
        k, s = np.asarray(kpts[0], float), np.asarray(scores[0], float)
        h, w = frame_bgr.shape[:2]
        self._box = next_box(k, s, w, h)
        return Pose(t_us, to_blaze(k, s), None)

    def close(self) -> None:
        pass

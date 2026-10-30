"""Tier 0 input: webcam frames -> MediaPipe Pose Landmarker -> Pose.

Uses the MediaPipe Tasks API (mediapipe >= 1.0) in VIDEO mode, synchronously,
so each frame's capture and inference times are known for the latency stats.
The model file is fetched on first use into data/models/ (git-ignored).
"""

from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

from bodyctl.gestures.webcam import NUM_LANDMARKS, Pose

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_{v}/float16/latest/pose_landmarker_{v}.task"
)
MODEL_VARIANTS = ("lite", "full", "heavy")
POSE_BACKENDS = ("auto", "mediapipe", "rtmpose")


def resolve_pose(pose: str) -> str:
    """auto -> rtmpose when rtmlib and a CUDA GPU are available, else mediapipe."""
    if pose == "auto":
        from bodyctl.sensors import rtmpose

        return "rtmpose" if rtmpose.available() else "mediapipe"
    if pose not in POSE_BACKENDS:
        raise ValueError(f"pose backend must be one of {POSE_BACKENDS}")
    return pose


def pose_label(pose: str, model: str = "lite") -> str:
    """Name stored with recordings, e.g. rtmpose-m or mediapipe-lite."""
    return "rtmpose-m" if resolve_pose(pose) == "rtmpose" else f"mediapipe-{model}"


def open_pose(pose: str = "auto", model: str = "lite"):
    """A pose estimator (detect(frame, t_us) -> Pose | None, close()) and its label.
    `model` picks the MediaPipe variant; RTMPose always uses RTMPose-m."""
    if resolve_pose(pose) == "rtmpose":
        from bodyctl.sensors.rtmpose import RTMPoseEstimator

        return RTMPoseEstimator(), "rtmpose-m"
    return PoseEstimator(ensure_model(model)), f"mediapipe-{model}"


def now_us() -> int:
    return time.perf_counter_ns() // 1000


def ensure_model(variant: str = "lite", models_dir: str | Path = "data/models") -> Path:
    if variant not in MODEL_VARIANTS:
        raise ValueError(f"model variant must be one of {MODEL_VARIANTS}")
    path = Path(models_dir) / f"pose_landmarker_{variant}.task"
    if not path.exists():
        url = MODEL_URL.format(v=variant)
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading the MediaPipe pose model ({variant}) from {url}", file=sys.stderr)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(path)
    return path


class Camera:
    """OpenCV capture tuned for latency: MJPG, smallest buffer, highest fps the camera allows."""

    def __init__(self, index: int = 0, width: int = 640, height: int = 480, fps: int = 60):
        import cv2

        if sys.platform == "win32":
            api = cv2.CAP_DSHOW
        elif sys.platform == "darwin":
            api = cv2.CAP_AVFOUNDATION
        else:
            api = cv2.CAP_V4L2
        self._cv2 = cv2
        self.cap = cv2.VideoCapture(index, api)
        if not self.cap.isOpened():
            raise OSError(f"can't open camera {index}")
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.size = (
            int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )

    def read(self) -> tuple[int, np.ndarray]:
        ok, frame = self.cap.read()
        t = now_us()
        if not ok:
            raise OSError("camera stopped delivering frames")
        return t, frame

    def close(self) -> None:
        self.cap.release()


class PoseEstimator:
    def __init__(self, model_path: str | Path, min_confidence: float = 0.5):
        from mediapipe.tasks.python import BaseOptions, vision

        opts = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=min_confidence,
            min_pose_presence_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
        )
        self._landmarker = vision.PoseLandmarker.create_from_options(opts)
        self._last_ms = -1

    def detect(self, frame_bgr: np.ndarray, t_us: int) -> Pose | None:
        import cv2
        import mediapipe as mp

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        t_ms = max(t_us // 1000, self._last_ms + 1)  # VIDEO mode needs increasing timestamps
        self._last_ms = t_ms
        result = self._landmarker.detect_for_video(image, t_ms)
        if not result.pose_landmarks:
            return None
        h, w = frame_bgr.shape[:2]
        img = np.zeros((NUM_LANDMARKS, 3))
        for i, lm in enumerate(result.pose_landmarks[0][:NUM_LANDMARKS]):
            vis = lm.visibility if lm.visibility is not None else 1.0
            img[i] = (lm.x * w, lm.y * h, vis)
        world = None
        if result.pose_world_landmarks:
            world = np.array([(lm.x, lm.y, lm.z) for lm in result.pose_world_landmarks[0][:NUM_LANDMARKS]])
        return Pose(t_us, img, world)

    def close(self) -> None:
        self._landmarker.close()

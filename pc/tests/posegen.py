"""Synthetic MediaPipe-style poses for Tier 0 tests (no camera needed)."""

from __future__ import annotations

import numpy as np

from bodyctl.gestures import webcam as W
from bodyctl.gestures.webcam import Pose

FPS = 30
TORSO_PX = 150.0
TORSO_M = 0.5

# Image pixels (640x480 frame, person facing the camera: their left is image right).
_IMG = {
    W.NOSE: (320, 90),
    W.L_SHOULDER: (370, 150),
    W.R_SHOULDER: (270, 150),
    W.L_ELBOW: (390, 230),
    W.R_ELBOW: (250, 230),
    W.L_WRIST: (395, 300),
    W.R_WRIST: (245, 300),
    W.L_HIP: (355, 300),
    W.R_HIP: (285, 300),
    W.L_KNEE: (355, 400),
    W.R_KNEE: (285, 400),
    W.L_ANKLE: (355, 470),
    W.R_ANKLE: (285, 470),
}
# World metres, hip-centred, y down, z toward the camera negative.
_WORLD = {
    W.L_SHOULDER: (0.18, -0.5, 0.0),
    W.R_SHOULDER: (-0.18, -0.5, 0.0),
    W.L_ELBOW: (0.22, -0.25, 0.0),
    W.R_ELBOW: (-0.22, -0.25, 0.0),
    W.L_WRIST: (0.24, 0.0, 0.0),
    W.R_WRIST: (-0.24, 0.0, 0.0),
    W.L_HIP: (0.12, 0.0, 0.0),
    W.R_HIP: (-0.12, 0.0, 0.0),
    W.L_KNEE: (0.12, 0.45, 0.0),
    W.R_KNEE: (-0.12, 0.45, 0.0),
}


def standing() -> tuple[np.ndarray, np.ndarray]:
    img = np.zeros((W.NUM_LANDMARKS, 3))
    img[:, 2] = 0.99
    img[:, :2] = (320, 240)
    for i, xy in _IMG.items():
        img[i, :2] = xy
    world = np.zeros((W.NUM_LANDMARKS, 3))
    for i, p in _WORLD.items():
        world[i] = p
    return img, world


class Timeline:
    """Builds a frame sequence; each frame starts from the standing pose plus offsets."""

    def __init__(self, seconds: float, fps: int = FPS):
        self.fps = fps
        self.n = int(seconds * fps)
        base_img, base_world = standing()
        self.img = np.repeat(base_img[None], self.n, axis=0)
        self.world = np.repeat(base_world[None], self.n, axis=0)

    def idx(self, t_s: float) -> int:
        return int(round(t_s * self.fps))

    def t_us(self, i: int) -> int:
        return int(round(i * 1e6 / self.fps))

    def poses(self, with_world: bool = True) -> list[Pose]:
        return [
            Pose(self.t_us(i), self.img[i].copy(), self.world[i].copy() if with_world else None)
            for i in range(self.n)
        ]

    def knee_lift(self, t0_s: float, dur_s: float, knee: int, lift_torso: float = 0.35) -> None:
        i0, i1 = self.idx(t0_s), self.idx(t0_s + dur_s)
        k = np.arange(i1 - i0)
        self.img[i0:i1, knee, 1] -= lift_torso * TORSO_PX * np.sin(np.pi * (k + 0.5) / (i1 - i0))

    def run(self, t0_s: float, t1_s: float, cadence_hz: float) -> list[float]:
        """Alternating knee lifts; returns the step start times."""
        t, k, starts = t0_s, 0, []
        while t < t1_s:
            self.knee_lift(t, 0.6 / cadence_hz, W.L_KNEE if k % 2 == 0 else W.R_KNEE)
            starts.append(t)
            t += 1.0 / cadence_hz
            k += 1
        return starts

    def shift_body(self, i0: int, i1: int, dy_px: np.ndarray) -> None:
        self.img[i0:i1, :, 1] += dy_px[:, None]

    def jump(self, t0_s: float, dip_torso: float = 0.15, dip_s: float = 0.2, air_s: float = 0.35) -> float:
        """Dip, push up, flight, land. Returns the reversal time (bottom of the dip)."""
        i0 = self.idx(t0_s)
        nd = self.idx(dip_s)
        k = np.arange(nd)
        self.shift_body(i0, i0 + nd, dip_torso * TORSO_PX * np.sin(0.5 * np.pi * k / nd))
        rev = i0 + nd
        na = self.idx(air_s)
        k = np.arange(na)
        path = dip_torso * TORSO_PX * np.cos(np.pi * k / na) - 0.5 * TORSO_PX * np.sin(np.pi * k / na)
        self.shift_body(rev, rev + na, path)
        return rev / self.fps

    def squat_hold(self, t0_s: float, hold_s: float, depth_torso: float = 0.2) -> None:
        i0 = self.idx(t0_s)
        nd = self.idx(0.4)
        k = np.arange(nd)
        self.shift_body(i0, i0 + nd, depth_torso * TORSO_PX * np.sin(0.5 * np.pi * k / nd))
        nh = self.idx(hold_s)
        self.shift_body(i0 + nd, i0 + nd + nh, np.full(nh, depth_torso * TORSO_PX))
        nu = self.idx(0.6)
        k = np.arange(nu)
        self.shift_body(
            i0 + nd + nh, i0 + nd + nh + nu, depth_torso * TORSO_PX * np.cos(0.5 * np.pi * k / nu)
        )

    def lean(self, t0_s: float, t1_s: float, deg: float) -> None:
        """Positive = lean to the person's right: their right shoulder (image left) drops."""
        i0, i1 = self.idx(t0_s), self.idx(t1_s)
        half = (_IMG[W.L_SHOULDER][0] - _IMG[W.R_SHOULDER][0]) / 2
        dy = half * np.tan(np.radians(deg))
        self.img[i0:i1, W.R_SHOULDER, 1] += dy
        self.img[i0:i1, W.L_SHOULDER, 1] -= dy

    def _set_right_arm(self, i: int, elbow_w, wrist_w) -> None:
        self.world[i, W.R_ELBOW] = elbow_w
        self.world[i, W.R_WRIST] = wrist_w
        # Rough image projection: forward reach foreshortens toward the shoulder.
        sx, sy = _IMG[W.R_SHOULDER]
        for j, p in ((W.R_ELBOW, elbow_w), (W.R_WRIST, wrist_w)):
            self.img[i, j, 0] = sx + (p[0] + 0.18) * TORSO_PX / TORSO_M
            self.img[i, j, 1] = sy + (p[1] + 0.5) * TORSO_PX / TORSO_M

    def punch(self, t0_s: float) -> None:
        guard_e, guard_w = np.array((-0.25, -0.3, 0.0)), np.array((-0.16, -0.42, -0.1))
        ext_e, ext_w = np.array((-0.18, -0.5, -0.3)), np.array((-0.18, -0.5, -0.6))
        i0 = self.idx(t0_s)
        for k in range(self.idx(0.3)):  # guard
            self._set_right_arm(i0 - self.idx(0.3) + k, guard_e, guard_w)
        n = 3  # 100 ms at 30 fps
        for k in range(1, n + 1):
            a = k / n
            self._set_right_arm(i0 + k - 1, guard_e + a * (ext_e - guard_e), guard_w + a * (ext_w - guard_w))
        self._set_right_arm(i0 + n, ext_e, ext_w)
        for k in range(1, 4):  # retract
            a = k / 3
            self._set_right_arm(i0 + n + k, ext_e + a * (guard_e - ext_e), ext_w + a * (guard_w - ext_w))

    def aim(self, t0_s: float, t1_s: float) -> None:
        for i in range(self.idx(t0_s), self.idx(t1_s)):
            self._set_right_arm(i, np.array((-0.19, -0.48, -0.25)), np.array((-0.19, -0.48, -0.5)))

"""Tier 0 gestures from MediaPipe pose landmarks (webcam only).

Distances are in torso lengths (shoulder center to hip center) so thresholds
don't depend on how far you stand from the camera. Each detector fires as early
as the camera allows: a step on the knee crossing its lift threshold (not at the
peak), a jump on the dip reversing upward.

IMU/EMG gestures are NOT here: they live in firmware/lib/gestures (hard rule 5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from bodyctl.config.profile import Gestures

# BlazePose landmark indices.
NOSE = 0
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28
NUM_LANDMARKS = 33


@dataclass
class Pose:
    """One frame of landmarks.

    img:   (33, 3) pixel x, pixel y (down), visibility 0..1
    world: (33, 3) metres, hip-centred, x right, y down, z toward the camera negative;
           None if unavailable (then punch and aim fall back to image coordinates).
    """

    t_us: int
    img: np.ndarray
    world: np.ndarray | None = None


@dataclass
class GestureEvent:
    name: str  # jump, crouch, step, run_start, run_stop, punch, aim_start, aim_end
    t_us: int
    confidence: float


@dataclass
class BodyState:
    visible: bool = False
    cadence_hz: float = 0.0
    run_stick: float = 0.0  # 0..1 forward
    lean_deg: float = 0.0  # + = leaning right
    lean_stick: float = 0.0  # -1 (left) .. 1 (right)
    twist_stick: float = 0.0  # not tracked on Tier 0
    aiming: bool = False
    sprinting: bool = False
    running: bool = False

    def source(self, name: str) -> float:
        """Continuous sources referenced by profile bindings."""
        return {
            "run_cadence": self.run_stick,
            "lean": self.lean_stick,
            "twist": self.twist_stick,
            "stroke_rhythm": self.run_stick,
            "step_back_or_lean_back": 0.0,
            "hand_height": 0.0,
        }.get(name, 0.0)

    def condition(self, name: str) -> bool:
        return {
            "aiming": self.aiming,
            "cadence_above_sprint": self.sprinting,
            "stopped": not self.running,
        }.get(name, False)


def deadzone_map(value: float, deadzone: float, full: float) -> float:
    """Signed value -> -1..1 with a deadzone; |value| >= full gives full deflection."""
    mag = (abs(value) - deadzone) / (full - deadzone)
    return math.copysign(min(1.0, max(0.0, mag)), value) if mag > 0 else 0.0


def cadence_to_stick(cadence_hz: float, curve: list[float]) -> float:
    """Cadence (steps/s) -> forward stick 0..1: walk speed at curve[0], full run at curve[1]."""
    if cadence_hz <= 0:
        return 0.0
    return float(np.interp(cadence_hz, [0.0, curve[0], curve[1]], [0.0, 0.5, 1.0]))


@dataclass
class _Leg:
    knee: int
    baseline: float | None = None
    lifted: bool = False


@dataclass
class _Arm:
    shoulder: int
    elbow: int
    wrist: int
    last_reach: float | None = None
    last_speed: float = 0.0
    last_t_us: int = 0
    last_punch_us: int = -(10**12)


@dataclass
class WebcamGestures:
    params: Gestures
    state: BodyState = field(default_factory=BodyState)

    def __post_init__(self) -> None:
        self._legs = [_Leg(L_KNEE), _Leg(R_KNEE)]
        self._arms = [_Arm(L_SHOULDER, L_ELBOW, L_WRIST), _Arm(R_SHOULDER, R_ELBOW, R_WRIST)]
        self._last_step_us: int | None = None
        self._last_step_leg = -1
        self._last_interval_s: float | None = None
        self._hip_base: float | None = None
        self._hip_hist: list[tuple[int, float]] = []
        self._dip_start_us: int | None = None
        self._crouch_sent = False
        self._last_jump_us = -(10**12)
        self._aim_since_us: int | None = None

    # -- helpers -------------------------------------------------------------

    def _visible(self, pose: Pose, *idx: int) -> bool:
        return bool(np.all(pose.img[list(idx), 2] >= self.params.webcam.min_visibility))

    @staticmethod
    def _mid(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return (a + b) / 2

    # -- main entry ----------------------------------------------------------

    def update(self, pose: Pose | None) -> list[GestureEvent]:
        """Feed one frame (None = no person found). Returns events fired this frame."""
        events: list[GestureEvent] = []
        if pose is None or not self._visible(pose, L_SHOULDER, R_SHOULDER, L_HIP, R_HIP):
            self.state.visible = False
            self._check_stop(pose.t_us if pose else None, events)
            if self.state.aiming:
                self.state.aiming = False
                events.append(GestureEvent("aim_end", pose.t_us if pose else 0, 1.0))
            self._aim_since_us = None
            return events
        self.state.visible = True
        xy = pose.img[:, :2]
        sh = self._mid(xy[L_SHOULDER], xy[R_SHOULDER])
        hip = self._mid(xy[L_HIP], xy[R_HIP])
        torso = float(np.linalg.norm(sh - hip))
        if torso < 1e-6:
            return events
        conf = float(np.clip(pose.img[[L_SHOULDER, R_SHOULDER, L_HIP, R_HIP], 2].min(), 0, 1))
        self._run(pose, hip, torso, conf, events)
        self._jump(pose, hip, torso, conf, events)
        self._lean(pose)
        self._punch(pose, torso, events)
        self._aim(pose, torso, events)
        return events

    # -- run -----------------------------------------------------------------

    def _run(
        self, pose: Pose, hip: np.ndarray, torso: float, conf: float, events: list[GestureEvent]
    ) -> None:
        p = self.params
        t = pose.t_us
        for li, leg in enumerate(self._legs):
            if not self._visible(pose, leg.knee):
                continue
            s = (pose.img[leg.knee, 1] - hip[1]) / torso  # knee below hip, in torso lengths
            if leg.baseline is None:
                leg.baseline = s
            lift = leg.baseline - s
            if not leg.lifted and lift > p.webcam.knee_lift:
                leg.lifted = True
                self._step(li, t, conf, events)
            elif leg.lifted and lift < p.webcam.knee_lift / 2:
                leg.lifted = False
            if not leg.lifted and lift < p.webcam.knee_lift / 2:
                # Standing level drifts slowly (posture, camera); only learn it while down.
                leg.baseline += 0.02 * (s - leg.baseline)
        self._check_stop(t, events)

    def _step(self, leg: int, t_us: int, conf: float, events: list[GestureEvent]) -> None:
        p = self.params
        if self._last_step_us is not None:
            dt = (t_us - self._last_step_us) / 1e6
            if dt * 1000 < p.step.refractory_ms:
                return
            if leg == self._last_step_leg and dt < 0.6:
                return  # same knee twice in quick succession: one step, not two
        if not self.state.running or self._last_step_us is None:
            self.state.running = True
            self.state.cadence_hz = p.cadence_curve[0]
            self._last_interval_s = None
            events.append(GestureEvent("run_start", t_us, conf))
        else:
            dt = (t_us - self._last_step_us) / 1e6
            inst = 1.0 / dt
            self._last_interval_s = dt
            self.state.cadence_hz += 0.5 * (inst - self.state.cadence_hz)
        self._last_step_us = t_us
        self._last_step_leg = leg
        events.append(GestureEvent("step", t_us, conf))
        self._update_run_outputs()

    def _check_stop(self, t_us: int | None, events: list[GestureEvent]) -> None:
        if not self.state.running or self._last_step_us is None or t_us is None:
            return
        interval = self._last_interval_s or (1.0 / self.params.cadence_curve[0])
        if (t_us - self._last_step_us) / 1e6 > self.params.step.stop_after_intervals * interval:
            self.state.running = False
            self.state.cadence_hz = 0.0
            self._last_step_us = None
            events.append(GestureEvent("run_stop", t_us, 1.0))
            self._update_run_outputs()

    def _update_run_outputs(self) -> None:
        curve = self.params.cadence_curve
        self.state.run_stick = cadence_to_stick(self.state.cadence_hz, curve)
        self.state.sprinting = self.state.running and self.state.cadence_hz >= curve[2]

    # -- jump ----------------------------------------------------------------

    def _jump(
        self, pose: Pose, hip: np.ndarray, torso: float, conf: float, events: list[GestureEvent]
    ) -> None:
        p = self.params
        t = pose.t_us
        h = hip[1] / torso  # image y grows downward: a dip makes h larger
        self._hip_hist.append((t, h))
        self._hip_hist = self._hip_hist[-4:]
        if self._hip_base is None:
            self._hip_base = h
            return
        vel = 0.0
        if len(self._hip_hist) >= 2:
            (t0, h0), (t1, h1) = self._hip_hist[-2], self._hip_hist[-1]
            if t1 > t0:
                vel = (h1 - h0) / ((t1 - t0) / 1e6)  # torso/s, negative = rising
        rel = h - self._hip_base
        in_dip = rel > p.webcam.jump_dip
        since_jump_ms = (t - self._last_jump_us) / 1000
        if in_dip and self._dip_start_us is None:
            self._dip_start_us = t
            self._crouch_sent = False
        if self._dip_start_us is not None:
            dip_ms = (t - self._dip_start_us) / 1000
            rising = vel < -p.webcam.jump_rise_speed
            if rising and dip_ms >= p.jump.dip_min_ms and since_jump_ms > p.webcam.jump_refractory_ms:
                events.append(GestureEvent("jump", t, conf))
                self._last_jump_us = t
                self._dip_start_us = None
            elif not in_dip and not rising:
                self._dip_start_us = None  # came back up slowly: just a bob
            elif dip_ms >= p.jump.crouch_hold_ms and not self._crouch_sent:
                events.append(GestureEvent("crouch", t, conf))
                self._crouch_sent = True
        elif rel < -1.5 * p.webcam.jump_dip and vel < -p.webcam.jump_rise_speed:
            # Jumped without a visible dip: fire late rather than never.
            if since_jump_ms > p.webcam.jump_refractory_ms:
                events.append(GestureEvent("jump", t, conf * 0.8))
                self._last_jump_us = t
        if abs(rel) < p.webcam.jump_dip / 2 and abs(vel) < p.webcam.jump_rise_speed / 2:
            self._hip_base += 0.02 * (h - self._hip_base)

    # -- lean ----------------------------------------------------------------

    def _lean(self, pose: Pose) -> None:
        lp = self.params.lean
        ls, rs = pose.img[L_SHOULDER, :2], pose.img[R_SHOULDER, :2]
        # Person's left shoulder dropping = leaning left. Image x of the left shoulder
        # is larger (camera faces the person), so use the absolute width.
        dy = ls[1] - rs[1]
        dx = abs(ls[0] - rs[0])
        self.state.lean_deg = -math.degrees(math.atan2(dy, max(dx, 1e-6)))
        self.state.lean_stick = deadzone_map(self.state.lean_deg, lp.deadzone_deg, lp.full_deg)

    # -- arms ----------------------------------------------------------------

    def _arm_points(self, pose: Pose, arm: _Arm, torso_px: float) -> tuple[np.ndarray, float]:
        """Arm landmarks in a common unit (torso lengths), 3D if world landmarks exist."""
        if pose.world is not None:
            w = pose.world
            torso_w = float(
                np.linalg.norm(self._mid(w[L_SHOULDER], w[R_SHOULDER]) - self._mid(w[L_HIP], w[R_HIP]))
            )
            return w[[arm.shoulder, arm.elbow, arm.wrist]] / max(torso_w, 1e-6), 1.0
        pts = np.zeros((3, 3))
        pts[:, :2] = pose.img[[arm.shoulder, arm.elbow, arm.wrist], :2] / torso_px
        return pts, 1.0

    def _punch(self, pose: Pose, torso_px: float, events: list[GestureEvent]) -> None:
        wp = self.params.webcam
        for arm in self._arms:
            if not self._visible(pose, arm.shoulder, arm.elbow, arm.wrist):
                arm.last_reach = None
                continue
            (s, e, w), _ = self._arm_points(pose, arm, torso_px)
            reach = float(np.linalg.norm(w - s))
            ua, fa = s - e, w - e
            cosang = float(np.dot(ua, fa) / max(np.linalg.norm(ua) * np.linalg.norm(fa), 1e-9))
            elbow_deg = math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
            if arm.last_reach is not None and pose.t_us > arm.last_t_us:
                speed = (reach - arm.last_reach) / ((pose.t_us - arm.last_t_us) / 1e6)
                arm.last_speed = speed
                if (
                    speed > wp.punch_speed
                    and elbow_deg > wp.punch_elbow_deg
                    and (pose.t_us - arm.last_punch_us) / 1000 > wp.punch_refractory_ms
                ):
                    conf = float(pose.img[[arm.shoulder, arm.elbow, arm.wrist], 2].min())
                    events.append(GestureEvent("punch", pose.t_us, conf))
                    arm.last_punch_us = pose.t_us
            else:
                arm.last_speed = 0.0
            arm.last_reach = reach
            arm.last_t_us = pose.t_us

    def _aim(self, pose: Pose, torso_px: float, events: list[GestureEvent]) -> None:
        wp = self.params.webcam
        arm = self._arms[1]  # right arm aims
        aim_pose = False
        if self._visible(pose, arm.shoulder, arm.elbow, arm.wrist):
            (s, _e, w), _ = self._arm_points(pose, arm, torso_px)
            if pose.world is not None:
                forward = s[2] - w[2]  # wrist closer to the camera than the shoulder
                height = abs(w[1] - s[1])
                aim_pose = forward > wp.aim_forward and height < wp.aim_height
            else:
                # 2D fallback: wrist raised to shoulder height and close to it in the image
                # (an arm pointing at the camera foreshortens).
                aim_pose = abs(w[1] - s[1]) < wp.aim_height and np.linalg.norm(w[:2] - s[:2]) < 0.6
        t = pose.t_us
        # A punch passes through the aim pose: only a steady arm aims, and the hold
        # timer restarts after every punch.
        moving = abs(arm.last_speed) > wp.punch_speed / 4
        recent_punch = (t - arm.last_punch_us) / 1000 < wp.punch_refractory_ms
        if aim_pose and (moving or recent_punch):
            self._aim_since_us = None
        elif aim_pose:
            if self._aim_since_us is None:
                self._aim_since_us = t
            if not self.state.aiming and (t - self._aim_since_us) / 1000 >= wp.aim_hold_ms:
                self.state.aiming = True
                events.append(GestureEvent("aim_start", t, 1.0))
        else:
            self._aim_since_us = None
            if self.state.aiming:
                self.state.aiming = False
                events.append(GestureEvent("aim_end", t, 1.0))

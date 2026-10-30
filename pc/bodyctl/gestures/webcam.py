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
    name: str  # jump, crouch, step, run_start, run_stop, punch, aim_start, aim_end, shoot,
    # wheel_open, wheel_close, stand (back up after a crouch), fly_start, fly_end
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
    crouching: bool = False  # between a crouch event and standing back up
    wheel_open: bool = False  # weapon wheel held open (right hand over the shoulder)
    wheel_x: float = 0.0  # where the left hand points while it's open, -1..1 (right +)
    wheel_y: float = 0.0  # -1..1 (up +); both 0 while the arm rests
    flying: bool = False  # arms spread like wings
    fly_roll: float = 0.0  # -1 (left wing down) .. 1 (right wing down)
    fly_pitch: float = 0.0  # -1 (hands up: nose up, stick back) .. 1 (hands down: nose down)

    def source(self, name: str) -> float:
        """Continuous sources referenced by profile bindings."""
        return {
            "run_cadence": self.run_stick,
            "lean": self.lean_stick,
            "twist": self.twist_stick,
            "stroke_rhythm": self.run_stick,
            "step_back_or_lean_back": 0.0,
            "hand_height": 0.0,
            "wheel_x": self.wheel_x,
            "wheel_y": self.wheel_y,
            "fly_roll": self.fly_roll,
            "fly_pitch": self.fly_pitch,
        }.get(name, 0.0)

    def condition(self, name: str) -> bool:
        return {
            "aiming": self.aiming,
            "cadence_above_sprint": self.sprinting,
            "stopped": not self.running,
            "wheel_open": self.wheel_open,
            "flying": self.flying,
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


class OneEuro:
    """One Euro filter (Casiez et al., CHI 2012) over an array of coordinates: a low-pass
    whose cutoff opens with speed, so a still pose stops jittering while fast moves keep
    their timing. Speeds are divided by `scale` (a torso length), so beta is per
    torso length/s and doesn't depend on the camera distance or resolution."""

    def __init__(self, min_cutoff_hz: float, beta: float, d_cutoff_hz: float):
        self.min_cutoff_hz, self.beta, self.d_cutoff_hz = min_cutoff_hz, beta, d_cutoff_hz
        self._x: np.ndarray | None = None
        self._dx: np.ndarray | None = None
        self._t_us = 0

    @staticmethod
    def _alpha(cutoff_hz, dt_s: float):
        return 1.0 / (1.0 + 1.0 / (2 * math.pi * cutoff_hz * dt_s))

    def reset(self) -> None:
        self._x = None

    def __call__(self, t_us: int, x: np.ndarray, scale: float) -> np.ndarray:
        if self._x is None or t_us <= self._t_us:
            self._x, self._dx, self._t_us = x.astype(float).copy(), np.zeros(x.shape), t_us
            return x
        dt = (t_us - self._t_us) / 1e6
        self._dx = self._dx + self._alpha(self.d_cutoff_hz, dt) * ((x - self._x) / dt - self._dx)
        cutoff = self.min_cutoff_hz + self.beta * np.abs(self._dx) / max(scale, 1e-6)
        self._x = self._x + self._alpha(cutoff, dt) * (x - self._x)
        self._t_us = t_us
        return self._x.copy()


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
        self._aim_lost_us: int | None = None
        self._flick: tuple[int, float] | None = None  # (t_us, wrist height above shoulder)
        self._last_shot_us = -(10**12)
        self._wheel_since_us: int | None = None
        self._wheel_lost_us: int | None = None
        self._fly_since_us: int | None = None
        self._fly_lost_us: int | None = None
        wp = self.params.webcam
        self._smooth = wp.smooth_min_cutoff_hz > 0
        args = (wp.smooth_min_cutoff_hz, wp.smooth_beta, wp.smooth_d_cutoff_hz)
        self._filter_img, self._filter_world = OneEuro(*args), OneEuro(*args)
        self._torso_px: float | None = None

    # -- helpers -------------------------------------------------------------

    def _visible(self, pose: Pose, *idx: int) -> bool:
        return bool(np.all(pose.img[list(idx), 2] >= self.params.webcam.min_visibility))

    @staticmethod
    def _mid(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return (a + b) / 2

    def _smoothed(self, pose: Pose) -> Pose:
        """Landmark positions through the One Euro filters (visibility untouched)."""
        xy = pose.img[:, :2]
        torso = float(
            np.linalg.norm(self._mid(xy[L_SHOULDER], xy[R_SHOULDER]) - self._mid(xy[L_HIP], xy[R_HIP]))
        )
        if torso > 1e-6:  # slow average: the filter's speed unit shouldn't jump with noise
            self._torso_px = (
                torso if self._torso_px is None else self._torso_px + 0.05 * (torso - self._torso_px)
            )
        if self._torso_px is None:
            return pose
        img = pose.img.copy()
        img[:, :2] = self._filter_img(pose.t_us, xy, self._torso_px)
        world = None
        if pose.world is not None:
            w = pose.world
            torso_m = float(
                np.linalg.norm(self._mid(w[L_SHOULDER], w[R_SHOULDER]) - self._mid(w[L_HIP], w[R_HIP]))
            )
            world = self._filter_world(pose.t_us, w, torso_m or 0.5)
        return Pose(pose.t_us, img, world)

    # -- main entry ----------------------------------------------------------

    def update(self, pose: Pose | None) -> list[GestureEvent]:
        """Feed one frame (None = no person found). Returns events fired this frame."""
        events: list[GestureEvent] = []
        if self._smooth:
            if pose is None:
                self._filter_img.reset()
                self._filter_world.reset()
            else:
                pose = self._smoothed(pose)
        if pose is None or not self._visible(pose, L_SHOULDER, R_SHOULDER, L_HIP, R_HIP):
            self.state.visible = False
            self._check_stop(pose.t_us if pose else None, events)
            t = pose.t_us if pose else 0
            if self.state.aiming:
                self.state.aiming = False
                events.append(GestureEvent("aim_end", t, 1.0))
            if self.state.wheel_open:
                self._close_wheel(t, events)
            if self.state.flying:
                self._land(t, events)
            self._aim_since_us = self._aim_lost_us = self._flick = self._wheel_since_us = None
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
        self._wheel(pose, torso, events)
        self._fly(pose, torso, events)
        self._punch(pose, torso, events)
        self._aim(pose, torso, events)
        self._shoot(pose, torso, events)
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
                self.state.crouching = True
        elif rel < -1.5 * p.webcam.jump_dip and vel < -p.webcam.jump_rise_speed:
            # Jumped without a visible dip: fire late rather than never.
            if since_jump_ms > p.webcam.jump_refractory_ms:
                events.append(GestureEvent("jump", t, conf * 0.8))
                self._last_jump_us = t
        if self.state.crouching and rel < p.webcam.jump_dip / 2:  # hips back up: stood up
            self.state.crouching = False
            events.append(GestureEvent("stand", t, conf))
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
        # Reaching for the wheel, pointing and spreading wings aren't punches.
        quiet = self.state.wheel_open or self._wheel_since_us is not None or self._fly_since_us is not None
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
                    not quiet
                    and speed > wp.punch_speed
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
        if aim_pose:
            self._aim_lost_us = None
        if aim_pose and (moving or recent_punch) and not self.state.aiming:
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
                # Keep aiming through a short excursion (a recoil flick leaves the pose).
                self._aim_lost_us = self._aim_lost_us or t
                if (t - self._aim_lost_us) / 1000 >= wp.aim_release_ms:
                    self.state.aiming = False
                    self._aim_lost_us = None
                    events.append(GestureEvent("aim_end", t, 1.0))

    # -- shoot -----------------------------------------------------------------

    def _shoot(self, pose: Pose, torso_px: float, events: list[GestureEvent]) -> None:
        """Recoil flick: while aiming, the right wrist jumps up relative to the shoulder.
        Costly action (rule 7): gated on aiming here and in the binding, and on the arm
        keypoints' confidence."""
        wp = self.params.webcam
        arm = self._arms[1]
        if not self.state.aiming or not self._visible(pose, arm.shoulder, arm.wrist):
            self._flick = None
            return
        (s, _e, w), _ = self._arm_points(pose, arm, torso_px)
        up = float(s[1] - w[1])  # y grows downward in image and world coordinates
        t = pose.t_us
        if self._flick is not None and t > self._flick[0]:
            speed = (up - self._flick[1]) / ((t - self._flick[0]) / 1e6)
            conf = float(pose.img[[arm.shoulder, arm.elbow, arm.wrist], 2].min())
            if (
                speed > wp.shoot_flick_speed
                and conf >= wp.shoot_min_conf
                and (t - self._last_shot_us) / 1000 > wp.shoot_refractory_ms
            ):
                events.append(GestureEvent("shoot", t, conf))
                self._last_shot_us = t
        self._flick = (t, up)

    # -- weapon wheel ------------------------------------------------------------

    def _wheel(self, pose: Pose, torso_px: float, events: list[GestureEvent]) -> None:
        """Right hand reaches over the right shoulder (to a backpack) and stays: the wheel
        opens; the left hand's direction picks the slot; dropping the right hand closes it
        (GTA equips the highlighted weapon)."""
        wp = self.params.webcam
        xy = pose.img[:, :2] / torso_px
        t = pose.t_us
        reach = False
        if self._visible(pose, R_SHOULDER, R_WRIST):
            above = xy[R_SHOULDER, 1] - xy[R_WRIST, 1]
            reach = above > wp.wheel_reach and abs(xy[R_WRIST, 0] - xy[R_SHOULDER, 0]) < wp.wheel_side
        if reach:
            self._wheel_lost_us = None
            self._wheel_since_us = self._wheel_since_us or t
            if not self.state.wheel_open and (t - self._wheel_since_us) / 1000 >= wp.wheel_hold_ms:
                self.state.wheel_open = True
                events.append(GestureEvent("wheel_open", t, 1.0))
        else:
            self._wheel_since_us = None
            if self.state.wheel_open:
                self._wheel_lost_us = self._wheel_lost_us or t
                if (t - self._wheel_lost_us) / 1000 >= wp.wheel_release_ms:
                    self._close_wheel(t, events)
        x = y = 0.0
        if self.state.wheel_open and self._visible(pose, L_SHOULDER, L_WRIST):
            d = xy[L_WRIST] - xy[L_SHOULDER]
            length = float(np.linalg.norm(d))
            # Angle from straight down; a hanging arm is resting, not pointing.
            from_down = math.degrees(math.acos(max(-1.0, min(1.0, d[1] / max(length, 1e-9)))))
            if length > wp.wheel_point_min and from_down > wp.wheel_rest_deg:
                # The camera faces you: your right is image left, so x flips; image y is down.
                x, y = -d[0] / length, -d[1] / length
        self.state.wheel_x, self.state.wheel_y = x, y

    # -- flight ----------------------------------------------------------------

    def _fly(self, pose: Pose, torso_px: float, events: list[GestureEvent]) -> None:
        """Arms spread like wings: flying. The wing line's tilt rolls (your left hand
        lower = roll left); both hands above the shoulders pull the nose up, below push
        it down."""
        wp = self.params.webcam
        t = pose.t_us
        spread = False
        if self._visible(pose, L_WRIST, R_WRIST, L_SHOULDER, R_SHOULDER):
            xy = pose.img[:, :2] / torso_px
            span = float(np.linalg.norm(xy[L_WRIST] - xy[R_WRIST]))
            spread = span > wp.fly_span
        if spread:
            self._fly_lost_us = None
            self._fly_since_us = self._fly_since_us or t
            if not self.state.flying and (t - self._fly_since_us) / 1000 >= wp.fly_hold_ms:
                self.state.flying = True
                events.append(GestureEvent("fly_start", t, 1.0))
        else:
            self._fly_since_us = None
            if self.state.flying:
                self._fly_lost_us = self._fly_lost_us or t
                if (t - self._fly_lost_us) / 1000 >= wp.fly_release_ms:
                    self._land(t, events)
        roll = pitch = 0.0
        if self.state.flying and spread:
            # Camera faces you: your left hand is on the image right, so the line runs
            # right hand -> left hand along +x; your left hand lower (larger y) = roll left.
            d = xy[L_WRIST] - xy[R_WRIST]
            tilt = math.degrees(math.atan2(d[1], abs(d[0])))
            roll = -deadzone_map(tilt, wp.fly_roll_deadzone_deg, wp.fly_roll_full_deg)
            hands_up = float(
                self._mid(xy[L_SHOULDER], xy[R_SHOULDER])[1] - self._mid(xy[L_WRIST], xy[R_WRIST])[1]
            )
            pitch = -deadzone_map(hands_up, wp.fly_pitch_deadzone, wp.fly_pitch_full)
        self.state.fly_roll, self.state.fly_pitch = roll, pitch

    def _land(self, t_us: int, events: list[GestureEvent]) -> None:
        self.state.flying = False
        self.state.fly_roll = self.state.fly_pitch = 0.0
        self._fly_lost_us = None
        events.append(GestureEvent("fly_end", t_us, 1.0))

    def _close_wheel(self, t_us: int, events: list[GestureEvent]) -> None:
        self.state.wheel_open = False
        self._wheel_lost_us = None
        events.append(GestureEvent("wheel_close", t_us, 1.0))

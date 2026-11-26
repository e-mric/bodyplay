"""Tier 0 gestures from MediaPipe pose landmarks (webcam only).

Distances are in torso lengths (shoulder center to hip center) so thresholds
don't depend on how far you stand from the camera. Each detector fires as early
as the camera allows: a step on the knee crossing its lift threshold (not at the
peak), a jump on the dip reversing upward. Dips and crouches are measured as the
knees bending (the hip-to-knee gap shrinking), so stepping closer or farther from
the camera doesn't look like crouching.

IMU/EMG gestures are NOT here: they live in firmware/lib/gestures (hard rule 5).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from bodyctl.config.profile import Gestures

# Fitness mode exercises looked for only when picked (they look like play otherwise), and
# the ones whose bar starts on the rack, lifted off as the set starts.
GYM_ONLY = ("squat", "deadlift", "bench", "bag")
TOP_START = ("squat", "bench")

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
    # wheel_open, wheel_close, stand (back up after a crouch), fly_start, fly_end,
    # workout_start, workout_end, pushup, pullup, kettlebell, squat, deadlift, bench, bag_hit
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
    aim_x: float = 0.0  # aim nudge, -1..1 (your right +): the aiming hand moved from where aiming began
    aim_y: float = 0.0  # -1..1 (up +)
    aim_anchor_px: tuple[float, float] | None = None  # pointer: where the hand started aiming (image px)
    aim_hand_px: tuple[float, float] | None = None  # and where it is now
    workout: str = ""  # "pushup" (plank), "pullup" (hanging), "kettlebell" (swings); "" = standing
    workout_reps: int = 0  # reps in the current set
    workout_progress: float = 0.0  # where the rep is: 0 bottom (arms straight) .. 1 top
    workout_rising: bool = False  # pressing: from starting up until going back down (held at the top)
    workout_struggling: bool = False  # rising but stuck: barely moving partway up
    exercise_name: str = ""  # fitness mode: the picked exercise, for display
    bag_hitting: bool = False  # punching bag: a punch landed this frame
    last_workout: str = ""  # the set that just ended (the bag has no "put down")

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
            "workout_press": self.workout_progress if self.workout_rising else 0.0,
            "aim_x": self.aim_x,
            "aim_y": self.aim_y,
        }.get(name, 0.0)

    def condition(self, name: str) -> bool:
        return {
            "aiming": self.aiming,
            "cadence_above_sprint": self.sprinting,
            "stopped": not self.running,
            "wheel_open": self.wheel_open,
            "flying": self.flying,
            "working_out": bool(self.workout),
            "lifting": bool(self.workout) and self.workout != "bag",  # a set with reps
            "workout_struggling": self.workout_struggling,
            "bag_hitting": self.bag_hitting,
            "exit_after_set": self.last_workout != "bag",
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
    peak: float = 0.0  # highest lift since this knee came up


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
    fitness: bool = False  # game in its fitness mode: gym exercises that look like play (swings) count
    exercise: str = ""  # fitness mode: the one exercise to look for ("" = any of them)

    def __post_init__(self) -> None:
        self._legs = [_Leg(L_KNEE), _Leg(R_KNEE)]
        self._arms = [_Arm(L_SHOULDER, L_ELBOW, L_WRIST), _Arm(R_SHOULDER, R_ELBOW, R_WRIST)]
        self._last_step_us: int | None = None
        self._last_step_leg = -1
        self._last_interval_s: float | None = None
        self._hip_base: float | None = None
        self._thigh_base: float | None = None  # standing hip-to-knee gap, torso lengths
        self._hip_hist: list[tuple[int, float]] = []
        self._dip_start_us: int | None = None
        self._crouch_sent = False
        self._last_jump_us = -(10**12)
        self._hurdle_up = [False, False]  # each knee: already counted as a hurdle this lift
        self._legs_moving_us = -(10**12)  # last time a knee was off its standing level
        self._alt_base: float | None = None  # left-minus-right foot+knee height while standing
        self._alt_side = 0  # which side is up: 1 left, -1 right, 0 level
        self._aim_since_us: int | None = None
        self._aim_lost_us: int | None = None
        self._flick: tuple[int, float] | None = None  # (t_us, wrist height above shoulder)
        self._aim_arm = 0 if self.params.webcam.aim_hand == "left" else 1  # index in _arms
        self._nudge_anchor: np.ndarray | None = None  # wrist - shoulder when aiming began, torso lengths
        self._nudge_last: tuple[int, np.ndarray] | None = None  # (t_us, wrist - shoulder) last frame
        self._nudge_fast = False  # the aiming hand moved fast last frame (flick, dropping the arm)
        self._last_shot_us = -(10**12)
        self._wheel_since_us: int | None = None
        self._wheel_lost_us: int | None = None
        self._fly_since_us: int | None = None
        self._fly_lost_us: int | None = None
        self._posture_since_us: int | None = None  # plank / hang seen since, set not started yet
        self._posture_lost_us: int | None = None
        self._gone_since_us: int | None = None  # nobody in view since (mid-set)
        self._rep_armed = False  # push-up: been down; pull-up: hung with straight arms
        self._tops: deque[tuple[int, float]] = deque()  # push-up: (t_us, shoulder height above hands)
        self._rep_last: tuple[int, float] | None = None  # (t_us, rep progress) last frame
        self._set_top = 0.0  # squat / deadlift / bench: the tallest measure this set (= the top)
        self._set_top_t_us: int | None = None
        # Punching bag, per arm (left, right): the guard position the punch starts from,
        # armed for the next punch, the farthest travel / extension since the last one.
        self._bag_last: tuple[int, np.ndarray] | None = None  # (t_us, wrists / torso) last frame
        self._bag_anchor: np.ndarray | None = None
        self._bag_hit_us: list[int | None] = [None, None]
        self._bag_armed = [True, True]
        self._bag_extension_anchor = [0.0, 0.0]
        self._bag_peak = [0.0, 0.0]
        self._bag_peak_ext = [0.0, 0.0]
        self._bag_rest_since_us: list[int | None] = [None, None]
        self._bag_fast_frames = [0, 0]
        self._rep_speed = 0.0  # rep progress per second, smoothed (+ = going up)
        self._stuck_since_us: int | None = None
        self._settling = False  # getting back up after a set: standing gestures wait
        self._settle_since_us = 0
        wp = self.params.webcam
        self._smooth = wp.smooth_min_cutoff_hz > 0
        args = (wp.smooth_min_cutoff_hz, wp.smooth_beta, wp.smooth_d_cutoff_hz)
        self._filter_img, self._filter_world = OneEuro(*args), OneEuro(*args)
        self._torso_px: float | None = None
        self._body_px: float | None = None  # torso scale: the recent tallest (bent over, it looks short)
        self._body_t_us: int | None = None
        self._knee_drop_px: float | None = None  # knees below the hips, last seen (hidden when hinged)

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

    def update(self, pose: Pose | None, t_us: int | None = None) -> list[GestureEvent]:
        """Feed one frame (None = no person found). `t_us` is the frame's time, needed only
        when there's no pose: a set ends once nobody has been in view for a while. Returns
        events fired this frame."""
        events: list[GestureEvent] = []
        self.state.exercise_name = (self.exercise.upper() or "AUTO") if self.fitness else ""
        if pose is not None:
            self._gone_since_us = None
        elif self.state.workout and t_us is not None:
            self._workout_gone(t_us, events)
        if self._smooth:
            if pose is None:
                self._filter_img.reset()
                self._filter_world.reset()
            else:
                pose = self._smoothed(pose)
        if pose is not None and self._workout(pose, events):
            # Down on the floor or hanging from a bar (or getting there):
            # nothing a standing body does means anything now.
            self.state.visible = True
            bag_hitting = self.state.bag_hitting
            self._release(pose.t_us, events)
            # `_workout` clears this pulse at the start of every frame and sets it
            # again only when a punch lands. Preserve it through standing-gesture
            # cleanup so the binding engine can send Square on this frame.
            self.state.bag_hitting = bag_hitting
            return events
        if self.fitness:  # the game's gym: only the exercise counts, nothing from play
            self.state.visible = pose is not None
            self._release(pose.t_us if pose else None, events)
            return events
        if pose is None or not self._visible(pose, L_SHOULDER, R_SHOULDER, L_HIP, R_HIP):
            self.state.visible = False
            self._release(pose.t_us if pose else None, events)
            return events
        self.state.visible = True
        xy = pose.img[:, :2]
        sh = self._mid(xy[L_SHOULDER], xy[R_SHOULDER])
        hip = self._mid(xy[L_HIP], xy[R_HIP])
        torso = float(np.linalg.norm(sh - hip))
        if torso < 1e-6:
            return events
        if self._settling and not self._settled(pose.t_us, self._thigh(pose, hip, torso)):
            self._release(pose.t_us, events)
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
        self._aim_nudge(pose, torso)
        return events

    def _release(self, t_us: int | None, events: list[GestureEvent]) -> None:
        """No standing gestures this frame: let go of everything held and forget the
        motion history, so the next frame that counts doesn't see a jump in it."""
        self.state.bag_hitting = False
        self._check_stop(t_us, events)
        t = t_us or 0
        self.state.lean_deg = self.state.lean_stick = 0.0  # a lost body doesn't keep leaning
        if self.state.aiming:
            self.state.aiming = False
            events.append(GestureEvent("aim_end", t, 1.0))
        if self.state.wheel_open:
            self._close_wheel(t, events)
        if self.state.flying:
            self._land(t, events)
        self._aim_since_us = self._aim_lost_us = self._flick = self._wheel_since_us = None
        self._fly_since_us = None
        self._end_nudge()
        self._hip_hist = []
        self._dip_start_us = None
        for arm in self._arms:
            arm.last_reach = None

    # -- workout ---------------------------------------------------------------

    def _elbow_deg(self, pose: Pose, arm: _Arm, torso_px: float) -> float:
        (s, e, w), _ = self._arm_points(pose, arm, torso_px)
        ua, fa = s - e, w - e
        cosang = float(np.dot(ua, fa) / max(np.linalg.norm(ua) * np.linalg.norm(fa), 1e-9))
        return math.degrees(math.acos(max(-1.0, min(1.0, cosang))))

    def _pair(self, pose: Pose, left: int, right: int) -> np.ndarray | None:
        """Visibility-weighted mean of a left/right landmark pair (image px). The far side
        drops out when the camera can't see it (side-on)."""
        v = pose.img[[left, right], 2]
        ok = v >= self.params.webcam.min_visibility
        if not ok.any():
            return None
        return np.average(pose.img[[left, right], :2][ok], axis=0, weights=v[ok])

    def _gym_posture(self, pose: Pose, sh, wr, hip, torso_px: float) -> str | None:
        """The picked gym exercise's posture, or None (out of it: the set ends).
        squat: both hands at shoulder height (a bar on your back);
        deadlift: hands at or below the hips (holding the bar);
        bench: lying (shoulders level with the hips, side-on) with the arms up;
        bag: any time the hands are seen."""
        wp = self.params.webcam
        ex = self.exercise
        if torso_px < 1e-6:
            return None
        hands_up = float(sh[1] - wr[1]) / torso_px  # hands above the shoulders (torso lengths)
        if ex == "squat":
            thigh = self._thigh(pose, hip, torso_px)
            tall_enough = self.state.workout == "squat" or (
                thigh is not None and thigh >= wp.squat_start_thigh
            )
            return "squat" if abs(hands_up) < wp.bar_hands and tall_enough else None
        if ex == "deadlift":
            low = float(wr[1] - hip[1]) / torso_px > -wp.bar_hands  # hands not raised above the hips
            return "deadlift" if low else None
        if ex == "bench":
            lying = abs(float(hip[1] - sh[1])) / torso_px < wp.bench_flat
            arms = hands_up > (-0.1 if self.state.workout == "bench" else wp.bench_arms_up)
            return "bench" if lying and arms else None
        if ex == "bag":
            return "bag" if self._visible(pose, L_WRIST, R_WRIST) else None
        return None

    def _auto_gym_posture(self, pose: Pose, sh, wr, hip, torso_px: float, kind: str | None) -> str | None:
        """Identify a clearly distinct gym posture when no station was selected.

        The floor, bar-hang and kettlebell poses are classified above. This covers the
        remaining static setups; bag hits are motion-classified separately below.
        """
        wp = self.params.webcam
        if torso_px < 1e-6:
            return kind
        hands_up = float(sh[1] - wr[1]) / torso_px
        lying = abs(float(hip[1] - sh[1])) / torso_px < wp.bench_flat
        if lying and hands_up > wp.bench_arms_up:
            return "bench"
        if kind is not None:
            return kind
        thigh = self._thigh(pose, hip, torso_px)
        if abs(hands_up) < wp.bar_hands and thigh is not None and thigh >= wp.squat_start_thigh:
            return "squat"
        hands_low = float(wr[1] - hip[1]) / torso_px > -wp.bar_hands
        torso_uprightness = abs(float(hip[1] - sh[1])) / torso_px
        if hands_low and torso_uprightness < wp.deadlift_hinge_ratio:
            return "deadlift"
        return None

    def _gym_measure(self, pose: Pose, sh, wr, hip, torso_px: float) -> float | None:
        """How tall the lift stands now (larger = higher): squat, the hip-to-knee gap;
        deadlift, the shoulders' height above the hips; bench, the hands' height above
        the shoulders. Each rep's progress compares it with the tallest this set."""
        ex = self.state.workout
        if ex == "squat":
            return self._thigh(pose, hip, torso_px)
        if ex == "deadlift":
            return float(hip[1] - sh[1])
        if ex == "bench":
            return float(sh[1] - wr[1])
        return None

    def _bag(self, pose: Pose, torso_px: float, events: list[GestureEvent]) -> None:
        """Count punches from wrist travel or arm extension; re-arm after retracting."""
        wp = self.params.webcam
        t = pose.t_us
        shoulders = pose.img[[L_SHOULDER, R_SHOULDER], :2]
        elbows = pose.img[[L_ELBOW, R_ELBOW], :2]
        wrists = pose.img[[L_WRIST, R_WRIST], :2]
        # Hands relative to the shoulders: swaying or stepping moves both, punching one.
        w = (wrists - shoulders.mean(axis=0)) / torso_px
        arm_visibility = np.min(
            pose.img[[L_SHOULDER, L_ELBOW, L_WRIST, R_SHOULDER, R_ELBOW, R_WRIST], 2].reshape(2, 3),
            axis=1,
        )
        extension = np.linalg.norm(wrists - shoulders, axis=1) / np.maximum(
            np.linalg.norm(elbows - shoulders, axis=1) + np.linalg.norm(wrists - elbows, axis=1), 1e-6
        )
        last = self._bag_last
        self._bag_last = (t, w)
        self.state.bag_hitting = False
        if last is None or t <= last[0]:
            self._bag_anchor = w.copy()
            self._bag_extension_anchor = extension.tolist()
            return
        dt = (t - last[0]) / 1e6
        speeds = np.linalg.norm(w - last[1], axis=1) / dt
        if self._bag_anchor is None:
            self._bag_anchor = w.copy()
        for arm, speed in enumerate(speeds):
            if arm_visibility[arm] < wp.min_visibility:
                self._bag_fast_frames[arm] = 0
                self._bag_rest_since_us[arm] = None
                continue
            speed = float(speed)
            ext = float(extension[arm])
            retracted = ext <= wp.bag_rearm_extension
            if speed <= wp.bag_rest_speed and retracted:
                self._bag_fast_frames[arm] = 0
                if self._bag_armed[arm]:
                    self._bag_anchor[arm] = w[arm]
                    self._bag_extension_anchor[arm] = ext
                else:
                    self._bag_rest_since_us[arm] = self._bag_rest_since_us[arm] or t
                    if (t - self._bag_rest_since_us[arm]) / 1000 >= wp.bag_rearm_ms:
                        self._bag_anchor[arm] = w[arm]
                        self._bag_extension_anchor[arm] = ext
                        self._bag_armed[arm] = True
                        self._bag_rest_since_us[arm] = None
                continue
            self._bag_rest_since_us[arm] = None
            self._bag_fast_frames[arm] = self._bag_fast_frames[arm] + 1 if speed > wp.bag_speed else 0
            travel = float(np.linalg.norm(w[arm] - self._bag_anchor[arm]))
            if not self._bag_armed[arm]:
                # A quick combination never stops to rest: the hand coming at least
                # halfway back (or the arm bending back) is enough for the next punch.
                self._bag_peak[arm] = max(self._bag_peak[arm], travel)
                self._bag_peak_ext[arm] = max(self._bag_peak_ext[arm], ext)
                if travel <= 0.5 * self._bag_peak[arm] or (
                    retracted and self._bag_peak_ext[arm] - ext >= wp.bag_extension_delta
                ):
                    self._bag_armed[arm] = True
                continue
            # Only a hand moving away from the guard punches (not one coming back to it).
            outward = float(np.dot(w[arm] - last[1][arm], w[arm] - self._bag_anchor[arm])) > 0
            fast_hit = self._bag_fast_frames[arm] >= 2 and travel >= wp.bag_min_travel and outward
            extension_hit = ext >= wp.bag_extension_min and (
                ext - self._bag_extension_anchor[arm] >= wp.bag_extension_delta
            )
            last_hit = self._bag_hit_us[arm]
            cooled = last_hit is None or t - last_hit >= wp.bag_refractory_ms * 1000
            # A punch leaves the guard and lands at the bag, both with the hand up: an arm
            # dropping to your side (or coming up from there) isn't one.
            guarded = max(float(self._bag_anchor[arm][1]), float(w[arm][1])) < wp.bag_guard_low
            if (fast_hit or extension_hit) and cooled and guarded:
                self.state.bag_hitting = True
                self._bag_armed[arm] = False
                self._bag_hit_us[arm] = t
                self._bag_peak[arm], self._bag_peak_ext[arm] = travel, ext
                events.append(GestureEvent("bag_hit", t, min(1.0, speed / wp.bag_speed)))

    def _bag_reset(self) -> None:
        """Forget the punching-bag motion history (a set starts or ends)."""
        self._bag_last, self._bag_anchor = None, None
        self._bag_hit_us, self._bag_armed = [None, None], [True, True]
        self._bag_extension_anchor, self._bag_peak, self._bag_peak_ext = [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]
        self._bag_rest_since_us, self._bag_fast_frames = [None, None], [0, 0]

    def _knee_y(self, pose: Pose, hip: np.ndarray | None) -> float | None:
        """Knee height (image px). Hinged over a kettlebell, the arms hide the knees: they
        are then where they were last seen below the hips."""
        knee = self._pair(pose, L_KNEE, R_KNEE)
        if knee is not None:
            if hip is not None:
                self._knee_drop_px = float(knee[1] - hip[1])
            return float(knee[1])
        if hip is None or self._knee_drop_px is None:
            return None
        return float(hip[1]) + self._knee_drop_px

    def _kettlebell(self, pose: Pose, torso_px: float) -> bool:
        """Kettlebell posture (fitness mode only). A set starts like the game's: hinged
        over the kettlebell with both hands down at it, well below the hips and together
        on the handle; it carries on while the hands stay together (the swing
        may take them overhead) and ends when they come apart. `torso_px` is the recent
        tallest torso: hinged toward the camera, the torso looks short."""
        wp = self.params.webcam
        sh, wr = self._pair(pose, L_SHOULDER, R_SHOULDER), self._pair(pose, L_WRIST, R_WRIST)
        if sh is None or wr is None or torso_px < 1e-6:
            return False
        # Side-on, the far hand hides behind the near one on the handle: one seen is enough.
        together = not self._visible(pose, L_WRIST, R_WRIST) or (
            float(np.linalg.norm(pose.img[L_WRIST, :2] - pose.img[R_WRIST, :2])) / torso_px
            < wp.swing_hands_apart
        )
        if self.state.workout == "kettlebell":
            return together
        hip = self._pair(pose, L_HIP, R_HIP)
        if hip is None:
            return False
        # Standing, hanging hands are level with the hips; down at the kettlebell, well below.
        return together and float(wr[1] - hip[1]) / torso_px > wp.swing_grab

    @staticmethod
    def _hand_up(pose: Pose, arm: _Arm) -> float:
        """How far the hand is above the shoulder, in lengths of that arm as the camera
        sees it (shoulder to elbow to wrist): 1 = straight up, -1 = hanging down. Unlike
        torso lengths, this reads the same from a camera low and close (the torso looks
        long) as from one at chest height."""
        s, e, w = pose.img[[arm.shoulder, arm.elbow, arm.wrist], :2]
        length = float(np.linalg.norm(e - s) + np.linalg.norm(w - e))
        return float(s[1] - w[1]) / max(length, 1e-6)

    def _hanging(self, pose: Pose, torso_px: float, reach: float) -> bool:
        """Both hands above the shoulders by `reach` and closer together than spread
        wings: holding a bar overhead. With pull-ups picked, `reach` is in arm lengths, which
        reads the same from any camera (from behind and below, it's how a hang shows). In
        AUTO it stays in torso lengths: the top of a kettlebell swing, arms pointing at the
        camera, looks short-armed and would pass for a hang."""
        if not self._visible(pose, L_SHOULDER, R_SHOULDER, L_WRIST, R_WRIST):
            return False
        if self.exercise == "pullup":
            if not self._visible(pose, L_ELBOW, R_ELBOW):
                return False
            up = min(self._hand_up(pose, arm) for arm in self._arms)
        else:
            xy = pose.img[:, :2] / torso_px
            up = min(xy[L_SHOULDER, 1] - xy[L_WRIST, 1], xy[R_SHOULDER, 1] - xy[R_WRIST, 1])
        apart = float(np.linalg.norm(pose.img[L_WRIST, :2] - pose.img[R_WRIST, :2])) / torso_px
        return up > reach and apart < self.params.webcam.fly_span

    def _on_bar(self, pose: Pose, torso_px: float) -> bool:
        """Still in a pull-up set: off the bar only once the arms seen are straight and
        the hands low, down by your sides. Bent elbows are a rep, whatever the hands
        look like: seen from behind at the top, the pose model puts the wrists down by
        the elbows. Arms the camera can't make out carry the set on."""
        wp = self.params.webcam
        seen = [a for a in self._arms if self._visible(pose, a.shoulder, a.elbow, a.wrist)]
        return not seen or not all(
            self._elbow_deg(pose, a, torso_px) > wp.pullup_bottom_deg
            and self._hand_up(pose, a) < -wp.pullup_release
            for a in seen
        )

    def _workout(self, pose: Pose, events: list[GestureEvent]) -> bool:
        """Push-ups and pull-ups. Each completed rep fires one event. True while the body
        is in a set or heading into one.

        Push-ups work from any camera angle: on the floor, the hands are below the hips
        by a good share of their drop below the shoulders (standing, the hands hang level
        with the hips); a rep is the shoulders sinking toward the hands, then rising back
        near the top of the last few seconds. Pull-ups: hanging from a bar, the elbows
        bend past `pullup_top_deg` after hanging straight."""
        wp = self.params.webcam
        t = pose.t_us
        self.state.bag_hitting = False
        sh = self._pair(pose, L_SHOULDER, R_SHOULDER)
        wr = self._pair(pose, L_WRIST, R_WRIST)
        hip = self._pair(pose, L_HIP, R_HIP)
        if sh is None or wr is None or hip is None:
            # The camera lost a hand or the hips for a moment: that's not standing up, so a
            # set carries on (it ends only when the body is seen out of the posture).
            return bool(self.state.workout) or self._posture(None, t, events)
        arm_px = float(wr[1] - sh[1])  # shoulders' height above the hands
        torso_px = float(np.linalg.norm(sh - hip))
        if torso_px > 1e-6:  # the recent tallest torso, slowly forgotten (you may step back)
            dt = 0.0 if self._body_t_us is None else min((t - self._body_t_us) / 1e6, 1.0)
            self._body_t_us = t
            self._body_px = max(torso_px, (self._body_px or 0.0) * (1 - wp.body_scale_decay * dt))
        floor = arm_px > 1e-6 and (wr[1] - hip[1]) / arm_px > wp.plank_hip
        hang = torso_px > 1e-6 and (
            self._on_bar(pose, torso_px)
            if self.state.workout == "pullup"
            else self._hanging(pose, torso_px, wp.hang_reach)
        )
        if self.state.workout != "pullup" and self.state.wheel_open:
            hang = False  # pointing up at the wheel's top slot, not grabbing a bar
        if self.fitness and self.exercise:  # you picked the station: look for that exercise only
            floor = floor and self.exercise == "pushup"
            hang = hang and self.exercise == "pullup"
        swing = (
            self.fitness
            and self.exercise in ("", "kettlebell")
            and not hang
            and self._kettlebell(pose, self._body_px or torso_px)
        )
        # Hands together near the knees also pass the push-up check: the swing wins.
        kind = "pullup" if hang else "kettlebell" if swing else "pushup" if floor else None
        if self.fitness and self.exercise in GYM_ONLY:
            kind = self._gym_posture(pose, sh, wr, hip, torso_px)
        elif self.fitness and not self.exercise:
            kind = self._auto_gym_posture(pose, sh, wr, hip, torso_px, kind)
        if self.state.workout and kind != self.state.workout:
            kind = None
        busy = self._posture(kind, t, events)
        if self.state.workout == "pushup" and kind == "pushup":
            self._tops.append((t, arm_px))
            while t - self._tops[0][0] > wp.pushup_top_window_ms * 1000:
                self._tops.popleft()
            top = max(a for _, a in self._tops)
            if arm_px < wp.pushup_down * top:
                self._rep_armed = True
            elif self._rep_armed and arm_px > wp.pushup_up * top:
                self._rep(t, events)
            self._rep_phase(t, (arm_px / top - wp.pushup_down) / (1 - wp.pushup_down))
        elif self.state.workout == "pullup" and kind == "pullup":
            elbow = float(np.mean([self._elbow_deg(pose, a, torso_px) for a in self._arms]))
            if elbow > wp.pullup_bottom_deg:
                self._rep_armed = True
            elif self._rep_armed and elbow < wp.pullup_top_deg:
                self._rep(t, events)
            self._rep_phase(t, (wp.pullup_bottom_deg - elbow) / (wp.pullup_bottom_deg - wp.pullup_top_deg))
        elif self.state.workout == "kettlebell" and kind == "kettlebell":
            # Hands from the knees (bottom of the swing) to the shoulders (top).
            knee_y = self._knee_y(pose, hip)
            if knee_y is not None and knee_y - sh[1] > 1e-6:
                progress = float((knee_y - wr[1]) / (knee_y - sh[1]))
                if progress < wp.swing_low:
                    self._rep_armed = True
                elif self._rep_armed and progress > wp.swing_high:
                    self._rep(t, events)
                self._rep_phase(t, progress)
        elif self.state.workout in ("squat", "deadlift", "bench") and kind == self.state.workout:
            measure = self._gym_measure(pose, sh, wr, hip, torso_px)
            if measure is not None and measure > 1e-6:
                # The top is the tallest reading, slowly forgotten: a pose misread a bit
                # high (hips at the top of a squat) would otherwise hold it out of reach.
                dt = 0.0 if self._set_top_t_us is None else min((t - self._set_top_t_us) / 1e6, 1.0)
                self._set_top_t_us = t
                self._set_top = max(measure, self._set_top * (1 - wp.rep_top_decay * dt))
                bottom = {"squat": wp.squat_bottom, "deadlift": wp.deadlift_bottom, "bench": wp.bench_bottom}
                b = bottom[self.state.workout]
                progress = (measure / self._set_top - b) / (1 - b)
                if progress < wp.rep_low:
                    self._rep_armed = True
                elif self._rep_armed and progress > wp.rep_high:
                    self._rep(t, events)
                self._rep_phase(t, progress)
        elif self.state.workout == "bag" and kind == "bag":
            self._bag(pose, torso_px, events)
        elif self.state.workout:  # out of the posture (letting go of the bar): nothing is pressed
            self.state.workout_rising = self.state.workout_struggling = False
            self._rep_last = None
        return busy or kind is not None

    def _rep_phase(self, t_us: int, progress: float) -> None:
        """Where the rep is and which way it goes, for games that hold a button through
        each rep (GTA 6's fitness: L2+R2 pressed harder on the way up and held at the top,
        released to go down, pressed again from the bottom, X mashed when the character
        struggles). The press lasts until the body actually starts down, so the game lowers
        the character when you lower yourself."""
        wp = self.params.webcam
        st = self.state
        progress = min(1.0, max(0.0, progress))
        last = self._rep_last
        self._rep_last = (t_us, progress)
        st.workout_progress = progress
        if last is None or t_us <= last[0]:
            return
        speed = (progress - last[1]) / ((t_us - last[0]) / 1e6)
        self._rep_speed += 0.4 * (speed - self._rep_speed)  # progress/s, smoothed
        if self._rep_speed > wp.rep_move_speed:
            st.workout_rising = True
        elif self._rep_speed < -wp.rep_move_speed:
            st.workout_rising = False  # going down: let go
        # Stuck = pressing partway up and barely moving (a pause at the top isn't a struggle).
        stuck = (
            st.workout_rising
            and 0.15 < progress < wp.rep_top
            and abs(self._rep_speed) < wp.rep_struggle_speed
        )
        if stuck:
            self._stuck_since_us = self._stuck_since_us or t_us
        else:
            self._stuck_since_us = None
        st.workout_struggling = stuck and (t_us - self._stuck_since_us) / 1000 >= wp.rep_struggle_ms

    def _posture(self, kind: str | None, t_us: int, events: list[GestureEvent]) -> bool:
        """Start a set after `workout_hold_ms` in the posture, end it after
        `workout_release_ms` out of it. True while a set runs."""
        wp = self.params.webcam
        if kind is not None:
            self._posture_lost_us = None
            if not self.state.workout:
                self._posture_since_us = self._posture_since_us or t_us
                if (t_us - self._posture_since_us) / 1000 >= wp.workout_hold_ms:
                    self.state.workout, self.state.workout_reps = kind, 0
                    self._end_rep_phase()
                    self._rep_armed = False
                    self._tops.clear()
                    self._set_top, self._set_top_t_us = 0.0, None
                    self._bag_reset()
                    if kind in TOP_START:  # the bar starts on the rack: lift it off right away
                        self.state.workout_rising = True
                    events.append(GestureEvent("workout_start", t_us, 1.0))
            return bool(self.state.workout)
        self._posture_since_us = None
        if not self.state.workout:
            return False
        self._posture_lost_us = self._posture_lost_us or t_us
        if (t_us - self._posture_lost_us) / 1000 < wp.workout_release_ms:
            return True
        self._end_set(t_us, events)
        return False

    def _workout_gone(self, t_us: int, events: list[GestureEvent]) -> None:
        """Nobody in view mid-set. A short dropout carries the set on; after
        `workout_gone_ms` you have walked off, so the set ends."""
        self._gone_since_us = self._gone_since_us or t_us
        if (t_us - self._gone_since_us) / 1000 >= self.params.webcam.workout_gone_ms:
            self._gone_since_us = None
            self._end_set(t_us, events)

    def _end_set(self, t_us: int, events: list[GestureEvent]) -> None:
        self.state.last_workout, self.state.workout = self.state.workout, ""
        self._end_rep_phase()
        self.state.bag_hitting = False
        self._bag_reset()
        self._posture_lost_us = self._posture_since_us = None
        self._settling, self._settle_since_us = True, t_us
        events.append(GestureEvent("workout_end", t_us, 1.0))

    def _end_rep_phase(self) -> None:
        self._rep_last, self._rep_speed, self._stuck_since_us = None, 0.0, None
        st = self.state
        st.workout_progress, st.workout_rising, st.workout_struggling = 0.0, False, False

    def _rep(self, t_us: int, events: list[GestureEvent]) -> None:
        self._rep_armed = False
        self.state.workout_reps += 1
        events.append(GestureEvent(self.state.workout, t_us, 1.0))

    def _settled(self, t_us: int, thigh: float | None) -> bool:
        """After a set, standing gestures resume once you're up: `workout_settle_ms`
        passed and the legs are straight again (or, failing that, after four times as
        long, from wherever they are now)."""
        wp = self.params.webcam
        waited_ms = (t_us - self._settle_since_us) / 1000
        if waited_ms < wp.workout_settle_ms:
            return False
        up = self._thigh_base is None or (thigh is not None and abs(self._thigh_base - thigh) < wp.jump_dip)
        if not up and waited_ms < 4 * wp.workout_settle_ms:
            return False
        if not up:  # stood up differently: learn the new standing levels
            self._hip_base = self._thigh_base = None
        self._settling = False
        return True

    # -- run -----------------------------------------------------------------

    def _run(
        self, pose: Pose, hip: np.ndarray, torso: float, conf: float, events: list[GestureEvent]
    ) -> None:
        p = self.params
        t = pose.t_us
        lifts: list[float | None] = []
        for leg in self._legs:
            if not self._visible(pose, leg.knee):
                lifts.append(None)
                continue
            s = (pose.img[leg.knee, 1] - hip[1]) / torso  # knee below hip, in torso lengths
            if leg.baseline is None:
                leg.baseline = s
            lifts.append(leg.baseline - s)
        for li, leg in enumerate(self._legs):
            lift = lifts[li]
            if lift is None:
                continue
            s = leg.baseline - lift
            other = lifts[1 - li]
            if lift > p.webcam.knee_lift / 2:
                self._legs_moving_us = t  # a knee is coming up: running arms swing, they don't punch
            # A step lifts one knee off a straight standing leg; both knees "rising" toward
            # the hips together is the knees bending (a dip, a crouch, kneeling down).
            standing_leg = other is None or other < p.webcam.knee_lift * p.webcam.step_other_max
            self._hurdle(li, lift, other, t, conf, events)
            if not leg.lifted and lift > p.webcam.knee_lift and standing_leg:
                leg.lifted, leg.peak = True, lift
                if p.webcam.step_signal == "knee_lift":
                    self._step(li, t, conf, events)
            elif leg.lifted:
                leg.peak = max(leg.peak, lift)
                # Down again once it has dropped to half its peak: fast steps never come all
                # the way back to standing before the next one.
                if lift < max(p.webcam.knee_lift / 2, leg.peak / 2):
                    leg.lifted = False
            if not leg.lifted and lift < p.webcam.knee_lift / 2:
                # Standing level drifts slowly (posture, camera); only learn it while down.
                leg.baseline += 0.02 * (s - leg.baseline)
        if p.webcam.step_signal == "alternate":
            self._alternate(pose, torso, t, conf, events)
        self._check_stop(t, events)

    def _alternate(
        self, pose: Pose, torso: float, t_us: int, conf: float, events: list[GestureEvent]
    ) -> None:
        """Steps from the legs alternating: how much higher your left foot and knee are than
        your right ones. Running in place flips its sign every step even when the knees
        barely rise; it cancels the body's bounce, the camera height and distance; and both
        feet moving together (a jump, a crouch) leave it at zero."""
        wp = self.params.webcam
        img = pose.img
        s = 0.0
        for left, right in ((L_KNEE, R_KNEE), (L_ANKLE, R_ANKLE)):
            if self._visible(pose, left, right):
                s += (img[right, 1] - img[left, 1]) / torso  # image y grows down: + = left higher
        if self._alt_base is None:
            self._alt_base = s
        d = s - self._alt_base
        thr = wp.step_alt
        if d > thr and self._alt_side != 1:
            self._alt_side = 1
            self._step(0, t_us, conf, events)
        elif d < -thr and self._alt_side != -1:
            self._alt_side = -1
            self._step(1, t_us, conf, events)
        elif abs(d) < thr / 3:
            self._alt_side = 0  # both feet level: the same foot may step again
        if abs(d) < thr / 2:
            self._alt_base += 0.02 * (s - self._alt_base)  # standing offset (camera angle) drifts

    def _hurdle(
        self, li: int, lift: float, other: float | None, t_us: int, conf: float, events: list[GestureEvent]
    ) -> None:
        """Hurdle jump (GTA's running jump, like a 110 m hurdle): the lead knee comes up far
        higher than a running step while the other leg stays low (both knees high is a
        crouch). Fires as the knee crosses `hurdle_lift`, once per lift."""
        wp = self.params.webcam
        up = self._hurdle_up[li]
        if lift < wp.hurdle_lift / 2:
            self._hurdle_up[li] = False
            return
        if up or lift < wp.hurdle_lift or (other is not None and other > wp.hurdle_other_max):
            return
        self._hurdle_up[li] = True
        if (t_us - self._last_jump_us) / 1000 > wp.jump_refractory_ms and not self.state.crouching:
            events.append(GestureEvent("jump", t_us, conf))
            self._last_jump_us = t_us

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

    def _thigh(self, pose: Pose, hip: np.ndarray, torso: float) -> float | None:
        """Vertical hip-to-knee gap of the straighter leg, in torso lengths: it shrinks as
        the knees bend, wherever you stand; a running knee lift only shortens the lifted
        leg. None without a visible knee."""
        gaps = [(pose.img[k, 1] - hip[1]) / torso for k in (L_KNEE, R_KNEE) if self._visible(pose, k)]
        return max(gaps) if gaps else None

    def _jump(
        self, pose: Pose, hip: np.ndarray, torso: float, conf: float, events: list[GestureEvent]
    ) -> None:
        p = self.params
        t = pose.t_us
        h = hip[1] / torso  # image y grows downward: a dip makes h larger
        thigh = self._thigh(pose, hip, torso)
        self._hip_hist.append((t, h))
        self._hip_hist = self._hip_hist[-4:]
        if self._hip_base is None:
            self._hip_base = h
        if self._thigh_base is None and thigh is not None:
            self._thigh_base = thigh
            return
        vel = 0.0
        if len(self._hip_hist) >= 2:
            (t0, h0), (t1, h1) = self._hip_hist[-2], self._hip_hist[-1]
            if t1 > t0:
                vel = (h1 - h0) / ((t1 - t0) / 1e6)  # torso/s, negative = rising
        rise = h - self._hip_base  # hips above (-) / below (+) standing height
        # Knee bend (torso lengths); hip height only without a visible knee.
        rel = self._thigh_base - thigh if thigh is not None and self._thigh_base is not None else rise
        in_dip = rel > p.webcam.jump_dip
        since_jump_ms = (t - self._last_jump_us) / 1000
        if in_dip and self._dip_start_us is None:
            self._dip_start_us = t
            self._crouch_sent = False
        if self._dip_start_us is not None:
            dip_ms = (t - self._dip_start_us) / 1000
            rising = vel < -p.webcam.jump_rise_speed
            # Rising out of a held crouch is standing up, not a jump.
            crouched = self._crouch_sent or self.state.crouching
            if (
                rising
                and not crouched
                and dip_ms >= p.jump.dip_min_ms
                and since_jump_ms > p.webcam.jump_refractory_ms
            ):
                events.append(GestureEvent("jump", t, conf))
                self._last_jump_us = t
                self._dip_start_us = None
            elif not in_dip and not rising:
                self._dip_start_us = None  # came back up slowly: just a bob
            elif dip_ms >= p.jump.crouch_hold_ms and not self._crouch_sent:
                events.append(GestureEvent("crouch", t, conf))
                self._crouch_sent = True
                self.state.crouching = True
        elif rise < -1.5 * p.webcam.jump_dip and vel < -p.webcam.jump_rise_speed:
            # Jumped without a visible dip: fire late rather than never.
            if since_jump_ms > p.webcam.jump_refractory_ms and not self.state.crouching:
                events.append(GestureEvent("jump", t, conf * 0.8))
                self._last_jump_us = t
        if self.state.crouching and rel < p.webcam.jump_dip / 2:  # hips back up: stood up
            self.state.crouching = False
            self._dip_start_us = None
            self._last_jump_us = t  # the overshoot of standing up isn't a jump either
            events.append(GestureEvent("stand", t, conf))
        if abs(rel) < p.webcam.jump_dip / 2 and abs(vel) < p.webcam.jump_rise_speed / 2:
            # Legs straight and still: standing. Both levels follow you as you move around.
            self._hip_base += 0.02 * (h - self._hip_base)
            if thigh is not None and self._thigh_base is not None:
                self._thigh_base += 0.02 * (thigh - self._thigh_base)

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
        # Reaching for the wheel, pointing, spreading wings and the arm swing of running
        # in place aren't punches.
        quiet = (
            self.state.wheel_open
            or self._wheel_since_us is not None
            or self._fly_since_us is not None
            or self.state.running
            or (pose.t_us - self._legs_moving_us) / 1000 < wp.punch_still_legs_ms
        )
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

    def _aim_pose(self, pose: Pose, arm: _Arm, torso_px: float, keep: bool = False) -> bool:
        """keep: already aiming; then a hand steering slowly within the aim nudge's range of
        where aiming began still aims, wherever that takes it."""
        wp = self.params.webcam
        if not self._visible(pose, arm.shoulder, arm.elbow, arm.wrist):
            return False
        if keep and self._nudge_anchor is not None and not self._nudge_fast:
            rel = (pose.img[arm.wrist, :2] - pose.img[arm.shoulder, :2]) / torso_px
            if np.linalg.norm(rel - self._nudge_anchor) <= wp.aim_nudge_full:
                return True
        (s, e, w), _ = self._arm_points(pose, arm, torso_px)
        if pose.world is not None:
            forward = s[2] - w[2]  # wrist closer to the camera than the shoulder
            return forward > wp.aim_forward and abs(w[1] - s[1]) < wp.aim_height
        # 2D fallback: elbow and wrist raised to shoulder height (a guarding fist keeps its
        # elbow down) and the arm short in the image (pointing at the camera foreshortens).
        return (
            abs(w[1] - s[1]) < wp.aim_height
            and abs(e[1] - s[1]) < wp.aim_height
            and np.linalg.norm(w[:2] - s[:2]) < 0.6
        )

    def _aim(self, pose: Pose, torso_px: float, events: list[GestureEvent]) -> None:
        """The profile's aim_hand aims (either: the right one first); while aiming, only that
        arm counts."""
        wp = self.params.webcam
        order = {"either": (1, 0), "right": (1,), "left": (0,)}[wp.aim_hand]
        if self.state.wheel_open or self._wheel_since_us is not None:
            order = ()  # the left hand is picking a wheel slot, not aiming
        if not order:
            aim_pose = False
        elif self.state.aiming:
            aim_pose = self._aim_pose(pose, self._arms[self._aim_arm], torso_px, keep=True)
        else:
            k = next((k for k in order if self._aim_pose(pose, self._arms[k], torso_px)), None)
            aim_pose = k is not None
            if k is not None and k != self._aim_arm:
                self._aim_arm, self._aim_since_us = k, None
        arm = self._arms[self._aim_arm]
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
        """Recoil flick: while aiming, the aiming wrist jumps up relative to the shoulder.
        Costly action (rule 7): gated on aiming here and in the binding, and on the arm
        keypoints' confidence."""
        wp = self.params.webcam
        arm = self._arms[self._aim_arm]
        if not self.state.aiming or not self._visible(pose, arm.shoulder, arm.wrist):
            self._flick = None
            return
        (s, _e, w), _ = self._arm_points(pose, arm, torso_px)
        up = float(s[1] - w[1])  # y grows downward in image and world coordinates
        t = pose.t_us
        if self._flick is not None and t > self._flick[0]:
            speed = (up - self._flick[1]) / ((t - self._flick[0]) / 1e6)
            conf = float(pose.img[[arm.shoulder, arm.elbow, arm.wrist], 2].min())
            # A recoil flick starts from the aiming position (rule 7: no shots from an arm
            # that wandered off, e.g. into a jump or the weapon wheel).
            near = (
                self._nudge_anchor is None
                or self._nudge_last is None
                or np.linalg.norm(self._nudge_last[1] - self._nudge_anchor) <= wp.aim_nudge_full
            )
            if (
                speed > wp.shoot_flick_speed
                and conf >= wp.shoot_min_conf
                and near
                and (t - self._last_shot_us) / 1000 > wp.shoot_refractory_ms
            ):
                events.append(GestureEvent("shoot", t, conf))
                self._last_shot_us = t
        self._flick = (t, up)

    # -- aim nudge -------------------------------------------------------------------

    def _aim_nudge(self, pose: Pose, torso_px: float) -> None:
        """While aiming, the aiming hand steers the aim: where it is relative to where it
        was when aiming began (both relative to the shoulder, so body sway cancels) pushes
        the right stick, past a deadzone and up to full at `aim_nudge_full`. Fast moves
        (the recoil flick) don't steer, and the aim holds still just after a shot."""
        wp = self.params.webcam
        arm = self._arms[self._aim_arm]
        if not self.state.aiming or not self._visible(pose, arm.shoulder, arm.wrist):
            self._end_nudge()
            return
        t = pose.t_us
        rel = (pose.img[arm.wrist, :2] - pose.img[arm.shoulder, :2]) / torso_px
        if self._nudge_anchor is None:
            self._nudge_anchor = rel.copy()
        fast = False
        if self._nudge_last is not None and t > self._nudge_last[0]:
            speed = float(np.linalg.norm(rel - self._nudge_last[1])) / ((t - self._nudge_last[0]) / 1e6)
            fast = speed > wp.aim_nudge_max_speed
        self._nudge_last, self._nudge_fast = (t, rel), fast
        quiet = fast or (t - self._last_shot_us) / 1000 < wp.shoot_refractory_ms
        d = rel - self._nudge_anchor
        # The camera faces you: your right is image left (x flips); image y is down.
        dx, dy = -float(d[0]) * wp.aim_nudge_x_gain, -float(d[1])
        mag = math.hypot(dx, dy)
        x = y = 0.0
        if not quiet and mag > wp.aim_nudge_deadzone:
            k = min(1.0, (mag - wp.aim_nudge_deadzone) / (wp.aim_nudge_full - wp.aim_nudge_deadzone)) / mag
            x, y = dx * k, dy * k
        self.state.aim_x, self.state.aim_y = x, y
        sh = pose.img[arm.shoulder, :2]
        self.state.aim_anchor_px = tuple(sh + self._nudge_anchor * torso_px)
        self.state.aim_hand_px = tuple(pose.img[arm.wrist, :2])

    def _end_nudge(self) -> None:
        self._nudge_anchor = self._nudge_last = None
        self._nudge_fast = False
        self.state.aim_x = self.state.aim_y = 0.0
        self.state.aim_anchor_px = self.state.aim_hand_px = None

    # -- weapon wheel ------------------------------------------------------------

    def _wheel(self, pose: Pose, torso_px: float, events: list[GestureEvent]) -> None:
        """Right hand reaches over the right shoulder (to a backpack) and stays: the wheel
        opens; the left hand's direction picks the slot; dropping the right hand closes it
        (GTA equips the highlighted weapon)."""
        wp = self.params.webcam
        xy = pose.img[:, :2] / torso_px
        t = pose.t_us
        reach = False
        if (
            self._visible(pose, R_SHOULDER, R_ELBOW, R_WRIST) and not self.state.aiming
        ):  # an aim isn't a reach
            above = xy[R_SHOULDER, 1] - xy[R_WRIST, 1]
            # Into the backpack: hand up over the shoulder with the elbow raised too (a
            # guarding fist keeps its elbow down, an aiming arm at shoulder height).
            elbow_up = xy[R_SHOULDER, 1] - xy[R_ELBOW, 1]
            reach = (
                above > wp.wheel_reach
                and elbow_up > wp.wheel_elbow_up
                and abs(xy[R_WRIST, 0] - xy[R_SHOULDER, 0]) < wp.wheel_side
            )
            if not self.state.wheel_open and self._visible(pose, L_SHOULDER, L_WRIST):
                # Both hands going up reach for a bar, not the backpack (pointing up comes
                # after the wheel is open).
                reach = reach and xy[L_SHOULDER, 1] - xy[L_WRIST, 1] < wp.hang_reach
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

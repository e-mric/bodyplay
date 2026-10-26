"""Profile schema: gestures, ports and per-mode bindings (config/profiles/*.yaml).

Every gesture threshold lives here with a documented default, so a profile only
needs to override what it tunes. The validators enforce the hard rules that can
be checked statically: press timing (rule 8), one fixed PS5 button per port and
body sticks at neutral in vehicles (rule 4), and gating of costly actions (rule 7).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bodyctl.protocol import Mode

PortName = Annotated[str, Field(pattern=r"^[AB]-E[1-4]$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --- gestures -----------------------------------------------------------------


class StepParams(_Strict):
    peak_g: float = Field(0.25, gt=0, description="Vertical accel peak above 1 g that counts as a step")
    refractory_ms: int = Field(200, ge=50, description="Ignore new peaks this long after a step")
    stop_after_intervals: float = Field(
        1.5, gt=1, description="Running stops after this many missed step intervals"
    )


class JumpParams(_Strict):
    dip_g: float = Field(-0.30, lt=0, description="Vertical accel (minus 1 g) that starts a dip")
    dip_min_ms: int = Field(40, ge=0, description="Dip must last this long before a reversal counts")
    push_jerk: float = Field(8.0, gt=0, description="Upward jerk (g/s) that marks the push-off reversal")
    crouch_hold_ms: int = Field(300, gt=0, description="A dip held this long without reversal = crouch")
    min_conf: float = Field(0.6, ge=0, le=1)
    freefall_g: float = Field(-0.85, lt=0, description="Below this is running flight, not a jump dip")
    running_dip_min_ms: int = Field(100, ge=0, description="Stricter minimum dip while running")


class LeanParams(_Strict):
    deadzone_deg: float = Field(6, ge=0, description="Roll below this maps to zero stick")
    full_deg: float = Field(25, gt=0, description="Roll at which the stick reaches full deflection")

    @model_validator(mode="after")
    def _range(self) -> LeanParams:
        if self.full_deg <= self.deadzone_deg:
            raise ValueError("full_deg must exceed deadzone_deg")
        return self


class TwistParams(_Strict):
    deadzone_deg: float = Field(15, ge=0, description="Torso yaw from neutral below this = no turn")
    full_deg: float = Field(45, gt=0, description="Yaw for full turn rate")
    max_rate: float = Field(1.0, gt=0, le=1, description="Cap on stick deflection (0-1)")
    rezero_still_ms: int = Field(
        1000, gt=0, description="Standing still facing the TV this long re-zeroes yaw"
    )

    @model_validator(mode="after")
    def _range(self) -> TwistParams:
        if self.full_deg <= self.deadzone_deg:
            raise ValueError("full_deg must exceed deadzone_deg")
        return self


class AimParams(_Strict):
    forearm_pitch_deg: float = Field(35, description="Forearm pitch above horizontal that means aiming")


class ShootParams(_Strict):
    emg_k_baseline: float = Field(3.0, gt=1, description="EMG envelope multiple of baseline that fires")
    min_conf: float = Field(0.9, ge=0.8, le=1, description="Costly action: high confidence required")


class PunchParams(_Strict):
    accel_g: float = Field(2.0, gt=0, description="Forward wrist accel onset that fires a punch")
    min_conf: float = Field(0.6, ge=0, le=1)


class EnterExitParams(_Strict):
    two_cue_window_ms: int = Field(150, gt=0, description="Both cues must agree within this window")
    min_conf: float = Field(0.9, ge=0.8, le=1, description="Costly action: high confidence required")


class WebcamParams(_Strict):
    """Tier 0 only. Distances are in torso lengths (shoulder center to hip center), so
    they don't depend on how far you stand from the camera."""

    knee_lift: float = Field(0.12, gt=0, description="Knee rise above its standing level that counts a step")
    jump_dip: float = Field(0.05, gt=0, description="Hip drop below standing level that starts a jump dip")
    jump_rise_speed: float = Field(0.8, gt=0, description="Upward hip speed (torso/s) marking the reversal")
    jump_refractory_ms: int = Field(600, ge=0, description="Ignore new jumps this long after one")
    punch_speed: float = Field(4.0, gt=0, description="Shoulder-to-wrist growth rate (torso/s) for a punch")
    punch_elbow_deg: float = Field(120, ge=0, le=180, description="Elbow must open past this angle")
    punch_refractory_ms: int = Field(300, ge=0, description="Per arm")
    aim_forward: float = Field(0.4, gt=0, description="Right wrist this far in front of the shoulder")
    aim_height: float = Field(0.35, gt=0, description="Right wrist within this of shoulder height")
    aim_hold_ms: int = Field(120, ge=0, description="Pose held this long before aiming starts")
    min_visibility: float = Field(0.5, ge=0, le=1, description="Ignore landmarks MediaPipe isn't sure of")


class Gestures(_Strict):
    step: StepParams = StepParams()
    cadence_curve: list[float] = Field(
        [1.5, 2.5, 3.5],
        min_length=3,
        max_length=3,
        description="Steps/s where walk, jog and sprint reach full effect",
    )
    jump: JumpParams = JumpParams()
    lean: LeanParams = LeanParams()
    twist: TwistParams = TwistParams()
    aim: AimParams = AimParams()
    shoot: ShootParams = ShootParams()
    punch: PunchParams = PunchParams()
    enter_exit: EnterExitParams = EnterExitParams()
    webcam: WebcamParams = WebcamParams()

    @field_validator("cadence_curve")
    @classmethod
    def _ascending(cls, v: list[float]) -> list[float]:
        if not all(0 < a < b for a, b in zip(v, v[1:], strict=False)) or v[0] <= 0:
            raise ValueError("cadence_curve must be positive and strictly ascending")
        return v


class PressTiming(_Strict):
    hold_ms: int = Field(30, ge=30, description="Hard rule 8: each virtual press held >= 30 ms")
    release_ms: int = Field(30, ge=30, description="Hard rule 8: released >= 30 ms between presses")


# --- ports --------------------------------------------------------------------


class PortType(StrEnum):
    STICK = "stick"
    BUTTON = "button"
    TRIGGER = "trigger"


STICK_OUTPUTS = {"left_stick", "right_stick"}
TRIGGER_OUTPUTS = {"l2", "r2"}
BUTTON_OUTPUTS = {
    "cross",
    "circle",
    "square",
    "triangle",
    "l1",
    "r1",
    "l3",
    "r3",
    "dpad_up",
    "dpad_down",
    "dpad_left",
    "dpad_right",
    "options",
    "create",
    "touchpad",
    "ps",
}


class Port(_Strict):
    type: PortType
    ps5: str = Field(description="PS5 input this port emits, set once in the Access controller profile")

    @model_validator(mode="after")
    def _type_matches_output(self) -> Port:
        allowed = {
            PortType.STICK: STICK_OUTPUTS,
            PortType.TRIGGER: TRIGGER_OUTPUTS,
            PortType.BUTTON: BUTTON_OUTPUTS,
        }[self.type]
        if self.ps5 not in allowed:
            raise ValueError(f"{self.type} port can't emit {self.ps5!r}; pick one of {sorted(allowed)}")
        return self


# --- bindings -----------------------------------------------------------------

ContinuousSource = Literal[
    "run_cadence", "lean", "twist", "stroke_rhythm", "step_back_or_lean_back", "hand_height"
]
PulseSource = Literal["step_rhythm", "stroke_rhythm", "fast_feet"]
EventName = Literal["jump", "crouch", "punch", "shoot", "enter_exit", "flick", "stomp"]
HoldState = Literal["aiming"]
Condition = Literal["aiming", "stopped", "cadence_above_sprint"]

COSTLY_EVENTS = {"shoot", "enter_exit"}


class Binding(_Strict):
    """What drives one port in one mode: at most one of axes, pulse, event, hold, analog.

    None of them (written as `neutral` in YAML) holds the port released / centered.
    """

    x: ContinuousSource | None = None
    y: ContinuousSource | None = None
    pulse: PulseSource | None = None
    event: EventName | None = None
    hold: HoldState | None = None
    analog: ContinuousSource | None = None
    when: Condition | None = Field(None, description="Pulse only runs while this holds")
    requires: Condition | None = Field(None, description="State gate for events")
    as_: Literal["full_pull"] | None = Field(None, alias="as", description="Trigger output level")
    max_hz: float | None = Field(None, gt=0, description="Pulse rate cap")

    @property
    def kind(self) -> str:
        if self.x is not None or self.y is not None:
            return "axes"
        for k in ("pulse", "event", "hold", "analog"):
            if getattr(self, k) is not None:
                return k
        return "neutral"

    @model_validator(mode="after")
    def _one_kind(self) -> Binding:
        kinds = [
            k
            for k, on in (
                ("axes", self.x is not None or self.y is not None),
                ("pulse", self.pulse is not None),
                ("event", self.event is not None),
                ("hold", self.hold is not None),
                ("analog", self.analog is not None),
            )
            if on
        ]
        if len(kinds) > 1:
            raise ValueError(f"binding takes one of axes/pulse/event/hold/analog, got {kinds}")
        if self.when is not None and self.pulse is None:
            raise ValueError("'when' only applies to pulse bindings")
        if self.requires is not None and self.event is None:
            raise ValueError("'requires' only applies to event bindings")
        if self.max_hz is not None and self.pulse is None:
            raise ValueError("'max_hz' only applies to pulse bindings")
        return self


NEUTRAL = Binding()

ALLOWED_KINDS = {
    PortType.STICK: {"axes", "neutral"},
    PortType.BUTTON: {"event", "pulse", "hold", "neutral"},
    PortType.TRIGGER: {"event", "hold", "analog", "neutral"},
}


class ModeProfile(_Strict):
    also: list[str] = Field(default_factory=list, description="Other game modes sharing these bindings")
    vehicle: bool = Field(False, description="DualSense steers; body sticks must stay neutral (rule 4)")
    ports: dict[PortName, Binding] = Field(
        default_factory=dict, description="Ports not listed are released / neutral in this mode"
    )

    @field_validator("ports", mode="before")
    @classmethod
    def _neutral_strings(cls, v: object) -> object:
        if isinstance(v, dict):
            out = {}
            for port, b in v.items():
                if b == "neutral" or b is None:
                    out[port] = {}
                else:
                    out[port] = b
            return out
        return v


class Profile(_Strict):
    profile: str
    game_fps: int = Field(60, gt=0, description="Game frame rate; 30 fps needs the 30/30 ms press rule")
    press: PressTiming = PressTiming()
    gestures: Gestures = Gestures()
    ports: dict[PortName, Port]
    modes: dict[str, ModeProfile]

    @model_validator(mode="after")
    def _check(self) -> Profile:
        game_modes = {m.name.lower() for m in Mode}
        covered: dict[str, str] = {}
        for name, mode in self.modes.items():
            for gm in [name, *mode.also]:
                if gm not in game_modes:
                    raise ValueError(f"mode {gm!r} is not one of {sorted(game_modes)}")
                if gm in covered:
                    raise ValueError(f"mode {gm!r} defined by both {covered[gm]!r} and {name!r}")
                covered[gm] = name
            for port, b in mode.ports.items():
                if port not in self.ports:
                    raise ValueError(f"mode {name!r} binds unknown port {port!r}")
                ptype = self.ports[port].type
                if b.kind not in ALLOWED_KINDS[ptype]:
                    raise ValueError(f"{name}.{port}: a {ptype} port can't take a {b.kind} binding")
                if ptype == PortType.STICK and mode.vehicle and b.kind != "neutral":
                    raise ValueError(f"{name}.{port}: body sticks must be neutral in vehicle modes")
                if b.as_ is not None and ptype != PortType.TRIGGER:
                    raise ValueError(f"{name}.{port}: 'as' only applies to trigger ports")
                if b.event == "shoot" and b.requires != "aiming":
                    raise ValueError(f"{name}.{port}: shoot must require aiming (rule 7)")
                if b.event == "enter_exit" and mode.vehicle and b.requires is None:
                    raise ValueError(f"{name}.{port}: exiting a vehicle needs a state gate (rule 7)")
        if "on_foot" not in covered:
            raise ValueError("profile must define on_foot")
        return self

    def mode_for(self, game_mode: str) -> ModeProfile | None:
        for name, mode in self.modes.items():
            if game_mode == name or game_mode in mode.also:
                return mode
        return None

    def binding(self, game_mode: str, port: str) -> Binding:
        mode = self.mode_for(game_mode)
        if mode is None or port not in mode.ports:
            return NEUTRAL
        return mode.ports[port]


def load_profile(path: str | Path) -> Profile:
    with Path(path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Profile.model_validate(data)

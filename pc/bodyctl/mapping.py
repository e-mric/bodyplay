"""Profile bindings -> gamepad controls (Tier 0 on the PC).

Tier 1 does the same on the node in C++ from the pushed profile; Tier 2+ on the
hub. Each port emits one fixed PS5 control; the current mode picks which
gesture drives it (hard rule 4). Unlisted ports are released / centered.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from bodyctl.config.profile import Binding, PortType, Profile
from bodyctl.output.gamepad import Gamepad

# Gesture events that drive pulse bindings.
PULSE_EVENTS = {"step_rhythm": "step", "stroke_rhythm": "step", "fast_feet": "step"}


class State(Protocol):
    def source(self, name: str) -> float: ...

    def condition(self, name: str) -> bool: ...


class Event(Protocol):
    name: str


class BindingEngine:
    def __init__(self, profile: Profile, gamepad: Gamepad, mode: str = "on_foot"):
        self.profile = profile
        self.gamepad = gamepad
        self.mode = mode
        self._last_pulse_us: dict[str, int] = {}

    def set_mode(self, mode: str, now_us: int) -> None:
        if self.profile.mode_for(mode) is None:
            raise ValueError(f"profile {self.profile.profile!r} has no mode {mode!r}")
        if mode != self.mode:
            self.mode = mode
            self.gamepad.release_all(now_us)

    def apply(self, state: State, events: Iterable[Event], now_us: int) -> None:
        names = [e.name for e in events]
        gp = self.gamepad
        for port, pdef in self.profile.ports.items():
            b = self.profile.binding(self.mode, port)
            if pdef.type == PortType.STICK:
                x = state.source(b.x) if b.x else 0.0
                y = state.source(b.y) if b.y else 0.0
                gp.set_stick(pdef.ps5, x, y)
            else:
                self._button(port, pdef.ps5, pdef.type, b, state, names, now_us)
        self._tier0(state, names, now_us)
        gp.update(now_us)

    def _in_modes(self, modes: list[str]) -> bool:
        return any(self.profile.mode_for(m) is self.profile.mode_for(self.mode) for m in modes)

    def _tier0(self, state: State, events: list[str], now_us: int) -> None:
        """Virtual-pad-only controls (profile `tier0`), applied after the ports so the
        wheel's stick overrides the port binding while the wheel is open."""
        extras, gp = self.profile.tier0, self.gamepad
        wheel = extras.weapon_wheel
        if wheel is not None:
            open_ = self._in_modes(wheel.modes) and state.condition("wheel_open")
            gp.hold(wheel.hold, open_, now_us)
            if open_:
                gp.set_stick(wheel.select, state.source("wheel_x"), state.source("wheel_y"))
        flight = extras.flight
        if flight is not None and self._in_modes(flight.modes) and state.condition("flying"):
            # Rule 4's one exception: the body flies (the port binding keeps the stick neutral).
            gp.set_stick(flight.stick, state.source("fly_roll"), state.source("fly_pitch"))
        cover = extras.cover
        if cover is not None and self._in_modes(cover.modes):
            for name in events:
                if name in ("crouch", "stand"):
                    gp.press(cover.tap, now_us)

    def _button(
        self, port: str, ctl: str, ptype: PortType, b: Binding, state: State, events: list[str], now_us: int
    ) -> None:
        gp = self.gamepad
        if b.kind == "event":
            gp.hold(ctl, False, now_us)
            if ptype == PortType.TRIGGER:
                gp.set_trigger(ctl, 0.0)
            for name in events:
                if name == b.event and (b.requires is None or state.condition(b.requires)):
                    gp.press(ctl, now_us)
        elif b.kind == "pulse":
            gp.hold(ctl, False, now_us)
            if b.when is not None and not state.condition(b.when):
                return
            trigger_event = PULSE_EVENTS[b.pulse]
            for name in events:
                if name != trigger_event:
                    continue
                last = self._last_pulse_us.get(port)
                if b.max_hz and last is not None and now_us - last < 1e6 / b.max_hz:
                    continue
                self._last_pulse_us[port] = now_us
                gp.press(ctl, now_us)
        elif b.kind == "hold":
            gp.hold(ctl, state.condition(b.hold), now_us)
        elif b.kind == "analog":
            gp.hold(ctl, False, now_us)
            gp.set_trigger(ctl, max(0.0, state.source(b.analog)))
        else:  # neutral
            gp.hold(ctl, False, now_us)
            if ptype == PortType.TRIGGER:
                gp.set_trigger(ctl, 0.0)

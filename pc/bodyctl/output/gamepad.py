"""Virtual gamepad for Tier 0: PS5-named controls in, an Xbox 360 pad out.

chiaki-ng reads the virtual Xbox 360 pad (vgamepad + ViGEmBus on Windows) and
maps it to the DualSense: A = cross, B = circle, X = square, Y = triangle,
LB/RB = L1/R1, LT/RT = L2/R2, LS/RS = L3/R3.

Every press goes through a PressScheduler that enforces hard rule 8 (held
>= hold_ms, released >= release_ms) so a 30 fps game sees every press.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from typing import Protocol

from bodyctl.output.marker import Marker, NullMarker

BUTTONS = (
    "cross", "circle", "square", "triangle", "l1", "r1", "l3", "r3",
    "dpad_up", "dpad_down", "dpad_left", "dpad_right", "options", "create", "ps",
)  # fmt: skip
TRIGGERS = ("l2", "r2")
STICKS = ("left_stick", "right_stick")
STICK_NEUTRAL_EPS = 0.02


class PressScheduler:
    """One button: momentary presses (queued) or a continuous hold, never shorter
    than hold_us on or release_us off."""

    def __init__(self, hold_us: int, release_us: int, max_queue: int = 2):
        self.hold_us = hold_us
        self.release_us = release_us
        self.max_queue = max_queue
        self.level = False
        self._edge_us = -(10**15)
        self._pending = 0
        self._held = False

    def press(self, now_us: int) -> None:
        if not self._held:
            self._pending = min(self._pending + 1, self.max_queue)
        self.tick(now_us)

    def hold(self, on: bool, now_us: int) -> None:
        self._held = on
        if on:
            self._pending = 0
        self.tick(now_us)

    def clear(self, now_us: int) -> None:
        self._pending = 0
        self._held = False
        self.tick(now_us)

    def tick(self, now_us: int) -> bool:
        if self.level:
            if not self._held and now_us - self._edge_us >= self.hold_us:
                self.level = False
                self._edge_us = now_us
        elif (self._held or self._pending) and now_us - self._edge_us >= self.release_us:
            if not self._held:
                self._pending -= 1
            self.level = True
            self._edge_us = now_us
        return self.level


@dataclass(frozen=True)
class Report:
    buttons: frozenset[str] = frozenset()
    left: tuple[float, float] = (0.0, 0.0)  # x right, y forward/up, -1..1
    right: tuple[float, float] = (0.0, 0.0)
    l2: float = 0.0
    r2: float = 0.0


class Backend(Protocol):
    def send(self, report: Report, now_us: int) -> None: ...

    def close(self) -> None: ...


@dataclass
class DryRunBackend:
    """Records every report; prints changes if verbose. For macOS dev and tests."""

    verbose: bool = False
    history: list[tuple[int, Report]] = field(default_factory=list)

    def send(self, report: Report, now_us: int) -> None:
        self.history.append((now_us, report))
        if self.verbose:
            print(f"{now_us / 1e6:10.3f}s {report}")

    def close(self) -> None:
        pass


class VGamepadBackend:
    """Virtual Xbox 360 pad through vgamepad + ViGEmBus (Windows only)."""

    def __init__(self) -> None:
        import vgamepad as vg

        self._vg = vg
        self._pad = vg.VX360Gamepad()
        b = vg.XUSB_BUTTON
        self._map = {
            "cross": b.XUSB_GAMEPAD_A,
            "circle": b.XUSB_GAMEPAD_B,
            "square": b.XUSB_GAMEPAD_X,
            "triangle": b.XUSB_GAMEPAD_Y,
            "l1": b.XUSB_GAMEPAD_LEFT_SHOULDER,
            "r1": b.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "l3": b.XUSB_GAMEPAD_LEFT_THUMB,
            "r3": b.XUSB_GAMEPAD_RIGHT_THUMB,
            "dpad_up": b.XUSB_GAMEPAD_DPAD_UP,
            "dpad_down": b.XUSB_GAMEPAD_DPAD_DOWN,
            "dpad_left": b.XUSB_GAMEPAD_DPAD_LEFT,
            "dpad_right": b.XUSB_GAMEPAD_DPAD_RIGHT,
            "options": b.XUSB_GAMEPAD_START,
            "create": b.XUSB_GAMEPAD_BACK,
            "ps": b.XUSB_GAMEPAD_GUIDE,
        }

    def send(self, report: Report, now_us: int) -> None:
        pad = self._pad
        for name, btn in self._map.items():
            if name in report.buttons:
                pad.press_button(button=btn)
            else:
                pad.release_button(button=btn)
        pad.left_joystick_float(x_value_float=report.left[0], y_value_float=report.left[1])
        pad.right_joystick_float(x_value_float=report.right[0], y_value_float=report.right[1])
        pad.left_trigger_float(value_float=report.l2)
        pad.right_trigger_float(value_float=report.r2)
        pad.update()

    def close(self) -> None:
        self._pad.reset()
        self._pad.update()


def open_backend(name: str | None, verbose: bool = False) -> Backend:
    """`vgamepad` (default on Windows), `dry-run` (default elsewhere)."""
    if name is None:
        name = "vgamepad" if sys.platform == "win32" else "dry-run"
    if name == "vgamepad":
        return VGamepadBackend()
    if name == "dry-run":
        return DryRunBackend(verbose=verbose)
    raise ValueError(f"unknown gamepad backend {name!r} (vgamepad, dry-run)")


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


class Gamepad:
    """PS5-named controls -> scheduled report -> backend, with the output marker."""

    def __init__(self, backend: Backend, hold_ms: int, release_ms: int, marker: Marker | None = None):
        self.backend = backend
        self.marker = marker or NullMarker()
        self._sched = {n: PressScheduler(hold_ms * 1000, release_ms * 1000) for n in BUTTONS + TRIGGERS}
        self._sticks = {s: (0.0, 0.0) for s in STICKS}
        self._analog = {t: 0.0 for t in TRIGGERS}
        self.report = Report()
        self.changes = 0

    def press(self, control: str, now_us: int) -> None:
        self._sched[control].press(now_us)

    def hold(self, control: str, on: bool, now_us: int) -> None:
        self._sched[control].hold(on, now_us)

    def set_stick(self, stick: str, x: float, y: float) -> None:
        self._sticks[stick] = (_clamp(x), _clamp(y))

    def set_trigger(self, trigger: str, value: float) -> None:
        self._analog[trigger] = _clamp(value, 0.0, 1.0)

    def release_all(self, now_us: int) -> None:
        for s in self._sched.values():
            s.clear(now_us)
        for s in STICKS:
            self._sticks[s] = (0.0, 0.0)
        for t in TRIGGERS:
            self._analog[t] = 0.0

    def update(self, now_us: int) -> Report:
        for s in self._sched.values():
            s.tick(now_us)
        buttons = frozenset(n for n in BUTTONS if self._sched[n].level)
        trig = {t: 1.0 if self._sched[t].level else self._analog[t] for t in TRIGGERS}
        new = replace(
            self.report,
            buttons=buttons,
            left=self._sticks["left_stick"],
            right=self._sticks["right_stick"],
            l2=trig["l2"],
            r2=trig["r2"],
        )
        if new != self.report:
            if _time_critical(self.report, new):
                self.marker.pulse()
            self.report = new
            self.changes += 1
            self.backend.send(new, now_us)
        return new

    def close(self) -> None:
        self.backend.close()
        self.marker.close()


def _time_critical(old: Report, new: Report) -> bool:
    """Changes the latency rig should see: a button or trigger going down, or a stick
    leaving neutral (run start). Continuous stick wiggles don't flash the marker."""
    if new.buttons - old.buttons:
        return True
    if (new.l2 > 0.5 >= old.l2) or (new.r2 > 0.5 >= old.r2):
        return True
    for o, n in ((old.left, new.left), (old.right, new.right)):
        if max(map(abs, o)) <= STICK_NEUTRAL_EPS < max(map(abs, n)):
            return True
    return False

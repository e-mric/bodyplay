"""Tier 0: your real controller and the body on one virtual pad.

Games on PC often read a single controller (GTA V reads XInput slot 1 only). With the
virtual pad in that slot, Bodyplay reads your real controller from another slot and
merges it into the virtual pad: buttons from either, a stick you move wins over the
body's, the further-pulled trigger wins. So you can pick up the controller any time.

Windows only (XInput). The real controller's input goes straight through, polled at
POLL_HZ on its own thread: it isn't delayed by the camera loop or held for rule 8's
30 ms (you press it yourself).
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass

POLL_HZ = 500
STICK_DEADZONE = 0.24  # XInput's recommended left-stick deadzone (7849 / 32767)
TRIGGER_DEADZONE = 30 / 255  # XINPUT_GAMEPAD_TRIGGER_THRESHOLD

# XInput button bits -> PS5 names (the Xbox backend maps them the other way).
XINPUT_BUTTONS = {
    0x0001: "dpad_up", 0x0002: "dpad_down", 0x0004: "dpad_left", 0x0008: "dpad_right",
    0x0010: "options", 0x0020: "create", 0x0040: "l3", 0x0080: "r3", 0x0100: "l1", 0x0200: "r1",
    0x0400: "ps", 0x1000: "cross", 0x2000: "circle", 0x4000: "square", 0x8000: "triangle",
}  # fmt: skip


@dataclass(frozen=True)
class PhysicalPad:
    buttons: frozenset[str] = frozenset()
    left: tuple[float, float] = (0.0, 0.0)  # y up, deadzone removed
    right: tuple[float, float] = (0.0, 0.0)
    l2: float = 0.0
    r2: float = 0.0


def _stick(x: int, y: int) -> tuple[float, float]:
    """Raw XInput axes -> -1..1 with a radial deadzone, rescaled so motion starts at 0."""
    fx, fy = x / 32767, y / 32767
    mag = (fx * fx + fy * fy) ** 0.5
    if mag <= STICK_DEADZONE:
        return (0.0, 0.0)
    k = min(1.0, (mag - STICK_DEADZONE) / (1 - STICK_DEADZONE)) / mag
    return (max(-1.0, min(1.0, fx * k)), max(-1.0, min(1.0, fy * k)))


def _trigger(v: int) -> float:
    f = v / 255
    return 0.0 if f <= TRIGGER_DEADZONE else (f - TRIGGER_DEADZONE) / (1 - TRIGGER_DEADZONE)


def from_xinput(buttons: int, lt: int, rt: int, lx: int, ly: int, rx: int, ry: int) -> PhysicalPad:
    return PhysicalPad(
        frozenset(name for bit, name in XINPUT_BUTTONS.items() if buttons & bit),
        _stick(lx, ly),
        _stick(rx, ry),
        _trigger(lt),
        _trigger(rt),
    )


def merge(report, phys: PhysicalPad | None):
    """The body's report with the real controller on top."""
    from dataclasses import replace

    if phys is None:
        return report
    return replace(
        report,
        buttons=report.buttons | phys.buttons,
        left=phys.left if phys.left != (0.0, 0.0) else report.left,
        right=phys.right if phys.right != (0.0, 0.0) else report.right,
        l2=max(report.l2, phys.l2),
        r2=max(report.r2, phys.r2),
    )


class XInput:
    """XInputGetState through ctypes."""

    def __init__(self) -> None:
        import ctypes

        class Gamepad(ctypes.Structure):
            _fields_ = [("buttons", ctypes.c_ushort), ("lt", ctypes.c_ubyte), ("rt", ctypes.c_ubyte),
                        ("lx", ctypes.c_short), ("ly", ctypes.c_short), ("rx", ctypes.c_short),
                        ("ry", ctypes.c_short)]  # fmt: skip

        class State(ctypes.Structure):
            _fields_ = [("packet", ctypes.c_uint), ("pad", Gamepad)]

        self._ctypes, self._State = ctypes, State
        for dll in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                self._dll = getattr(ctypes.windll, dll)
                break
            except OSError:
                continue
        else:
            raise OSError("no XInput DLL")

    def state(self, slot: int) -> PhysicalPad | None:
        s = self._State()
        if self._dll.XInputGetState(slot, self._ctypes.byref(s)) != 0:
            return None
        p = s.pad
        return from_xinput(p.buttons, p.lt, p.rt, p.lx, p.ly, p.rx, p.ry)

    def connected(self) -> list[int]:
        return [i for i in range(4) if self.state(i) is not None]


class Passthrough:
    """Polls the first real controller (any XInput slot but the virtual pad's) and
    feeds it to `gamepad.set_physical`."""

    def __init__(self, gamepad, virtual_slot: int, xinput: XInput | None = None):
        self.gamepad = gamepad
        self.virtual_slot = virtual_slot
        self.xinput = xinput or XInput()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="pad-passthrough", daemon=True)

    def real_slots(self) -> list[int]:
        return [i for i in self.xinput.connected() if i != self.virtual_slot]

    def start(self) -> Passthrough:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1)

    def _run(self) -> None:
        last: PhysicalPad | None = None
        slots: list[int] = []
        next_scan = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_scan:  # a controller turned on later shows up within a second
                slots, next_scan = self.real_slots(), now + 1.0
            phys = None
            for i in slots:
                phys = self.xinput.state(i)
                if phys is not None:
                    break
            if phys != last:
                self.gamepad.set_physical(phys, time.perf_counter_ns() // 1000)  # tier0's clock
                last = phys
            self._stop.wait(1 / POLL_HZ)


def available() -> bool:
    return sys.platform == "win32"

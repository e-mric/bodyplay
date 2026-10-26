"""SDL game controller mapping for the node's HID gamepad, and a chiaki-ng launcher.

chiaki-ng reads controllers through SDL. A generic HID gamepad that SDL doesn't
know needs a mapping string (SDL_GAMECONTROLLERCONFIG) that says which HID button
and axis is which. Button/axis layout: firmware/lib/padmap/hid_report.h.

Open question (CLAUDE.md, M3): whether SDL picks the node up without this, and
whether the axis indices below match what Windows reports. Check in chiaki-ng's
controller settings or SDL's testcontroller; adjust AXES if a stick is swapped.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# XIAO ESP32S3 defaults from the Arduino core (variants/XIAO_ESP32S3/pins_arduino.h).
DEFAULT_VID = 0x2886
DEFAULT_PID = 0x0056

BUTTONS = {
    "a": "b0",  # cross
    "b": "b1",  # circle
    "x": "b4",  # square (west)
    "y": "b3",  # triangle (north)
    "leftshoulder": "b6",
    "rightshoulder": "b7",
    "back": "b10",  # create
    "start": "b11",  # options
    "guide": "b12",  # PS
    "leftstick": "b13",
    "rightstick": "b14",
    "touchpad": "b2",
    "dpup": "h0.1",
    "dpright": "h0.2",
    "dpdown": "h0.4",
    "dpleft": "h0.8",
}
# HID usages X, Y, Z, Rx, Ry, Rz enumerate as a0..a5 on Windows; the node sends the
# left stick on X/Y, right stick on Z/Rz and L2/R2 on Rx/Ry (0..127, released 0).
AXES = {
    "leftx": "a0",
    "lefty": "a1",
    "rightx": "a2",
    "righty": "a5",
    "lefttrigger": "+a3",
    "righttrigger": "+a4",
}


def sdl_guid(vid: int = DEFAULT_VID, pid: int = DEFAULT_PID, version: int = 0) -> str:
    """SDL2 joystick GUID for a USB device: bus 0x0003, no name CRC, LE vendor/product/version."""

    def le16(v: int) -> str:
        return f"{v & 0xFF:02x}{(v >> 8) & 0xFF:02x}"

    return "0300" + "0000" + le16(vid) + "0000" + le16(pid) + "0000" + le16(version) + "0000"


def mapping(name: str = "Bodyplay Belt", vid: int = DEFAULT_VID, pid: int = DEFAULT_PID) -> str:
    platform = {"win32": "Windows", "darwin": "Mac OS X"}.get(sys.platform, "Linux")
    fields = [f"{k}:{v}" for k, v in {**BUTTONS, **AXES}.items()]
    return ",".join([sdl_guid(vid, pid), name, *fields, f"platform:{platform}"]) + ","


def find_chiaki(explicit: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit)
    for name in ("chiaki-ng", "chiaki"):
        found = shutil.which(name)
        if found:
            return Path(found)
    candidates = []
    if sys.platform == "win32":
        bases = [os.environ.get("PROGRAMFILES", r"C:\Program Files"), os.environ.get("LOCALAPPDATA")]
        for base in filter(None, bases):
            candidates += [
                Path(base) / "chiaki-ng" / "chiaki.exe",
                Path(base) / "chiaki-ng" / "chiaki-ng.exe",
            ]
    elif sys.platform == "darwin":
        candidates += [Path("/Applications/chiaki-ng.app/Contents/MacOS/chiaki")]
    return next((c for c in candidates if c.exists()), None)


def launch_chiaki(exe: Path, vid: int = DEFAULT_VID, pid: int = DEFAULT_PID) -> subprocess.Popen:
    env = dict(os.environ)
    existing = env.get("SDL_GAMECONTROLLERCONFIG", "")
    env["SDL_GAMECONTROLLERCONFIG"] = mapping(vid=vid, pid=pid) + ("\n" + existing if existing else "")
    return subprocess.Popen([str(exe)], env=env)

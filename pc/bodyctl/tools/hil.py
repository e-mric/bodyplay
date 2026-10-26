"""Hardware-in-the-loop bench test: the hub drives each Access controller port in
turn while you watch a PS5 screen that shows the inputs and confirm what you see.
Also how the stick axis-order question gets answered (Tip vs Ring 1)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from bodyctl.config.compile import PORT_INDEX
from bodyctl.config.profile import PortType, Profile
from bodyctl.output.device import DeviceLink
from bodyctl.protocol import Command

STICK_DIRS = [(1, "up"), (2, "right"), (3, "down"), (4, "left")]
PS5_NAMES = {
    "cross": "Cross (X)", "circle": "Circle", "square": "Square", "triangle": "Triangle",
    "r2": "R2", "l2": "L2", "left_stick": "left stick", "right_stick": "right stick",
}  # fmt: skip

Ask = Callable[[str], bool]


def run_hil(link: DeviceLink, profile: Profile, ask: Ask, hold_s: float = 1.0) -> list[dict]:
    """Returns one result row per check: port, what was driven, what you confirmed."""
    results = []

    def drive(port: int, level: int) -> None:
        link.command_values(Command.HIL, port, level)

    try:
        for name, port in sorted(profile.ports.items(), key=lambda kv: PORT_INDEX[kv[0]]):
            idx = PORT_INDEX[name]
            label = PS5_NAMES.get(port.ps5, port.ps5)
            if port.type == PortType.BUTTON:
                drive(idx, 1)
                time.sleep(hold_s)
                ok = ask(f"{name}: is {label} shown pressed?")
                drive(idx, 0)
                results.append({"port": name, "check": "press", "expect": label, "ok": ok})
            elif port.type == PortType.TRIGGER:
                for level, desc in ((500, "half"), (1000, "fully")):
                    drive(idx, level)
                    time.sleep(hold_s)
                    ok = ask(f"{name}: is {label} pulled {desc}?")
                    results.append({"port": name, "check": f"{desc} pull", "expect": label, "ok": ok})
                drive(idx, 0)
            else:
                for level, desc in STICK_DIRS:
                    drive(idx, level)
                    time.sleep(hold_s)
                    ok = ask(f"{name}: does the {label} point {desc}?")
                    results.append({"port": name, "check": desc, "expect": label, "ok": ok})
                drive(idx, 0)
                ok = ask(f"{name}: is the {label} back at centre?")
                results.append({"port": name, "check": "centre", "expect": label, "ok": ok})
    finally:
        link.command_values(Command.HIL, -1, 0)
    return results


def save_results(results: list[dict], out_dir: str | Path = "data/results") -> Path:
    path = Path(out_dir) / f"hil_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2))
    return path

"""Record your real controller: buttons, trigger pressure and sticks, at POLL_HZ.

To learn how a game wants its controls pressed (GTA 6's gym: how hard L2 + R2 go down
through a rep, how long they're held), play it yourself with the controller and
replay the curve with the body. Windows only (XInput). Writes a CSV: one row per
change, times in ms from the start, raw XInput trigger values (0-255) next to the
0..1 pressure the passthrough uses.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

POLL_HZ = 250


def _row(t_ms: float, slot: int, raw, pad) -> list:
    return [
        f"{t_ms:.1f}", slot, " ".join(sorted(pad.buttons)), raw.lt, raw.rt,
        f"{pad.l2:.3f}", f"{pad.r2:.3f}",
        f"{pad.left[0]:.2f}", f"{pad.left[1]:.2f}", f"{pad.right[0]:.2f}", f"{pad.right[1]:.2f}",
    ]  # fmt: skip


def record(out: str | Path, seconds: float = 600.0, slot: int | None = None) -> Path:
    from bodyctl.output.passthrough import XInput, from_xinput

    xi = XInput()
    slots = [slot] if slot is not None else xi.connected()
    if not slots:
        raise SystemExit("no controller found (XInput): turn it on and try again")
    out = Path(out).with_suffix(".csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"recording controller slot(s) {', '.join(str(s + 1) for s in slots)} to {out}; Ctrl+C to stop")
    s = xi._State()
    last: dict[int, tuple] = {}
    t0 = time.perf_counter()
    rows = 0
    peak = {"l2": 0, "r2": 0}
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_ms", "slot", "buttons", "lt_raw", "rt_raw", "l2", "r2", "lx", "ly", "rx", "ry"])
        try:
            while time.perf_counter() - t0 < seconds:
                t_ms = (time.perf_counter() - t0) * 1000
                for i in slots:
                    if xi._dll.XInputGetState(i, xi._ctypes.byref(s)) != 0:
                        continue
                    p = s.pad
                    key = (p.buttons, p.lt, p.rt, p.lx, p.ly, p.rx, p.ry)
                    if last.get(i) != key:
                        last[i] = key
                        w.writerow(_row(t_ms, i + 1, p, from_xinput(*key)))
                        rows += 1
                        peak["l2"], peak["r2"] = max(peak["l2"], p.lt), max(peak["r2"], p.rt)
                time.sleep(1 / POLL_HZ)
        except KeyboardInterrupt:
            pass
    dur = time.perf_counter() - t0
    print(
        f"saved {rows} changes over {dur:.0f} s to {out} (highest L2 {peak['l2']}/255, R2 {peak['r2']}/255)"
    )
    return out

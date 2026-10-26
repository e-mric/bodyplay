"""Profile -> what the firmware consumes.

Gesture parameters travel as (id, float) pairs. Ids mirror
firmware/lib/gestures/param_table.cpp (append-only; pc/tests checks both lists
match). The same flattening feeds `--set name=value` for the replay tool.
"""

from __future__ import annotations

import struct

from bodyctl.config.profile import Binding, PortType, Profile
from bodyctl.protocol import EventCode, Mode, crc8

BELT_PARAM_IDS: dict[str, int] = {
    "step.peak_g": 1,
    "step.refractory_ms": 2,
    "step.stop_after_intervals": 3,
    "cadence_curve.0": 4,
    "cadence_curve.1": 5,
    "cadence_curve.2": 6,
    "jump.dip_g": 7,
    "jump.dip_min_ms": 8,
    "jump.push_jerk": 9,
    "jump.crouch_hold_ms": 10,
    "jump.min_conf": 11,
    "jump.freefall_g": 12,
    "jump.running_dip_min_ms": 13,
    "lean.deadzone_deg": 14,
    "lean.full_deg": 15,
    "twist.deadzone_deg": 16,
    "twist.full_deg": 17,
    "twist.max_rate": 18,
    "twist.rezero_still_ms": 19,
}


def flatten_gestures(profile: Profile) -> dict[str, float]:
    """Every belt parameter by its firmware name, from the profile (defaults included)."""
    g = profile.gestures.model_dump()
    out: dict[str, float] = {}
    for name in BELT_PARAM_IDS:
        head, _, tail = name.partition(".")
        val = g[head][int(tail)] if head == "cadence_curve" else g[head][tail]
        out[name] = float(val)
    return out


def belt_params(profile: Profile) -> list[tuple[int, float]]:
    return [(BELT_PARAM_IDS[k], v) for k, v in flatten_gestures(profile).items()]


# --- profile binary (pushed to node / dongle / hub) -----------------------------
#
#   "BPPF" | version u8 | n_params u8 | n_ports u8 | n_modes u8 | hold_ms u16 | release_ms u16
#   n_params x (id u8, value f32)
#   n_ports  x (port u8, type u8, output u8)
#   n_modes  x (mode_mask u16, n_bindings u8,
#               n_bindings x (port u8, kind u8, a u8, b u8, cond u8, flags u8, max_hz u8))
#   crc8 u8 over everything before it
#
# Mirrored by firmware/lib/padmap/profile_bin.h. Unlisted ports in a mode are neutral.


PROFILE_MAGIC = b"BPPF"
PROFILE_VERSION = 1

PORT_INDEX = {f"{c}-E{i}": n for n, (c, i) in enumerate((c, i) for c in "AB" for i in range(1, 5))}
PORT_TYPE_ID = {PortType.STICK: 0, PortType.BUTTON: 1, PortType.TRIGGER: 2}
OUTPUT_ID = {
    "left_stick": 0, "right_stick": 1, "l2": 2, "r2": 3,
    "cross": 10, "circle": 11, "square": 12, "triangle": 13, "l1": 14, "r1": 15, "l3": 16, "r3": 17,
    "dpad_up": 18, "dpad_down": 19, "dpad_left": 20, "dpad_right": 21,
    "options": 22, "create": 23, "touchpad": 24, "ps": 25,
}  # fmt: skip
KIND_ID = {"neutral": 0, "axes": 1, "pulse": 2, "event": 3, "hold": 4, "analog": 5}
SOURCE_ID = {
    "run_cadence": 1, "lean": 2, "twist": 3, "stroke_rhythm": 4,
    "step_back_or_lean_back": 5, "hand_height": 6,
}  # fmt: skip
PULSE_ID = {"step_rhythm": 1, "stroke_rhythm": 2, "fast_feet": 3}
CONDITION_ID = {"aiming": 1, "stopped": 2, "cadence_above_sprint": 3}
EVENT_ID = {
    "jump": EventCode.JUMP, "crouch": EventCode.CROUCH, "punch": EventCode.PUNCH, "shoot": EventCode.SHOOT,
    "enter_exit": EventCode.ENTER_EXIT, "flick": EventCode.FLICK, "stomp": EventCode.STOMP,
}  # fmt: skip
FLAG_FULL_PULL = 0x01


def _binding_bytes(port: str, b: Binding) -> bytes:
    kind = b.kind
    a = bb = cond = flags = 0
    if kind == "axes":
        a = SOURCE_ID[b.x] if b.x else 0
        bb = SOURCE_ID[b.y] if b.y else 0
    elif kind == "pulse":
        a = PULSE_ID[b.pulse]
        cond = CONDITION_ID[b.when] if b.when else 0
    elif kind == "event":
        a = int(EVENT_ID[b.event])
        cond = CONDITION_ID[b.requires] if b.requires else 0
    elif kind == "hold":
        a = CONDITION_ID[b.hold]
    elif kind == "analog":
        a = SOURCE_ID[b.analog]
    if b.as_ == "full_pull":
        flags |= FLAG_FULL_PULL
    max_hz = min(255, round(b.max_hz)) if b.max_hz else 0
    return struct.pack("<7B", PORT_INDEX[port], KIND_ID[kind], a, bb, cond, flags, max_hz)


def compile_profile(profile: Profile) -> bytes:
    params = belt_params(profile)
    ports = sorted(profile.ports.items(), key=lambda kv: PORT_INDEX[kv[0]])
    out = bytearray(PROFILE_MAGIC)
    out += struct.pack(
        "<BBBBHH",
        PROFILE_VERSION,
        len(params),
        len(ports),
        len(profile.modes),
        profile.press.hold_ms,
        profile.press.release_ms,
    )
    for pid, val in params:
        out += struct.pack("<Bf", pid, val)
    for name, port in ports:
        out += struct.pack("<BBB", PORT_INDEX[name], PORT_TYPE_ID[port.type], OUTPUT_ID[port.ps5])
    for name, mode in profile.modes.items():
        mask = 0
        for gm in [name, *mode.also]:
            mask |= 1 << int(Mode[gm.upper()])
        binds = sorted(mode.ports.items(), key=lambda kv: PORT_INDEX[kv[0]])
        out += struct.pack("<HB", mask, len(binds))
        for port, b in binds:
            out += _binding_bytes(port, b)
    out.append(crc8(bytes(out)))
    return bytes(out)

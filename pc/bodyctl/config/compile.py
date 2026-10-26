"""Profile -> what the firmware consumes.

Gesture parameters travel as (id, float) pairs. Ids mirror
firmware/lib/gestures/param_table.cpp (append-only; pc/tests checks both lists
match). The same flattening feeds `--set name=value` for the replay tool.
"""

from __future__ import annotations

from bodyctl.config.profile import Profile

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

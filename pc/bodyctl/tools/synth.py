"""Synthetic IMU traces, so gesture code and tests can start before hardware arrives.

Signals are in the node's body frame after gravity alignment: x forward, y left,
z up, accelerometer reading +1 g on z when standing still. "Noisy" variants add
sensor noise, gyro bias drift, strap wobble after impacts and a tilted mount, so
the firmware's calibration has something to undo.

Each trace carries ground-truth LABEL records (event name + time), which the
native replay tests score detections against. Real recordings replace these in
M3, but the synthetic set stays as regression tests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from bodyctl.protocol import MAX_RAW_SAMPLES_PER_FRAME, Kind, NodeId, Packet, RawSample
from bodyctl.trace import DEFAULT_IMU, Label, TraceWriter

# The LSM6DSOX's nominal 1666 Hz ODR; synthetic samples sit on an exact 600 us
# grid so per-sample timestamps (frame time + i * period) are exact.
PERIOD_US = 600
ODR_HZ = 1e6 / PERIOD_US
DT = PERIOD_US / 1e6
LINK_DELAY_US = 800  # fake receive delay so t_rx_us differs from t_sensor_us


@dataclass
class Signal:
    """Body-frame accel (g, incl. gravity) and gyro (dps) at ODR_HZ, plus labels."""

    accel: np.ndarray  # (n, 3)
    gyro: np.ndarray  # (n, 3)
    labels: list[Label] = field(default_factory=list)
    node_id: int = NodeId.BELT

    @classmethod
    def still(cls, seconds: float, node_id: int = NodeId.BELT) -> Signal:
        n = int(round(seconds * ODR_HZ))
        accel = np.zeros((n, 3))
        accel[:, 2] = 1.0
        return cls(accel, np.zeros((n, 3)), [], node_id)

    @property
    def n(self) -> int:
        return len(self.accel)

    def t_us(self, i: int) -> int:
        return i * PERIOD_US

    def label(self, event: str, i: int, **extra: int | float | str) -> None:
        self.labels.append(Label(event, self.t_us(i), dict(extra)))


def _idx(t_s: float) -> int:
    return int(round(t_s * ODR_HZ))


def _half_sine(n: int) -> np.ndarray:
    return np.sin(np.pi * np.arange(n) / max(n, 1))


# --- scenario builders ----------------------------------------------------------


def add_jump(
    sig: Signal,
    t0_s: float,
    dip_g: float = 0.5,
    dip_ms: float = 180,
    push_g: float = 1.5,
    push_ms: float = 250,
    flight_ms: float = 350,
    hold_ms: float = 0,
) -> None:
    """Counter-movement jump starting at t0: unweighting dip, optional held squat,
    push-off, flight (free fall reads 0 g), landing impact.

    Labels: jump_onset (dip start, the tell), jump (reversal into push-off, where the
    detector should fire), takeoff, landing. A held squat over 300 ms also labels
    squat_hold, which the detector should report as CROUCH.
    """
    i = _idx(t0_s)
    a = sig.accel[:, 2]
    sig.label("jump_onset", i)
    n_dip = _idx(dip_ms / 1000)
    if hold_ms > 0:
        # Slow descent: dip, then braking back to 0 g, then a held squat. The detector
        # calls it a crouch 300 ms into the dip when no sharp reversal came.
        sig.label("squat_hold", i + _idx(0.3))
        a[i : i + n_dip] -= dip_g * _half_sine(n_dip)
        i += n_dip
        n_brake = _idx(dip_ms / 1000)
        a[i : i + n_brake] += dip_g * _half_sine(n_brake)
        i += n_brake
        i += _idx(hold_ms / 1000)
    else:
        a[i : i + n_dip] -= dip_g * _half_sine(n_dip)
        i += n_dip
    sig.label("jump", i)
    n_push = _idx(push_ms / 1000)
    a[i : i + n_push] += push_g * _half_sine(n_push)
    i += n_push
    sig.label("takeoff", i)
    n_flight = _idx(flight_ms / 1000)
    a[i : i + n_flight] = 0.0
    i += n_flight
    sig.label("landing", i)
    n_land = _idx(0.15)
    a[i : i + n_land] += 3.0 * _half_sine(n_land)
    # Small pitch wobble on landing.
    sig.gyro[i : i + n_land, 1] += 40 * np.sin(2 * np.pi * 8 * np.arange(n_land) * DT)


def add_steps(sig: Signal, t0_s: float, t1_s: float, cadence_hz: float, ramp_s: float = 0.0) -> None:
    """Running in place from t0 to t1 at cadence_hz steps/s (optionally ramping up).

    Below 2 steps/s it's marching: a gentle bounce plus a heel-strike pulse, never
    leaving the ground. From 2 steps/s it's running in place: a ground contact (half
    sine) then a flight phase where the accelerometer reads free fall (0 g), sized so
    the mean stays at 1 g. The torso rolls side to side by about 3 degrees per step.
    Labels: run_start (first strike), step (each strike), run_stop (time after the
    last strike when no next step comes).
    """
    t = t0_s
    first = True
    k = 0
    last_strike = None
    while t < t1_s:
        frac = min(1.0, (t - t0_s) / ramp_s) if ramp_s > 0 else 1.0
        c = cadence_hz * (0.6 + 0.4 * frac) if ramp_s > 0 else cadence_hz
        period = 1.0 / c
        i = _idx(t)
        if i >= sig.n:
            break
        if first:
            sig.label("run_start", i)
            first = False
        sig.label("step", i, cadence_hz=round(c, 3))
        last_strike = (i, period)
        n = _idx(period)
        seg = np.arange(min(n, sig.n - i)) * DT
        if c < 2.0:
            bounce = 0.2 * np.cos(2 * np.pi * seg / period)
            bounce += 0.4 * np.exp(-0.5 * ((seg - 0.03) / 0.02) ** 2)
        else:
            contact = 0.5 * period
            amp = np.pi * (period - contact) / (2 * contact)  # half-sine mean balances 1 g of flight
            bounce = np.where(seg < contact, amp * np.sin(np.pi * seg / contact), -1.0)
        bounce -= bounce.mean()  # zero-mean over the step: the body doesn't drift up or down
        sig.accel[i : i + len(seg), 2] += bounce
        side = 1 if k % 2 == 0 else -1
        roll = side * 3.0 * np.sin(np.pi * seg / period)
        sig.gyro[i : i + len(seg), 0] += np.gradient(roll, DT)
        sig.accel[i : i + len(seg), 1] += 0.15 * side * np.sin(np.pi * seg / period)
        t += period
        k += 1
    if last_strike is not None:
        i, period = last_strike
        stop = i + _idx(1.5 * period)
        if stop < sig.n:
            sig.label("run_stop", stop)


def add_weight_shift(sig: Signal, t0_s: float, t1_s: float, amp_deg: float = 5.0) -> None:
    """Swaying side to side while standing (a hard negative for steps and jumps)."""
    i0, i1 = _idx(t0_s), min(_idx(t1_s), sig.n)
    t = np.arange(i1 - i0) * DT
    roll_deg = amp_deg * np.sin(2 * np.pi * 0.5 * t)
    sig.gyro[i0:i1, 0] += np.gradient(roll_deg, DT)
    sig.accel[i0:i1, 1] += np.sin(np.radians(roll_deg))
    sig.accel[i0:i1, 2] += 0.05 * np.sin(2 * np.pi * 1.0 * t)


def add_twist(sig: Signal, t0_s: float, t1_s: float, amp_deg: float = 30.0, hold_s: float = 1.0) -> None:
    """Torso turns left by amp_deg, holds, turns back (camera turn). Labels twist_left at
    the turn start and twist_back when it returns."""
    i0 = _idx(t0_s)
    n = _idx(0.3)
    turn = amp_deg * (1 - np.cos(np.pi * np.arange(n) / n)) / 2
    rate = np.gradient(turn, DT)
    sig.label("twist_left", i0)
    sig.gyro[i0 : i0 + n, 2] += rate
    j = i0 + n + _idx(hold_s)
    sig.label("twist_back", j)
    sig.gyro[j : j + n, 2] -= rate


def add_punch(sig: Signal, t0_s: float, peak_g: float = 3.0) -> None:
    """Wrist node: forward acceleration burst then braking. Labels punch at onset."""
    i = _idx(t0_s)
    sig.label("punch", i)
    n = _idx(0.08)
    sig.accel[i : i + n, 0] += peak_g * _half_sine(n)
    sig.accel[i + n : i + 2 * n, 0] -= peak_g * _half_sine(n)
    sig.gyro[i : i + 2 * n, 2] += 120 * _half_sine(2 * n)


def add_noise(sig: Signal, rng: np.random.Generator, tilt_deg: float = 8.0) -> None:
    """Sensor noise, gyro bias random walk, strap wobble after impacts, tilted mount."""
    n = sig.n
    # Strap wobble: a damped 12 Hz ring excited by vertical jolts (of the clean signal).
    jolt = np.clip(np.abs(np.diff(sig.accel[:, 2], prepend=1.0)) * ODR_HZ - 20, 0, None)
    kernel_t = np.arange(_idx(0.25)) * DT
    kernel = np.exp(-kernel_t / 0.05) * np.sin(2 * np.pi * 12 * kernel_t)
    wobble = np.convolve(jolt, kernel)[:n] * 0.0005
    sig.accel[:, 0] += wobble
    sig.gyro[:, 1] += wobble * 20
    sig.accel += rng.normal(0, 0.02, (n, 3))
    sig.gyro += rng.normal(0, 0.5, (n, 3))
    sig.gyro += np.cumsum(rng.normal(0, 0.002, (n, 3)), axis=0) + rng.normal(0, 1.0, 3)
    # Tilted mount: rotate both vectors about y.
    th = np.radians(tilt_deg)
    rot = np.array([[np.cos(th), 0, np.sin(th)], [0, 1, 0], [-np.sin(th), 0, np.cos(th)]])
    sig.accel = sig.accel @ rot.T
    sig.gyro = sig.gyro @ rot.T


# --- scenarios ----------------------------------------------------------------


def _still() -> Signal:
    return Signal.still(5.0)


def _weight_shift() -> Signal:
    s = Signal.still(8.0)
    add_weight_shift(s, 1.0, 7.0)
    return s


def _jump() -> Signal:
    s = Signal.still(4.0)
    add_jump(s, 1.5)
    return s


def _jump_long_dip() -> Signal:
    s = Signal.still(5.0)
    add_jump(s, 1.5, dip_g=0.4, dip_ms=300, hold_ms=500)
    return s


def _run_start() -> Signal:
    s = Signal.still(7.0)
    add_steps(s, 1.5, 5.5, cadence_hz=2.5, ramp_s=1.5)
    return s


def _run(cadence: float) -> Callable[[], Signal]:
    def build() -> Signal:
        s = Signal.still(8.0)
        add_steps(s, 1.0, 6.5, cadence_hz=cadence)
        return s

    return build


def _twist() -> Signal:
    s = Signal.still(6.0)
    add_twist(s, 1.5, 4.0)
    return s


def _punch() -> Signal:
    s = Signal.still(3.0, node_id=NodeId.WRIST_R)
    add_punch(s, 1.2)
    return s


SCENARIOS: dict[str, Callable[[], Signal]] = {
    "still": _still,
    "weight_shift": _weight_shift,
    "jump": _jump,
    "jump_long_dip": _jump_long_dip,
    "run_start": _run_start,
    "run_1.5": _run(1.5),
    "run_2.5": _run(2.5),
    "run_3.5": _run(3.5),
    "twist": _twist,
    "punch": _punch,
}

# Committed to firmware/test/fixtures/ for the native gesture tests
# (`bodyctl synth --fixtures`); pc/tests checks they match this generator.
FIXTURES: list[tuple[str, bool]] = [
    ("still", False),
    ("weight_shift", False),
    ("weight_shift", True),
    ("jump", False),
    ("jump", True),
    ("jump_long_dip", False),
    ("run_start", False),
    ("run_1.5", False),
    ("run_2.5", False),
    ("run_2.5", True),
    ("run_3.5", False),
    ("twist", False),
]
FIXTURES_DIR = Path(__file__).parents[3] / "firmware" / "test" / "fixtures"


def fixture_name(name: str, noisy: bool) -> str:
    return f"synth_{name}{'_noisy0' if noisy else ''}.bptrace"


def write_fixtures(out_dir: str | Path = FIXTURES_DIR) -> list[Path]:
    out = Path(out_dir)
    return [
        write_trace(build(name, noisy=noisy, seed=0), out / fixture_name(name, noisy), name, noisy, 0)
        for name, noisy in FIXTURES
    ]


def build(name: str, noisy: bool = False, seed: int = 0) -> Signal:
    sig = SCENARIOS[name]()
    if noisy:
        add_noise(sig, np.random.default_rng(seed))
    return sig


def write_trace(sig: Signal, path: str | Path, name: str, noisy: bool, seed: int) -> Path:
    accel_lsb = DEFAULT_IMU["accel_lsb_g"]
    gyro_lsb = DEFAULT_IMU["gyro_lsb_dps"]
    acc = np.clip(np.round(sig.accel / accel_lsb), -32768, 32767).astype(np.int16)
    gyr = np.clip(np.round(sig.gyro / gyro_lsb), -32768, 32767).astype(np.int16)
    header = {
        "source": "synth",
        "scenario": name,
        "noisy": noisy,
        "seed": seed,
        "imu": {**DEFAULT_IMU, "sample_period_us": PERIOD_US},
        "nodes": {str(sig.node_id): {"role": NodeId(sig.node_id).name.lower(), "fw": "synth"}},
        "frame": "body (x forward, y left, z up)" + (", mount tilted" if noisy else ""),
    }
    path = Path(path)
    with TraceWriter(path, header) as w:
        for seq, start in enumerate(range(0, sig.n, MAX_RAW_SAMPLES_PER_FRAME)):
            end = min(start + MAX_RAW_SAMPLES_PER_FRAME, sig.n)
            payload = b"".join(RawSample(tuple(acc[i]), tuple(gyr[i])).pack() for i in range(start, end))
            t_us = sig.t_us(start)
            p = Packet(sig.node_id, seq, t_us, Kind.RAW, value=(end - start, PERIOD_US), payload=payload)
            w.packet(p, t_us + (end - start - 1) * PERIOD_US + LINK_DELAY_US)
        for label in sorted(sig.labels, key=lambda lb: lb.t_us):
            w.label(label)
    return path


def generate_all(out_dir: str | Path, noisy_seeds: int = 1) -> list[Path]:
    """Writes every scenario clean plus `noisy_seeds` noisy variants of each."""
    out = Path(out_dir)
    paths = []
    for name in SCENARIOS:
        paths.append(write_trace(build(name), out / f"synth_{name}.bptrace", name, False, 0))
        for seed in range(noisy_seeds):
            sig = build(name, noisy=True, seed=seed)
            paths.append(write_trace(sig, out / f"synth_{name}_noisy{seed}.bptrace", name, True, seed))
    return paths

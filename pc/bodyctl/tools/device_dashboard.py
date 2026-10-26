"""Tier 1+ dashboard: what the belt node sees and does, live.

Scrolling plot of the IMU's dynamic acceleration (|a| - 1 g) with event markers,
cadence / lean / twist, the HID pad the node is sending, and recording with live
labels so drills (50 jumps, feints...) become test fixtures.

Keys: q quit, m next mode, c calibrate, r record on/off;
while recording: j jump, k crouch, s run start, f feint, space mark.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from bodyctl.output.device import DeviceError, DeviceLink, Received, decode_pad
from bodyctl.protocol import Command, EventCode, Kind, Mode, RawSample, StateField
from bodyctl.trace import DEFAULT_IMU

PLOT_SECONDS = 4.0
MODE_CYCLE = (Mode.ON_FOOT, Mode.CAR, Mode.HELI, Mode.SWIM, Mode.QTE, Mode.PAUSED)
LABEL_KEYS = {
    ord("j"): "jump",
    ord("k"): "squat_hold",
    ord("s"): "run_start",
    ord("f"): "feint",
    ord(" "): "mark",
}
PAD_BUTTONS = {
    0: "X",
    1: "O",
    3: "TRI",
    4: "SQ",
    6: "L1",
    7: "R1",
    8: "L2",
    9: "R2",
    11: "OPT",
    13: "L3",
    14: "R3",
}


@dataclass
class DeviceView:
    """Everything the dashboard shows; updated from the link's reader thread."""

    samples: deque[tuple[int, float]] = field(default_factory=lambda: deque(maxlen=int(1666 * PLOT_SECONDS)))
    events: deque[tuple[int, str, float]] = field(default_factory=lambda: deque(maxlen=50))
    state: dict[str, tuple[float, float]] = field(default_factory=dict)
    pad: dict[str, object] | None = None
    frames: int = 0
    calibrated: bool = False
    last_t_us: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def on_packet(self, rec: Received) -> None:
        p = rec.packet
        with self.lock:
            self.frames += 1
            if p.kind == Kind.RAW:
                lsb = DEFAULT_IMU["accel_lsb_g"]
                for i, s in enumerate(RawSample.unpack_many(p.payload)):
                    t = p.t_sensor_us + i * p.value[1]
                    a = np.linalg.norm(s.accel) * lsb - 1.0
                    self.samples.append((t, float(a)))
                    self.last_t_us = t
            elif p.kind == Kind.EVENT:
                try:
                    name = EventCode(p.code).name.lower()
                except ValueError:
                    name = str(p.code)
                if p.code == EventCode.CALIBRATED:
                    self.calibrated = True
                if p.code != EventCode.STEP:
                    self.events.append((p.t_sensor_us, name, p.confidence / 255))
                self.last_t_us = max(self.last_t_us, p.t_sensor_us)
            elif p.kind == Kind.STATE:
                if p.code == StateField.PAD:
                    self.pad = decode_pad(p.payload)
                else:
                    with contextlib.suppress(ValueError):
                        self.state[StateField(p.code).name.lower()] = (p.value[0], p.value[1])


def draw(
    view: DeviceView, mode: str, recording: str | None, width: int = 1000, height: int = 520
) -> np.ndarray:
    import cv2

    img = np.zeros((height, width, 3), np.uint8)
    plot_w, plot_h, top = width - 300, height - 60, 20
    with view.lock:
        samples = list(view.samples)
        events = list(view.events)
        state = dict(view.state)
        pad = view.pad
        now = view.last_t_us
        frames = view.frames
        calibrated = view.calibrated

    def x_of(t: int) -> int:
        return int(plot_w * (1 - (now - t) / (PLOT_SECONDS * 1e6)))

    def y_of(g: float) -> int:
        return int(top + plot_h / 2 - g * plot_h / 6)  # +-3 g full scale

    for g in (-1, 0, 1, 2):
        cv2.line(img, (0, y_of(g)), (plot_w, y_of(g)), (50, 50, 50), 1)
        cv2.putText(img, f"{g:+d} g", (4, y_of(g) - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (110, 110, 110), 1)
    if len(samples) > 1:
        step = max(1, len(samples) // plot_w)
        pts = np.array([(x_of(t), y_of(a)) for t, a in samples[::step]], np.int32)
        cv2.polylines(img, [pts], False, (80, 220, 120), 1, cv2.LINE_AA)
    for t, name, conf in events:
        x = x_of(t)
        if 0 <= x < plot_w:
            cv2.line(img, (x, top), (x, top + plot_h), (40, 180, 255), 1)
            cv2.putText(
                img, f"{name} {conf:.2f}", (x + 3, top + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (40, 180, 255), 1
            )

    x0, y = plot_w + 12, 28

    def text(s: str, color=(230, 230, 230), scale: float = 0.5) -> None:
        nonlocal y
        cv2.putText(img, s, (x0, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)
        y += int(24 * scale / 0.5)

    text(f"mode {mode}", (40, 180, 255), 0.6)
    text(
        "calibrated" if calibrated else "calibrating: stand still",
        (80, 220, 120) if calibrated else (60, 60, 230),
    )
    cad = state.get("cadence", (0, 0))
    lean = state.get("lean", (0, 0))
    twist = state.get("twist", (0, 0))
    text(f"cadence {cad[0] / 100:4.2f} steps/s  stick {cad[1] / 1000:+.2f}")
    text(f"lean    {lean[0] / 100:+5.1f} deg  stick {lean[1] / 1000:+.2f}")
    text(f"twist   {twist[0] / 100:+5.1f} deg  stick {twist[1] / 1000:+.2f}")
    y += 6
    text("pad (what the PS5 gets via chiaki-ng)", (150, 150, 150), 0.45)
    if pad:
        btns = " ".join(n for b, n in PAD_BUTTONS.items() if pad["buttons"] & (1 << b)) or "-"
        text(f"  {btns}")
        lx, ly = pad["left"]
        rx, ry = pad["right"]
        text(f"  L ({lx:+4d},{ly:+4d})  R ({rx:+4d},{ry:+4d})")
        text(f"  L2 {pad['l2']:3d}  R2 {pad['r2']:3d}")
    else:
        text("  (no report yet)", (150, 150, 150))
    y += 6
    text(f"frames {frames}", (150, 150, 150), 0.45)
    if recording:
        text(f"REC {recording}", (60, 60, 230), 0.5)
        text("j jump  k crouch  s run  f feint  space mark", (150, 150, 150), 0.4)
    cv2.putText(
        img,
        "q quit  m mode  c calibrate  r record",
        (10, height - 14),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (150, 150, 150),
        1,
    )
    return img


def run_device_dashboard(link: DeviceLink, out_dir: str | Path = "data/sessions") -> int:
    import cv2

    from bodyctl.tools.record import Recorder, session_path

    view = DeviceView()
    link.subscribe(view.on_packet)
    recorder: Recorder | None = None
    mode_i = 0
    try:
        hello = link.hello()
        print(f"connected: node {hello.node_id}, firmware {hello.payload.decode(errors='replace')}")
        link.command(Command.RAW_STREAM, 1)
        while True:
            img = draw(view, MODE_CYCLE[mode_i].name.lower(), recorder.path.name if recorder else None)
            cv2.imshow("bodyplay device", img)
            key = cv2.waitKey(30) & 0xFF
            if key == ord("q"):
                break
            try:
                if key == ord("m"):
                    nxt = (mode_i + 1) % len(MODE_CYCLE)
                    link.set_mode(MODE_CYCLE[nxt])
                    mode_i = nxt
                elif key == ord("c"):
                    link.command(Command.CALIBRATE)
                    with view.lock:
                        view.calibrated = False
                elif key == ord("r"):
                    if recorder:
                        recorder.stop()
                        link.command(Command.RAW_STREAM, 1)  # keep the plot alive
                        print(f"saved {recorder.path}")
                        recorder = None
                    else:
                        recorder = Recorder(link, session_path(out_dir), {"node": hello.node_id})
                        recorder.start()
                elif recorder and key in LABEL_KEYS:
                    recorder.label(LABEL_KEYS[key])
            except DeviceError as e:
                print(f"device: {e}")
            time.sleep(0)
    except KeyboardInterrupt:
        pass
    finally:
        if recorder:
            recorder.stop()
            print(f"saved {recorder.path}")
        with contextlib.suppress(DeviceError):
            link.command(Command.RAW_STREAM, 0)
        link.close()
        cv2.destroyAllWindows()
    return 0

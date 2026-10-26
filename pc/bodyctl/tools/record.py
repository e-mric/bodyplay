"""Record a device session to .bptrace: RAW IMU frames plus everything else the
device sends (events, state, pad), with PC receive times. Labels can be added
live (keys in the dashboard) so drills become test fixtures."""

from __future__ import annotations

import contextlib
import threading
import time
from datetime import datetime
from pathlib import Path

from bodyctl.output.device import DeviceLink, Received, now_us
from bodyctl.protocol import Command, EventCode, Kind
from bodyctl.trace import DEFAULT_IMU, Label, TraceWriter


def session_path(out_dir: str | Path, tag: str = "session") -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(out_dir) / f"{tag}_{stamp}.bptrace"


class Recorder:
    def __init__(self, link: DeviceLink, path: Path, header: dict):
        self.link = link
        self.path = path
        self._lock = threading.Lock()
        self._writer = TraceWriter(path, {"source": "record", "imu": DEFAULT_IMU, **header})
        self.frames = 0
        self.events: list[tuple[int, str]] = []
        self._pc_minus_device: int | None = None  # least-delayed RAW frame, as in Trace
        link.subscribe(self._on_packet)

    def _on_packet(self, rec: Received) -> None:
        with self._lock:
            if self._writer is None:
                return
            self._writer.frame(rec.body, rec.t_rx_us)
            self.frames += 1
            p = rec.packet
            if p.kind == Kind.RAW and p.value[0] > 0:
                delta = rec.t_rx_us - (p.t_sensor_us + (p.value[0] - 1) * p.value[1])
                if self._pc_minus_device is None or delta < self._pc_minus_device:
                    self._pc_minus_device = delta
            if p.kind == Kind.EVENT:
                try:
                    name = EventCode(p.code).name.lower()
                except ValueError:
                    name = f"event_{p.code}"
                self.events.append((p.t_sensor_us, name))

    def label(self, event: str, **extra: object) -> None:
        """Mark 'this just happened'. Stored on the device clock once RAW frames have given
        a clock offset; before that on the PC clock (Trace.device_labels converts it)."""
        with self._lock:
            if self._writer is None:
                return
            t = now_us()
            if self._pc_minus_device is None:
                self._writer.label(Label(event, t, {"clock": "pc", **extra}), t)
            else:
                self._writer.label(Label(event, t - self._pc_minus_device, dict(extra)), t)

    def start(self) -> None:
        self.link.command(Command.RAW_STREAM, 1)

    def stop(self) -> None:
        # The device may already be gone; keep what was recorded either way.
        with contextlib.suppress(Exception):
            self.link.command(Command.RAW_STREAM, 0)
        time.sleep(0.05)
        with self._lock:
            if self._writer is not None:
                self._writer.close()
                self._writer = None

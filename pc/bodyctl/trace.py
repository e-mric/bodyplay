"""`.bptrace` recording format.

Layout (little-endian):

    b"BPTRACE\\x01"                      8-byte magic + format version
    uint32 header_len, header JSON      session metadata (see TraceHeader)
    records until EOF:
        uint64 t_rx_us                  receive time on the recorder's clock
        uint8  type                     RecordType
        uint16 len, len bytes           FRAME: packet + payload (unframed)
                                        LABEL: JSON {"event", "t_us", ...}
                                        VIDEO: JSON {"frame", "file"} sync mark

The same file feeds the native gesture tests (replay), the dashboard and ML.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, BinaryIO

from bodyctl.protocol import Kind, Packet, RawSample

MAGIC = b"BPTRACE\x01"
_REC = struct.Struct("<QBH")

# LSM6DSOX at the ranges the node configures (+-8 g, +-2000 dps).
DEFAULT_IMU = {"odr_hz": 1666, "accel_lsb_g": 0.000244, "gyro_lsb_dps": 0.070, "emg_bits": 12}


class RecordType(IntEnum):
    FRAME = 0
    LABEL = 1
    VIDEO = 2


@dataclass
class Label:
    event: str
    t_us: int
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"event": self.event, "t_us": self.t_us, **self.extra}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Label:
        d = dict(d)
        return cls(d.pop("event"), int(d.pop("t_us")), d)


@dataclass
class Record:
    t_rx_us: int
    type: RecordType
    data: bytes

    def packet(self) -> Packet:
        return Packet.unpack(self.data)

    def json(self) -> dict[str, Any]:
        return json.loads(self.data)


class TraceWriter:
    def __init__(self, path: str | Path, header: dict[str, Any]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f: BinaryIO = self.path.open("wb")
        head = json.dumps({"format": 1, **header}, sort_keys=True).encode()
        self._f.write(MAGIC + struct.pack("<I", len(head)) + head)

    def _write(self, t_rx_us: int, rtype: RecordType, data: bytes) -> None:
        if len(data) > 0xFFFF:
            raise ValueError("record too large")
        self._f.write(_REC.pack(t_rx_us, rtype, len(data)) + data)

    def frame(self, body: bytes, t_rx_us: int) -> None:
        self._write(t_rx_us, RecordType.FRAME, body)

    def packet(self, p: Packet, t_rx_us: int) -> None:
        self.frame(p.pack(), t_rx_us)

    def label(self, label: Label, t_rx_us: int | None = None) -> None:
        data = json.dumps(label.to_json()).encode()
        self._write(label.t_us if t_rx_us is None else t_rx_us, RecordType.LABEL, data)

    def video_mark(self, t_rx_us: int, frame: int, file: str) -> None:
        self._write(t_rx_us, RecordType.VIDEO, json.dumps({"frame": frame, "file": file}).encode())

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> TraceWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Trace:
    """A whole trace loaded in memory (recordings are tens of MB at most)."""

    def __init__(self, header: dict[str, Any], records: list[Record]):
        self.header = header
        self.records = records

    @classmethod
    def read(cls, path: str | Path) -> Trace:
        with Path(path).open("rb") as f:
            return cls.read_stream(f)

    @classmethod
    def read_stream(cls, f: BinaryIO) -> Trace:
        if f.read(len(MAGIC)) != MAGIC:
            raise ValueError("not a .bptrace file (bad magic)")
        (hlen,) = struct.unpack("<I", f.read(4))
        header = json.loads(f.read(hlen))
        records = list(_iter_records(f))
        return cls(header, records)

    def frames(self) -> Iterator[tuple[int, Packet]]:
        for r in self.records:
            if r.type == RecordType.FRAME:
                yield r.t_rx_us, r.packet()

    def labels(self) -> list[Label]:
        return [Label.from_json(r.json()) for r in self.records if r.type == RecordType.LABEL]

    def pc_minus_device_us(self) -> int | None:
        """Clock offset from RAW frames: the least-delayed frame (receive time minus the
        device time of its last sample) best approximates pc_clock - device_clock."""
        best = None
        for t_rx, p in self.frames():
            if p.kind != Kind.RAW or p.value[0] <= 0:
                continue
            delta = t_rx - (p.t_sensor_us + (p.value[0] - 1) * p.value[1])
            best = delta if best is None else min(best, delta)
        return best

    def device_labels(self) -> list[Label]:
        """Labels on the device clock (live labels from the dashboard are stamped on the PC)."""
        offset = None
        out = []
        for lb in self.labels():
            if lb.extra.get("clock") == "pc":
                if offset is None:
                    offset = self.pc_minus_device_us()
                if offset is None:
                    continue
                lb = Label(lb.event, lb.t_us - offset, {k: v for k, v in lb.extra.items() if k != "clock"})
            out.append(lb)
        return out

    def raw_samples(self, node_id: int | None = None) -> Iterator[tuple[int, int, RawSample]]:
        """Yields (node_id, t_us, sample) for every RAW sample, timestamped per sample."""
        for _, p in self.frames():
            if p.kind != Kind.RAW or (node_id is not None and p.node_id != node_id):
                continue
            period = p.value[1]
            for i, s in enumerate(RawSample.unpack_many(p.payload)):
                yield p.node_id, p.t_sensor_us + i * period, s


def _iter_records(f: BinaryIO) -> Iterator[Record]:
    while True:
        head = f.read(_REC.size)
        if not head:
            return
        if len(head) < _REC.size:
            raise ValueError("truncated record header")
        t, rtype, n = _REC.unpack(head)
        data = f.read(n)
        if len(data) < n:
            raise ValueError("truncated record")
        yield Record(t, RecordType(rtype), data)

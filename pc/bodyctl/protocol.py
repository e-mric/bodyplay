"""Bodyplay wire protocol, mirror of firmware/lib/protocol.

Every frame starts with a 16-byte Packet; some kinds append a payload. On serial
links a frame travels as COBS(body + crc8(body)) followed by a 0x00 delimiter.
tests/test_protocol.py checks that these enums match protocol.h.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag

MAGIC = 0xB0
PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 250
PACKET_SIZE = 16
MAX_PAYLOAD_BYTES = MAX_FRAME_BYTES - PACKET_SIZE

_PACKET = struct.Struct("<BBHIBBBBhh")
_RAW_SAMPLE = struct.Struct("<7h")
RAW_SAMPLE_SIZE = _RAW_SAMPLE.size
assert _PACKET.size == PACKET_SIZE


class NodeId(IntEnum):
    BELT = 1
    WRIST_R = 2
    WRIST_L = 3
    DONGLE = 5
    HUB = 10
    PC = 20


class Kind(IntEnum):
    HELLO = 1
    EVENT = 2
    STATE = 3
    SYNC = 4
    PING = 5
    PONG = 6
    MODE = 7
    PROFILE_CHUNK = 8
    LOG = 9
    RAW = 10
    ACK = 11


class EventCode(IntEnum):
    NONE = 0
    JUMP = 1
    CROUCH = 2
    STEP = 3
    RUN_START = 4
    RUN_STOP = 5
    PUNCH = 6
    SHOOT = 7
    AIM_START = 8
    AIM_END = 9
    ENTER_EXIT = 10
    FLICK = 11
    STOMP = 12
    CALIBRATED = 13


class StateField(IntEnum):
    CADENCE = 1
    LEAN = 2
    TWIST = 3
    AIM = 4
    EMG = 5
    BATTERY = 6
    LINK = 7
    LATENCY = 8


class Mode(IntEnum):
    ON_FOOT = 0
    SWIM = 1
    CAR = 2
    BIKE = 3
    BOAT = 4
    HELI = 5
    PLANE = 6
    QTE = 7
    PAUSED = 8


class Flags(IntFlag):
    DUPLICATE = 0x01
    PREDICTED = 0x02
    SYNCED = 0x04


@dataclass
class Packet:
    node_id: int
    seq: int
    t_sensor_us: int
    kind: int
    code: int = 0
    confidence: int = 0
    flags: int = 0
    value: tuple[int, int] = (0, 0)
    payload: bytes = field(default=b"", repr=False)

    def pack(self) -> bytes:
        if len(self.payload) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"payload {len(self.payload)} B exceeds {MAX_PAYLOAD_BYTES} B")
        head = _PACKET.pack(
            MAGIC,
            self.node_id,
            self.seq & 0xFFFF,
            self.t_sensor_us & 0xFFFFFFFF,
            self.kind,
            self.code,
            self.confidence,
            self.flags,
            self.value[0],
            self.value[1],
        )
        return head + self.payload

    @classmethod
    def unpack(cls, data: bytes) -> Packet:
        if len(data) < PACKET_SIZE:
            raise ValueError(f"frame too short: {len(data)} B")
        magic, node, seq, t, kind, code, conf, flags, v0, v1 = _PACKET.unpack_from(data)
        if magic != MAGIC:
            raise ValueError(f"bad magic 0x{magic:02X}")
        return cls(node, seq, t, kind, code, conf, flags, (v0, v1), bytes(data[PACKET_SIZE:]))

    def frame(self) -> bytes:
        return frame_encode(self.pack())


@dataclass(frozen=True)
class RawSample:
    accel: tuple[int, int, int]
    gyro: tuple[int, int, int]
    emg: int = 0

    def pack(self) -> bytes:
        return _RAW_SAMPLE.pack(*self.accel, *self.gyro, self.emg)

    @classmethod
    def unpack_many(cls, data: bytes) -> list[RawSample]:
        if len(data) % RAW_SAMPLE_SIZE:
            raise ValueError("RAW payload is not a whole number of samples")
        out = []
        for f in _RAW_SAMPLE.iter_unpack(data):
            out.append(cls(f[0:3], f[3:6], f[6]))
        return out


MAX_RAW_SAMPLES_PER_FRAME = MAX_PAYLOAD_BYTES // RAW_SAMPLE_SIZE


def hello(node_id: int, seq: int, t_us: int, fw_version: str) -> Packet:
    return Packet(
        node_id,
        seq,
        t_us,
        Kind.HELLO,
        value=(PROTOCOL_VERSION, node_id),
        payload=fw_version.encode(),
    )


# --- CRC-8 (poly 0x07, init 0x00) -------------------------------------------


def _make_crc_table() -> list[int]:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
        table.append(c)
    return table


_CRC_TABLE = _make_crc_table()


def crc8(data: bytes, crc: int = 0) -> int:
    for b in data:
        crc = _CRC_TABLE[crc ^ b]
    return crc


# --- COBS ---------------------------------------------------------------------


def cobs_encode(data: bytes) -> bytes:
    out = bytearray([0])
    code_idx = 0
    code = 1
    for b in data:
        if b == 0:
            out[code_idx] = code
            code_idx = len(out)
            out.append(0)
            code = 1
        else:
            out.append(b)
            code += 1
            if code == 0xFF:
                out[code_idx] = code
                code_idx = len(out)
                out.append(0)
                code = 1
    out[code_idx] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        code = data[i]
        i += 1
        if code == 0:
            raise ValueError("zero byte inside COBS data")
        end = i + code - 1
        if end > n or 0 in data[i:end]:
            raise ValueError("truncated or corrupt COBS block")
        out += data[i:end]
        i = end
        if code != 0xFF and i < n:
            out.append(0)
    return bytes(out)


def frame_encode(body: bytes) -> bytes:
    if len(body) > MAX_FRAME_BYTES:
        raise ValueError(f"frame body {len(body)} B exceeds {MAX_FRAME_BYTES} B")
    return cobs_encode(body + bytes([crc8(body)])) + b"\x00"


class FrameDecoder:
    """Splits a serial byte stream into CRC-checked frame bodies."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.frames_ok = 0
        self.crc_errors = 0
        self.framing_errors = 0

    # Longer than any valid frame: drop it rather than grow without bound on a noisy line.
    MAX_RAW = MAX_FRAME_BYTES + 8

    def feed(self, data: bytes) -> list[bytes]:
        bodies = []
        *complete, tail = (bytes(self._buf) + bytes(data)).split(b"\x00")
        self._buf = bytearray(tail[-(self.MAX_RAW + 1) :])
        for raw in complete:
            if not raw:
                continue
            if len(raw) > self.MAX_RAW:
                self.framing_errors += 1
                continue
            try:
                dec = cobs_decode(raw)
            except ValueError:
                self.framing_errors += 1
                continue
            if len(dec) < 2:
                self.framing_errors += 1
                continue
            if crc8(dec[:-1]) != dec[-1]:
                self.crc_errors += 1
                continue
            self.frames_ok += 1
            bodies.append(dec[:-1])
        return bodies

"""Output marker for Tiers 0-1: tells the marker XIAO (firmware env `marker`) to
light its LED for 20 ms the instant the PC changes an output.

Without a marker board, the dashboard can flash a screen corner instead (M2).
"""

from __future__ import annotations

import time
from typing import Protocol

from bodyctl.protocol import Command, Kind, NodeId, Packet


class Marker(Protocol):
    def pulse(self) -> None: ...

    def close(self) -> None: ...


class NullMarker:
    def pulse(self) -> None:
        pass

    def close(self) -> None:
        pass


class SerialMarker:
    def __init__(self, port: str):
        import serial

        self._ser = serial.Serial(port, 115200, timeout=0, write_timeout=0.05)
        self._seq = 0

    def pulse(self) -> None:
        p = Packet(NodeId.PC, self._seq, time.perf_counter_ns() // 1000, Kind.CMD, Command.MARKER)
        self._seq = (self._seq + 1) & 0xFFFF
        self._ser.write(p.frame())

    def close(self) -> None:
        self._ser.close()


def open_marker(port: str | None) -> Marker:
    return SerialMarker(port) if port else NullMarker()

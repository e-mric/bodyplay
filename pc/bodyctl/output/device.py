"""Serial link to a node, dongle or hub (USB CDC), or to the simulated node.

The PC only configures, records and displays (hard rule 5): profile push, mode,
commands, and reading the event / state / RAW stream. Gameplay never depends on it.
"""

from __future__ import annotations

import queue
import struct
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bodyctl.protocol import MAX_PAYLOAD_BYTES, Command, FrameDecoder, Kind, NodeId, Packet

ACK_TIMEOUT_S = 1.0


def now_us() -> int:
    return time.perf_counter_ns() // 1000


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...

    def read(self) -> bytes: ...  # whatever is available, b"" after a short wait

    def close(self) -> None: ...


class SerialTransport:
    def __init__(self, port: str):
        import serial

        self._ser = serial.Serial(port, 115200, timeout=0.02, write_timeout=0.5)

    def write(self, data: bytes) -> None:
        self._ser.write(data)

    def read(self) -> bytes:
        return self._ser.read(max(1, self._ser.in_waiting))

    def close(self) -> None:
        self._ser.close()


class ProcessTransport:
    """Runs the simulated node (firmware env `nodesim`) and talks to it over pipes."""

    def __init__(self, cmd: list[str]):
        self._p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
        self._q: queue.Queue[bytes] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._p.stdout is not None
        while True:
            chunk = self._p.stdout.read1(4096) if hasattr(self._p.stdout, "read1") else self._p.stdout.read(1)
            if not chunk:
                self._q.put(b"")
                return
            self._q.put(chunk)

    def write(self, data: bytes) -> None:
        assert self._p.stdin is not None
        try:
            self._p.stdin.write(data)
            self._p.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def read(self) -> bytes:
        try:
            return self._q.get(timeout=0.02)
        except queue.Empty:
            return b""

    @property
    def finished(self) -> bool:
        return self._p.poll() is not None

    def close(self) -> None:
        if self._p.poll() is None:
            self._p.terminate()
            try:
                self._p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._p.kill()


@dataclass
class Received:
    t_rx_us: int
    packet: Packet
    body: bytes


class DeviceError(RuntimeError):
    pass


class DeviceLink:
    def __init__(self, transport: Transport):
        self.t = transport
        self._seq = 0
        self._dec = FrameDecoder()
        self._acks: queue.Queue[Packet] = queue.Queue()
        self._logs: queue.Queue[str] = queue.Queue()
        self._hello: queue.Queue[Packet] = queue.Queue()
        self._listeners: list[Callable[[Received], None]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    # -- receive ---------------------------------------------------------------

    def subscribe(self, fn: Callable[[Received], None]) -> None:
        self._listeners.append(fn)

    def _reader(self) -> None:
        while not self._stop.is_set():
            data = self.t.read()
            if not data:
                if getattr(self.t, "finished", False):
                    return
                continue
            t_rx = now_us()
            for body in self._dec.feed(data):
                try:
                    p = Packet.unpack(body)
                except ValueError:
                    continue
                if p.kind == Kind.ACK:
                    self._acks.put(p)
                elif p.kind == Kind.LOG:
                    self._logs.put(p.payload.decode(errors="replace"))
                elif p.kind == Kind.HELLO:
                    self._hello.put(p)
                rec = Received(t_rx, p, body)
                for fn in self._listeners:
                    fn(rec)

    # -- send ------------------------------------------------------------------

    def send(self, p: Packet) -> int:
        p.seq = self._seq
        self._seq = (self._seq + 1) & 0xFFFF
        p.node_id = NodeId.PC
        self.t.write(p.frame())
        return p.seq

    def _wait_ack(self, kind: int, seq: int, timeout: float = ACK_TIMEOUT_S) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                a = self._acks.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                break
            if a.code == kind and (a.value[0] & 0xFFFF) == seq:
                return a.value[1]
        raise DeviceError(f"no ACK for {Kind(kind).name} seq {seq} within {timeout:.1f} s")

    def command(self, cmd: Command, value: int = 0, timeout: float = ACK_TIMEOUT_S) -> None:
        self.command_values(cmd, value, 0, timeout)

    def command_values(self, cmd: Command, v0: int, v1: int, timeout: float = ACK_TIMEOUT_S) -> None:
        seq = self.send(Packet(NodeId.PC, 0, now_us(), Kind.CMD, cmd, value=(v0, v1)))
        status = self._wait_ack(Kind.CMD, seq, timeout)
        if status != 0:
            raise DeviceError(f"{cmd.name} refused (status {status})")

    def hello(self, timeout: float = ACK_TIMEOUT_S) -> Packet:
        while not self._hello.empty():
            self._hello.get_nowait()
        self.command(Command.HELLO, timeout=timeout)
        try:
            return self._hello.get(timeout=timeout)
        except queue.Empty as e:
            raise DeviceError("no HELLO reply") from e

    def stats(self, timeout: float = ACK_TIMEOUT_S) -> str:
        while not self._logs.empty():
            self._logs.get_nowait()
        self.command(Command.STATS, timeout=timeout)
        try:
            return self._logs.get(timeout=timeout)
        except queue.Empty as e:
            raise DeviceError("no stats reply") from e

    def set_mode(self, mode: int) -> None:
        seq = self.send(Packet(NodeId.PC, 0, now_us(), Kind.MODE, int(mode)))
        status = self._wait_ack(Kind.MODE, seq)
        if status != 0:
            raise DeviceError(f"device has no mode {mode} in its profile")

    def push_profile(self, data: bytes, progress: Callable[[int, int], None] | None = None) -> None:
        chunks = [data[i : i + MAX_PAYLOAD_BYTES] for i in range(0, len(data), MAX_PAYLOAD_BYTES)]
        for i, chunk in enumerate(chunks):
            p = Packet(NodeId.PC, 0, now_us(), Kind.PROFILE_CHUNK, value=(i, len(chunks)), payload=chunk)
            seq = self.send(p)
            status = self._wait_ack(Kind.PROFILE_CHUNK, seq)
            if status != 0:
                why = {2: "chunk out of order", 3: "profile rejected (CRC, version or bounds)"}
                raise DeviceError(why.get(status, f"status {status}"))
            if progress:
                progress(i + 1, len(chunks))

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self.t.close()


def decode_pad(payload: bytes) -> dict[str, object]:
    """ST_PAD payload (firmware/lib/padmap/hid_report.h HidGamepad, 11 bytes)."""
    x, y, z, rz, rx, ry, hat, buttons = struct.unpack("<6bBI", payload[:11])
    return {"left": (x, y), "right": (z, rz), "l2": rx, "r2": ry, "hat": hat, "buttons": buttons}


def nodesim_binary() -> Path:
    import os

    from bodyctl.tools.replay import FIRMWARE_DIR, find_pio

    name = "program.exe" if os.name == "nt" else "program"
    path = FIRMWARE_DIR / ".pio" / "build" / "nodesim" / name
    pio = find_pio()
    if pio:
        subprocess.run([pio, "run", "-e", "nodesim", "-s"], cwd=FIRMWARE_DIR, check=True)
    if not path.exists():
        raise FileNotFoundError(f"node simulator not built: {path} (install PlatformIO, docs/setup.md)")
    return path


def open_link(
    port: str | None = None, sim: str | None = None, realtime: bool = True, loop: bool = True
) -> DeviceLink:
    """A real device on `port`, or the simulated node playing the trace `sim`."""
    if sim:
        cmd = [str(nodesim_binary()), sim]
        if realtime:
            cmd.append("--realtime")
        if loop:
            cmd.append("--loop")
        return DeviceLink(ProcessTransport(cmd))
    if not port:
        raise ValueError("give --port (COM5, /dev/cu.usbmodem...) or --sim trace.bptrace")
    return DeviceLink(SerialTransport(port))

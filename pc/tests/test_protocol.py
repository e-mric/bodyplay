import re
from pathlib import Path

import pytest

from bodyctl import protocol as bp
from bodyctl.protocol import (
    EventCode,
    Flags,
    FrameDecoder,
    Kind,
    Mode,
    NodeId,
    Packet,
    RawSample,
    StateField,
    cobs_decode,
    cobs_encode,
    crc8,
)

PROTOCOL_H = Path(__file__).parents[2] / "firmware" / "lib" / "protocol" / "protocol.h"

# Same golden vectors as firmware/test/test_protocol/test_protocol.cpp.
EVENT_BODY = bytes.fromhex("b0013412efcdab890201e601feff2c01")
EVENT_WIRE = bytes.fromhex("12b0013412efcdab890201e601feff2c015900")
HELLO_WIRE = bytes.fromhex("04b00a0703e80301020101010201020a07302e312e303b00")


def golden_event() -> Packet:
    return Packet(
        NodeId.BELT, 0x1234, 0x89ABCDEF, Kind.EVENT, EventCode.JUMP, 230, Flags.DUPLICATE, (-2, 300)
    )


def test_crc8_check_value():
    assert crc8(b"123456789") == 0xF4


def test_golden_vectors():
    assert golden_event().pack() == EVENT_BODY
    assert golden_event().frame() == EVENT_WIRE
    assert bp.hello(NodeId.HUB, 7, 1000, "0.1.0").frame() == HELLO_WIRE


@pytest.mark.parametrize("n", [0, 1, 2, 253, 254, 255, 250, 600])
@pytest.mark.parametrize("pattern", ["zeros", "nonzero", "mixed"])
def test_cobs_round_trip(n, pattern):
    if pattern == "zeros":
        data = bytes(n)
    elif pattern == "nonzero":
        data = bytes(1 + i % 255 for i in range(n))
    else:
        data = bytes(i % 7 for i in range(n))
    enc = cobs_encode(data)
    assert 0 not in enc
    assert cobs_decode(enc) == data


@pytest.mark.parametrize("kind", list(Kind))
def test_round_trip_every_kind(kind):
    payload = bytes(range(14 * 16)) if kind == Kind.RAW else bytes(i % 256 for i in range(kind * 3))
    p = Packet(NodeId.WRIST_R, 65535, 0xFFFFFFFF, kind, 7, 255, Flags.SYNCED, (-32768, 32767), payload)
    dec = FrameDecoder()
    bodies = dec.feed(p.frame())
    assert len(bodies) == 1
    assert Packet.unpack(bodies[0]) == p


def test_decoder_rejects_corruption_and_resyncs():
    wire = bytearray(golden_event().frame())
    wire[5] ^= 0x40
    dec = FrameDecoder()
    assert dec.feed(bytes(wire)) == []
    assert dec.crc_errors == 1
    stream = b"\x55" * 600 + b"\x00" + golden_event().frame() * 3
    # Feed in awkward chunk sizes.
    got = []
    for i in range(0, len(stream), 7):
        got += dec.feed(stream[i : i + 7])
    assert got == [EVENT_BODY] * 3


def test_payload_limit():
    with pytest.raises(ValueError):
        Packet(1, 0, 0, Kind.LOG, payload=bytes(bp.MAX_PAYLOAD_BYTES + 1)).pack()
    assert bp.MAX_RAW_SAMPLES_PER_FRAME == 16


def test_raw_samples():
    samples = [RawSample((1, -2, 3), (-4, 5, -6), 7), RawSample((0, 0, 32767), (0, 0, 0), -1)]
    data = b"".join(s.pack() for s in samples)
    assert RawSample.unpack_many(data) == samples


def test_bad_magic():
    with pytest.raises(ValueError):
        Packet.unpack(b"\x00" + EVENT_BODY[1:])


def _c_enum(text: str, name: str, prefix: str) -> dict[str, int]:
    m = re.search(rf"enum {name} : uint8_t \{{(.*?)\}};", text, re.S)
    assert m, f"enum {name} not found in protocol.h"
    return {
        k.removeprefix(prefix): int(v, 0)
        for k, v in re.findall(r"(\w+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)", m.group(1))
    }


@pytest.mark.parametrize(
    ("c_name", "prefix", "py_enum"),
    [
        ("NodeId", "NODE_", NodeId),
        ("Kind", "KIND_", Kind),
        ("EventCode", "EV_", EventCode),
        ("StateField", "ST_", StateField),
        ("Mode", "MODE_", Mode),
        ("Flags", "FLAG_", Flags),
    ],
)
def test_enums_match_firmware(c_name, prefix, py_enum):
    c = _c_enum(PROTOCOL_H.read_text(), c_name, prefix)
    py = {m.name: int(m.value) for m in py_enum}
    assert c == py


def test_constants_match_firmware():
    text = PROTOCOL_H.read_text()
    assert f"kMagic = 0x{bp.MAGIC:02X}" in text
    assert f"kProtocolVersion = {bp.PROTOCOL_VERSION};" in text
    assert f"kMaxFrameBytes = {bp.MAX_FRAME_BYTES};" in text

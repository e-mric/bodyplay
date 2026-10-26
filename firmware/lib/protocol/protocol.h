// Bodyplay wire protocol, shared by node, dongle and hub.
// Mirrored in pc/bodyctl/protocol.py; pc/tests/test_protocol.py checks that the
// enum values below match the Python ones, so edit both together.
//
// Every frame starts with a 16-byte Packet. Kinds that need more room (RAW,
// PROFILE_CHUNK, LOG, HELLO) append a payload after it; the payload length is
// the frame length minus sizeof(Packet). All fields are little-endian (ESP32,
// x86 and Apple Silicon all are, so the struct is copied as-is).
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace bp {

constexpr uint8_t kMagic = 0xB0;
constexpr uint8_t kProtocolVersion = 1;
// ESP-NOW v1 caps a frame at 250 bytes; keep every frame under it.
constexpr size_t kMaxFrameBytes = 250;
constexpr size_t kMaxPayloadBytes = kMaxFrameBytes - 16;

enum NodeId : uint8_t {
  NODE_BELT = 1,
  NODE_WRIST_R = 2,
  NODE_WRIST_L = 3,
  NODE_DONGLE = 5,
  NODE_HUB = 10,
  NODE_PC = 20,
};

enum Kind : uint8_t {
  KIND_HELLO = 1,          // value[0] = protocol version, value[1] = role; payload = firmware version string
  KIND_EVENT = 2,          // code = EventCode
  KIND_STATE = 3,          // code = StateField
  KIND_SYNC = 4,           // t_sensor_us = sender clock
  KIND_PING = 5,           // t_sensor_us = sender clock at send
  KIND_PONG = 6,           // t_sensor_us = responder clock; payload = original PING packet
  KIND_MODE = 7,           // code = Mode
  KIND_PROFILE_CHUNK = 8,  // value[0] = chunk index, value[1] = chunk count; payload = profile bytes
  KIND_LOG = 9,            // code = log level; payload = text
  KIND_RAW = 10,           // value[0] = sample count, value[1] = sample period (us); payload = RawSample[]
  KIND_ACK = 11,           // code = acked Kind, value[0] = acked seq, value[1] = status (0 ok)
  KIND_CMD = 12,           // PC -> device; code = Command
};

enum Command : uint8_t {
  CMD_STATS = 1,        // reply with LOG lines of per-stage latency histograms
  CMD_STATS_RESET = 2,  // clear the histograms
  CMD_MARKER = 3,       // pulse the marker LED now (Tier 0: the PC's output marker)
  CMD_RAW_STREAM = 4,   // value[0] = 1 to stream RAW IMU frames (recording), 0 to stop
  CMD_CALIBRATE = 5,    // redo the standing calibration (stand still, facing the TV)
  CMD_HELLO = 6,        // reply with HELLO (version, role)
  CMD_HIL = 7,          // hub bench test: value[0] = port (0..7, -1 = end), value[1] = level (see hubcore.h)
};

enum EventCode : uint8_t {
  EV_NONE = 0,
  EV_JUMP = 1,
  EV_CROUCH = 2,
  EV_STEP = 3,
  EV_RUN_START = 4,
  EV_RUN_STOP = 5,
  EV_PUNCH = 6,
  EV_SHOOT = 7,
  EV_AIM_START = 8,
  EV_AIM_END = 9,
  EV_ENTER_EXIT = 10,
  EV_FLICK = 11,
  EV_STOMP = 12,
  EV_CALIBRATED = 13,
};

enum StateField : uint8_t {
  ST_CADENCE = 1,   // value[0] = steps/s x100, value[1] = stick y x1000 (-1000..1000)
  ST_LEAN = 2,      // value[0] = roll deg x100, value[1] = stick x x1000
  ST_TWIST = 3,     // value[0] = yaw deg x100 from neutral, value[1] = stick x x1000
  ST_AIM = 4,       // value[0] = forearm pitch deg x100, value[1] = aiming (0/1)
  ST_EMG = 5,       // value[0] = envelope, value[1] = baseline
  ST_BATTERY = 6,   // value[0] = mV
  ST_LINK = 7,      // value[0] = lost packets, value[1] = late packets
  ST_LATENCY = 8,   // value[0] = stage, value[1] = us (clamped to int16)
  ST_PAD = 9,       // payload = HID gamepad report (padmap/hid_report.h), sent on change
  ST_BODY = 10,     // node -> hub, 250 Hz: value[0] = cadence x100; payload = BodyStatePayload
};

// ST_BODY payload: the continuous sources the hub maps to sticks and triggers.
struct __attribute__((packed)) BodyStatePayload {
  int16_t run_stick;    // x1000, 0..1000
  int16_t lean_stick;   // x1000, -1000..1000
  int16_t twist_stick;  // x1000, -1000..1000
  uint8_t flags;        // BODY_RUNNING | BODY_SPRINTING | BODY_AIMING
  uint8_t reserved;
};
static_assert(sizeof(BodyStatePayload) == 8, "BodyStatePayload must stay 8 bytes");
enum BodyFlags : uint8_t { BODY_RUNNING = 0x01, BODY_SPRINTING = 0x02, BODY_AIMING = 0x04 };

enum Mode : uint8_t {
  MODE_ON_FOOT = 0,
  MODE_SWIM = 1,
  MODE_CAR = 2,
  MODE_BIKE = 3,
  MODE_BOAT = 4,
  MODE_HELI = 5,
  MODE_PLANE = 6,
  MODE_QTE = 7,
  MODE_PAUSED = 8,
};

enum Flags : uint8_t {
  FLAG_DUPLICATE = 0x01,  // second copy of an event (sent ~300 us after the first)
  FLAG_PREDICTED = 0x02,  // fired by the anticipation model, not the state machine
  FLAG_SYNCED = 0x04,     // t_sensor_us is on the hub clock
};

struct __attribute__((packed)) Packet {
  uint8_t magic;
  uint8_t node_id;
  uint16_t seq;
  uint32_t t_sensor_us;
  uint8_t kind;
  uint8_t code;
  uint8_t confidence;
  uint8_t flags;
  int16_t value[2];
};
static_assert(sizeof(Packet) == 16, "Packet must stay 16 bytes");

// One IMU (+EMG) sample in a RAW frame. Units are the sensor's raw LSBs; the
// trace header records the full-scale settings needed to convert them.
struct __attribute__((packed)) RawSample {
  int16_t accel[3];
  int16_t gyro[3];
  int16_t emg;
};
static_assert(sizeof(RawSample) == 14, "RawSample must stay 14 bytes");

constexpr size_t kMaxRawSamplesPerFrame = kMaxPayloadBytes / sizeof(RawSample);

inline Packet make_packet(uint8_t node_id, uint16_t seq, uint32_t t_sensor_us, uint8_t kind,
                          uint8_t code = 0, uint8_t confidence = 0, uint8_t flags = 0,
                          int16_t v0 = 0, int16_t v1 = 0) {
  Packet p;
  p.magic = kMagic;
  p.node_id = node_id;
  p.seq = seq;
  p.t_sensor_us = t_sensor_us;
  p.kind = kind;
  p.code = code;
  p.confidence = confidence;
  p.flags = flags;
  p.value[0] = v0;
  p.value[1] = v1;
  return p;
}

// Serializes header + payload into out. Returns bytes written, or 0 if it doesn't fit.
inline size_t pack(const Packet& p, const uint8_t* payload, size_t payload_len, uint8_t* out,
                   size_t out_cap) {
  if (payload_len > kMaxPayloadBytes || out_cap < sizeof(Packet) + payload_len) return 0;
  memcpy(out, &p, sizeof(Packet));
  if (payload_len) memcpy(out + sizeof(Packet), payload, payload_len);
  return sizeof(Packet) + payload_len;
}

// Parses a frame body. Returns false on short frames or a bad magic byte.
inline bool unpack(const uint8_t* data, size_t len, Packet* p, const uint8_t** payload,
                   size_t* payload_len) {
  if (len < sizeof(Packet) || data[0] != kMagic) return false;
  memcpy(p, data, sizeof(Packet));
  if (payload) *payload = data + sizeof(Packet);
  if (payload_len) *payload_len = len - sizeof(Packet);
  return true;
}

}  // namespace bp

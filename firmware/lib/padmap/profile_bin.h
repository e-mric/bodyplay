// Compiled profile, as pushed by `bodyctl device push-profile`.
// Format: pc/bodyctl/config/compile.py (the source of truth; pc/tests and
// test_padmap check both sides against the committed gta5.bpprofile fixture).
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace bp {

constexpr uint8_t kProfileVersion = 1;
constexpr int kMaxPorts = 8;      // A-E1..A-E4, B-E1..B-E4
constexpr int kMaxModes = 12;
constexpr int kMaxBindings = 8;
constexpr int kMaxParams = 64;
constexpr size_t kMaxProfileBytes = 1024;

enum PortType : uint8_t { PORT_STICK = 0, PORT_BUTTON = 1, PORT_TRIGGER = 2 };

// PS5 controls a port can emit.
enum Output : uint8_t {
  OUT_LEFT_STICK = 0,
  OUT_RIGHT_STICK = 1,
  OUT_L2 = 2,
  OUT_R2 = 3,
  OUT_CROSS = 10,
  OUT_CIRCLE = 11,
  OUT_SQUARE = 12,
  OUT_TRIANGLE = 13,
  OUT_L1 = 14,
  OUT_R1 = 15,
  OUT_L3 = 16,
  OUT_R3 = 17,
  OUT_DPAD_UP = 18,
  OUT_DPAD_DOWN = 19,
  OUT_DPAD_LEFT = 20,
  OUT_DPAD_RIGHT = 21,
  OUT_OPTIONS = 22,
  OUT_CREATE = 23,
  OUT_TOUCHPAD = 24,
  OUT_PS = 25,
};
constexpr uint8_t kFirstButton = OUT_CROSS;
constexpr uint8_t kLastButton = OUT_PS;

enum BindingKind : uint8_t { BIND_NEUTRAL = 0, BIND_AXES, BIND_PULSE, BIND_EVENT, BIND_HOLD, BIND_ANALOG };
enum Source : uint8_t {
  SRC_NONE = 0,
  SRC_RUN_CADENCE = 1,
  SRC_LEAN = 2,
  SRC_TWIST = 3,
  SRC_STROKE_RHYTHM = 4,
  SRC_STEP_BACK = 5,
  SRC_HAND_HEIGHT = 6,
};
enum Pulse : uint8_t { PULSE_STEP_RHYTHM = 1, PULSE_STROKE_RHYTHM = 2, PULSE_FAST_FEET = 3 };
enum Condition : uint8_t { COND_NONE = 0, COND_AIMING = 1, COND_STOPPED = 2, COND_SPRINTING = 3 };
constexpr uint8_t kFlagFullPull = 0x01;

struct PortDef {
  bool present = false;
  uint8_t type = PORT_BUTTON;
  uint8_t output = 0;
};

struct Binding {
  uint8_t port = 0;
  uint8_t kind = BIND_NEUTRAL;
  uint8_t a = 0;  // axes: x source; pulse: Pulse; event: EventCode; hold: Condition; analog: source
  uint8_t b = 0;  // axes: y source
  uint8_t cond = COND_NONE;  // pulse: when; event: requires
  uint8_t flags = 0;
  uint8_t max_hz = 0;
};

struct ModeDef {
  uint16_t mask = 0;  // bit per protocol Mode this entry covers
  uint8_t n = 0;
  Binding bindings[kMaxBindings];
};

struct ParamValue {
  uint8_t id;
  float value;
};

struct ProfileBin {
  uint16_t hold_ms = 30;
  uint16_t release_ms = 30;
  uint8_t n_params = 0;
  ParamValue params[kMaxParams];
  PortDef ports[kMaxPorts];
  uint8_t n_modes = 0;
  ModeDef modes[kMaxModes];

  // Parses and validates (magic, version, CRC, bounds). On failure returns false
  // and leaves *error pointing at a short reason.
  bool parse(const uint8_t* data, size_t len, const char** error);

  const ModeDef* mode(uint8_t game_mode) const;
  // Binding for a port in a mode; nullptr = neutral (unlisted).
  const Binding* binding(uint8_t game_mode, uint8_t port) const;
};

}  // namespace bp

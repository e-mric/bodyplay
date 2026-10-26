// Hub analog outputs: MCP4728 codes for the Access controller's stick and
// trigger ports, with the hard rule 1 clamp.
//
// HARD RULE 1: never drive an expansion-port pin above 1.8 V. Every code that
// leaves this file passes through clamp_code(), and encode_* clamp again, so
// no caller can exceed kMaxCode. test_hubout checks it exhaustively. Hardware
// adds a 1 kOhm series resistor and a Schottky clamp to the port's Vdd.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace bp {

// MCP4728 with internal Vref 2.048 V, gain 1: code = mV * 4095 / 2048.
constexpr uint32_t kDacVrefMv = 2048;
constexpr uint32_t kDacFullScale = 4095;
constexpr uint32_t kPortVddMv = 1800;  // Access controller expansion port Vdd
constexpr uint32_t kPortMaxMv = 1750;  // 50 mV margin under Vdd
constexpr uint16_t kMaxCode = static_cast<uint16_t>(kPortMaxMv * kDacFullScale / kDacVrefMv);  // floor -> 3499
static_assert(kMaxCode == 3499, "clamp must stay at floor(1.75 / 2.048 * 4095)");
static_assert(static_cast<uint64_t>(kMaxCode) * kDacVrefMv / kDacFullScale < kPortVddMv, "clamp under 1.8 V");

constexpr uint16_t clamp_code(int32_t code) {
  return code < 0 ? 0 : code > kMaxCode ? kMaxCode : static_cast<uint16_t>(code);
}

constexpr uint16_t code_for_mv(int32_t mv) {
  return clamp_code(static_cast<int32_t>((static_cast<int64_t>(mv) * kDacFullScale + kDacVrefMv / 2) / kDacVrefMv));
}

constexpr uint32_t mv_for_code(uint16_t code) { return static_cast<uint32_t>(code) * kDacVrefMv / kDacFullScale; }

// Per-axis calibration (set in the PS5's Access controller settings test screen).
struct StickAxisCal {
  uint16_t neutral_mv = 900;  // ~Vdd/2: confirm against Sony's stick table
  uint16_t span_mv = 850;     // full deflection = neutral +- span (clamped to 0..1750 mV)
  bool invert = false;
};

struct TriggerCal {
  uint16_t released_mv = 0;
  uint16_t full_mv = 1750;
};

// value -1..1 -> DAC code (clamped).
uint16_t stick_code(float value, const StickAxisCal& cal);
// value 0..1 -> DAC code (clamped).
uint16_t trigger_code(float value, const TriggerCal& cal);

// MCP4728 "multi-write" for one channel: 3 bytes (channel 0..3, Vref internal,
// gain 1, powered up, update on write). Returns bytes written (3) or 0.
size_t mcp4728_channel_write(uint8_t channel, uint16_t code, uint8_t* out, size_t cap);
// Sequential multi-write of four channels: 12 bytes.
size_t mcp4728_write_all(const uint16_t codes[4], uint8_t* out, size_t cap);

}  // namespace bp

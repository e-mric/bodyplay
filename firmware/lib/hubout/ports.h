// Hub port wiring and output frame: which DAC channel or relay drives each
// Access controller expansion port, and the values the hub writes this tick.
//
// Ports (profile names): A-E1..A-E4 = index 0..3 on controller A,
// B-E1..B-E4 = 4..7 on controller B. Hardware (hardware/wiring_tier2.md):
//   DAC A (I2C bus 0): ch0/ch1 = A-E1 stick (Tip / Ring 1)
//   DAC B (I2C bus 1): ch0/ch1 = B-E1 stick, ch2 = B-E2 trigger, ch3 = B-E3 trigger
//   photoMOS relays:   A-E2, A-E3, A-E4, B-E4
#pragma once

#include <stdint.h>

#include "dac.h"
#include "padmap.h"
#include "profile_bin.h"

namespace bp {

enum PortHw : uint8_t { HW_NONE = 0, HW_STICK, HW_TRIGGER, HW_RELAY };

struct PortWiring {
  uint8_t hw = HW_NONE;
  uint8_t dac = 0;      // 0 = DAC A, 1 = DAC B
  uint8_t channel = 0;  // stick: first of two channels (Tip), +1 = Ring 1
  uint8_t relay = 0;    // relay index 0..3
};

// The board as built (see header comment).
const PortWiring& port_wiring(uint8_t port);

struct HubCalibration {
  StickAxisCal stick[kMaxPorts][2];  // per stick port, Tip / Ring 1
  TriggerCal trigger[kMaxPorts];
  bool swap_xy[kMaxPorts] = {};  // open question: Sony's jack/plug tables disagree on axis order
};

struct OutputFrame {
  uint16_t dac[2][4] = {};  // codes, already clamped (hard rule 1)
  bool relay[4] = {};
  bool operator==(const OutputFrame& o) const;
  bool operator!=(const OutputFrame& o) const { return !(*this == o); }
};

// Checks that every port the profile uses is wired for its type.
// Returns the first bad port index, or -1 if the profile fits the board.
int check_profile_fits(const ProfileBin& p);

// Report -> DAC codes and relay states. Unused channels sit at their neutral
// (sticks) or released (triggers) level; relays open.
OutputFrame build_output(const ProfileBin& p, const PadReport& r, const HubCalibration& cal);

}  // namespace bp

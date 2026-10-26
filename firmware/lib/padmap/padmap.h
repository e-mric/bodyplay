// Profile bindings -> gamepad report, on the device (Tier 1 node / dongle;
// the hub reuses the scheduler and mapper for its ports in M4).
// Same semantics as pc/bodyctl/mapping.py (Tier 0).
#pragma once

#include <stdint.h>

#include "profile_bin.h"

namespace bp {

// One button: momentary presses (queued) or a hold, never shorter than hold_us on
// or release_us off (hard rule 8: a 30 fps game sees every press; mash cap ~16 Hz).
class PressScheduler {
 public:
  void configure(uint32_t hold_us, uint32_t release_us, uint8_t max_queue = 2) {
    hold_us_ = hold_us;
    release_us_ = release_us;
    max_queue_ = max_queue;
  }
  void press(uint32_t now_us);
  void hold(bool on, uint32_t now_us);
  void clear(uint32_t now_us);
  bool tick(uint32_t now_us);
  bool level() const { return level_; }

 private:
  uint32_t hold_us_ = 30000;
  uint32_t release_us_ = 30000;
  uint8_t max_queue_ = 2;
  bool level_ = false;
  bool started_ = false;  // no edge yet: the first press may start immediately
  uint32_t edge_us_ = 0;
  uint8_t pending_ = 0;
  bool held_ = false;
};

struct PadReport {
  uint32_t buttons = 0;  // bit (output - kFirstButton) per button Output
  float lx = 0, ly = 0;  // -1..1, y forward/up
  float rx = 0, ry = 0;
  float l2 = 0, r2 = 0;  // 0..1

  bool button(uint8_t output) const { return buttons & (1u << (output - kFirstButton)); }
  bool operator==(const PadReport& o) const {
    return buttons == o.buttons && lx == o.lx && ly == o.ly && rx == o.rx && ry == o.ry && l2 == o.l2 &&
           r2 == o.r2;
  }
  bool operator!=(const PadReport& o) const { return !(*this == o); }
};

// What the mapper reads from the gesture layer.
class BodyState {
 public:
  virtual ~BodyState() = default;
  virtual float source(uint8_t src) const = 0;  // Source
  virtual bool condition(uint8_t cond) const = 0;  // Condition
};

class PadMapper {
 public:
  void set_profile(const ProfileBin& p);
  const ProfileBin& profile() const { return profile_; }
  bool set_mode(uint8_t game_mode, uint32_t now_us);  // false if the profile has no such mode
  uint8_t mode() const { return mode_; }

  // events: protocol EventCodes fired since the last call (already confidence-gated).
  // Returns true if the report changed; *time_critical says whether the marker LED
  // should flash (a button/trigger went down or a stick left neutral).
  bool apply(const BodyState& state, const uint8_t* events, int n_events, uint32_t now_us,
             bool* time_critical);
  const PadReport& report() const { return report_; }
  void release_all(uint32_t now_us);

 private:
  int slot(uint8_t output) const;  // scheduler index for a button/trigger output, -1 if none
  void drive_button(const Binding* b, uint8_t port, uint8_t output, uint8_t type, const BodyState& st,
                    const uint8_t* events, int n, uint32_t now);

  ProfileBin profile_;
  uint8_t mode_ = 0;  // MODE_ON_FOOT
  static constexpr int kSlots = (kLastButton - kFirstButton + 1) + 2;  // buttons + L2 + R2
  PressScheduler sched_[kSlots];
  float analog_[2] = {0, 0};  // L2, R2 analog values
  float stick_[2][2] = {{0, 0}, {0, 0}};
  uint32_t last_pulse_us_[kMaxPorts] = {};
  bool pulsed_[kMaxPorts] = {};
  PadReport report_;
};

}  // namespace bp

// Gesture thresholds. Defaults match pc/bodyctl/config/profile.py; the pushed
// profile overrides them at runtime (thresholds are config, not constants).
#pragma once

#include <stdint.h>

namespace bp {

struct StepParams {
  float peak_g = 0.25f;             // vertical accel above 1 g that counts as a step
  uint32_t refractory_ms = 200;     // ignore new peaks this long after a step
  float stop_after_intervals = 1.5f;  // running stops after this many missed intervals
};

struct JumpParams {
  float dip_g = -0.30f;              // vertical accel (minus 1 g) that starts a dip
  uint32_t dip_min_ms = 40;          // dip must last this long before a reversal counts
  float push_jerk = 8.0f;            // upward jerk (g/s) marking the push-off reversal
  uint32_t crouch_hold_ms = 300;     // dip without reversal this long -> CROUCH
  float min_conf = 0.6f;
  float freefall_g = -0.85f;         // below this is flight (running), not a dip
  uint32_t running_dip_min_ms = 100;  // stricter dip while running
};

struct LeanParams {
  float deadzone_deg = 6.0f;
  float full_deg = 25.0f;
};

struct TwistParams {
  float deadzone_deg = 15.0f;
  float full_deg = 45.0f;
  float max_rate = 1.0f;
  uint32_t rezero_still_ms = 1000;
};

struct BeltParams {
  StepParams step;
  float cadence_curve[3] = {1.5f, 2.5f, 3.5f};  // walk, jog (full stick), sprint
  JumpParams jump;
  LeanParams lean;
  TwistParams twist;
};

}  // namespace bp

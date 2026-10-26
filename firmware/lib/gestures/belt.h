// Belt node gestures (Tier 1+): steps and cadence, jump and crouch, lean, torso twist.
//
// Input: body-frame IMU samples (see mount.h) at ~1.66 kHz. Output: events
// (EV_JUMP, EV_CROUCH, EV_STEP, EV_RUN_START, EV_RUN_STOP) through a small
// queue, plus continuous state for the sticks.
//
// The jump fires on the tell, not the movement: a counter-movement dip (vertical
// accel below dip_g, but above free fall) that reverses into push-off with high
// jerk. Free fall marks running flight, which is how running in place doesn't
// look like a string of jumps. A dip held without a sharp reversal is a crouch;
// a sharp push out of a crouch is still a jump.
#pragma once

#include <stdint.h>

#include "params.h"
#include "vec3.h"

namespace bp {

struct GestureEvent {
  uint8_t code;        // EventCode (protocol.h)
  uint8_t confidence;  // 0..255
  uint32_t t_us;       // sample time that fired it
};

// Fixed-size FIFO; drops the oldest event if the consumer falls behind.
class EventQueue {
 public:
  void push(const GestureEvent& e);
  bool pop(GestureEvent* e);
  bool empty() const { return count_ == 0; }

 private:
  static constexpr int kSize = 8;
  GestureEvent q_[kSize];
  int head_ = 0;
  int count_ = 0;
};

float deadzone_map(float value, float deadzone, float full);
float cadence_to_stick(float cadence_hz, const float curve[3]);

class BeltGestures {
 public:
  enum JumpState : uint8_t { J_IDLE, J_DIP, J_SQUAT, J_BUSY };

  explicit BeltGestures(const BeltParams& p = BeltParams()) : p_(p) {}
  void set_params(const BeltParams& p) { p_ = p; }
  const BeltParams& params() const { return p_; }
  void reset();

  void update(const Vec3& accel_g, const Vec3& gyro_dps, uint32_t t_us);
  bool pop(GestureEvent* e) { return events_.pop(e); }

  float cadence_hz() const { return cadence_hz_; }
  float run_stick() const { return cadence_to_stick(running_ ? cadence_hz_ : 0.0f, p_.cadence_curve); }
  bool running() const { return running_; }
  bool sprinting() const { return running_ && cadence_hz_ >= p_.cadence_curve[2]; }
  float lean_deg() const { return lean_deg_; }  // + = leaning right
  float lean_stick() const { return deadzone_map(lean_deg_, p_.lean.deadzone_deg, p_.lean.full_deg); }
  float twist_deg() const { return yaw_deg_; }  // + = turned left
  float twist_stick() const {
    return -deadzone_map(yaw_deg_, p_.twist.deadzone_deg, p_.twist.full_deg) * p_.twist.max_rate;
  }
  JumpState jump_state() const { return jstate_; }
  float vertical_g() const { return a_lp_; }  // filtered, gravity removed

 private:
  void emit(uint8_t code, float conf, uint32_t t_us);
  float jerk(uint32_t t_us) const;
  void update_jump(uint32_t t_us, float j);
  void update_steps(uint32_t t_us);
  void update_orientation(const Vec3& a, const Vec3& g, float dt, uint32_t t_us);

  BeltParams p_;
  EventQueue events_;
  bool have_prev_ = false;
  uint32_t prev_t_us_ = 0;

  // Vertical accel, low-passed, with a short history for jerk.
  float a_lp_ = 0;
  static constexpr int kHist = 32;
  float hist_a_[kHist] = {};
  uint32_t hist_t_[kHist] = {};
  int hist_head_ = 0;
  int hist_n_ = 0;

  // Jump.
  JumpState jstate_ = J_IDLE;
  uint32_t dip_start_us_ = 0;
  float dip_min_g_ = 0;
  bool crouch_sent_ = false;
  uint32_t busy_since_us_ = 0;
  bool busy_flight_ = false;
  uint32_t busy_land_us_ = 0;

  // Steps.
  bool step_armed_ = true;
  bool have_step_ = false;
  uint32_t last_step_us_ = 0;
  float last_interval_s_ = 0;
  bool running_ = false;
  float cadence_hz_ = 0;

  // Orientation.
  float lean_deg_ = 0;
  float yaw_deg_ = 0;
  uint32_t still_since_us_ = 0;
  bool still_ = false;
};

}  // namespace bp

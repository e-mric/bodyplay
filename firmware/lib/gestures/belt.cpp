#include "belt.h"

#include <math.h>

#include "protocol.h"

namespace bp {

namespace {
constexpr float kRadToDeg = 57.29578f;
constexpr float kLowPassHz = 40.0f;      // vertical accel smoothing before jerk
constexpr uint32_t kJerkWindowUs = 5000;  // jerk measured over ~5 ms
constexpr uint32_t kSquatTimeoutUs = 3000000;
constexpr uint32_t kBusyTimeoutUs = 1500000;
constexpr uint32_t kLandQuietUs = 200000;
constexpr float kLeanTauS = 0.5f;

inline float clamp01(float v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
inline uint8_t conf_byte(float c) { return static_cast<uint8_t>(clamp01(c) * 255.0f + 0.5f); }
}  // namespace

void EventQueue::push(const GestureEvent& e) {
  if (count_ == kSize) {
    head_ = (head_ + 1) % kSize;
    --count_;
  }
  q_[(head_ + count_) % kSize] = e;
  ++count_;
}

bool EventQueue::pop(GestureEvent* e) {
  if (count_ == 0) return false;
  *e = q_[head_];
  head_ = (head_ + 1) % kSize;
  --count_;
  return true;
}

float deadzone_map(float value, float deadzone, float full) {
  float mag = (fabsf(value) - deadzone) / (full - deadzone);
  if (mag <= 0) return 0;
  if (mag > 1) mag = 1;
  return value < 0 ? -mag : mag;
}

float cadence_to_stick(float c, const float curve[3]) {
  if (c <= 0) return 0;
  if (c >= curve[1]) return 1;
  if (c <= curve[0]) return 0.5f * c / curve[0];
  return 0.5f + 0.5f * (c - curve[0]) / (curve[1] - curve[0]);
}

void BeltGestures::reset() {
  BeltParams p = p_;
  *this = BeltGestures(p);
}

void BeltGestures::emit(uint8_t code, float conf, uint32_t t_us) { events_.push({code, conf_byte(conf), t_us}); }

float BeltGestures::jerk(uint32_t t_us) const {
  // Compare with the newest sample at least kJerkWindowUs old.
  for (int k = 1; k < hist_n_; ++k) {
    int i = (hist_head_ - 1 - k + kHist) % kHist;
    uint32_t dt = t_us - hist_t_[i];
    if (dt >= kJerkWindowUs) return (a_lp_ - hist_a_[i]) / (dt * 1e-6f);
  }
  return 0;
}

void BeltGestures::update(const Vec3& a, const Vec3& g, uint32_t t_us) {
  float dt = have_prev_ ? (t_us - prev_t_us_) * 1e-6f : 0.0f;
  if (dt > 0.05f) dt = 0.05f;  // gap in the data: don't integrate across it
  prev_t_us_ = t_us;
  float vert = a.z - 1.0f;
  if (!have_prev_) {
    a_lp_ = vert;
    have_prev_ = true;
  } else {
    float rc = 1.0f / (2.0f * 3.14159265f * kLowPassHz);
    a_lp_ += (dt / (rc + dt)) * (vert - a_lp_);
  }
  float j = jerk(t_us);
  hist_a_[hist_head_] = a_lp_;
  hist_t_[hist_head_] = t_us;
  hist_head_ = (hist_head_ + 1) % kHist;
  if (hist_n_ < kHist) ++hist_n_;

  update_jump(t_us, j);
  update_steps(t_us);
  update_orientation(a, g, dt, t_us);
}

void BeltGestures::update_jump(uint32_t t, float j) {
  const JumpParams& jp = p_.jump;
  float a = a_lp_;
  switch (jstate_) {
    case J_IDLE:
      if (a < jp.dip_g && a > jp.freefall_g) {
        jstate_ = J_DIP;
        dip_start_us_ = t;
        dip_min_g_ = a;
        crouch_sent_ = false;
      }
      break;

    case J_DIP: {
      if (a <= jp.freefall_g) {  // flight phase of a running stride, not a dip
        jstate_ = J_IDLE;
        break;
      }
      if (a < dip_min_g_) dip_min_g_ = a;
      uint32_t dip_ms = (t - dip_start_us_) / 1000;
      if (a > jp.dip_g * 0.5f) {  // rising out of the dip
        uint32_t min_ms = running_ ? jp.running_dip_min_ms : jp.dip_min_ms;
        if (j > jp.push_jerk) {
          if (dip_ms >= min_ms) {
            float depth = dip_min_g_ / jp.dip_g;  // >= 1
            float conf = 0.5f + 0.25f * (depth - 1.0f) + 0.25f * (j / jp.push_jerk - 1.0f);
            if (conf < 0.5f) conf = 0.5f;
            emit(EV_JUMP, conf, t);
            jstate_ = J_BUSY;
            busy_since_us_ = t;
            busy_flight_ = false;
            busy_land_us_ = 0;
          } else {
            jstate_ = J_IDLE;  // too brief: a foot strike out of flight
          }
        } else {
          jstate_ = J_SQUAT;  // slow return: lowering into a squat, may crouch or jump from here
        }
        break;
      }
      if (!crouch_sent_ && dip_ms >= jp.crouch_hold_ms) {
        emit(EV_CROUCH, 0.7f, t);
        crouch_sent_ = true;
        jstate_ = J_SQUAT;
      }
      break;
    }

    case J_SQUAT:
      if (!crouch_sent_ && (t - dip_start_us_) / 1000 >= jp.crouch_hold_ms) {
        emit(EV_CROUCH, 0.7f, t);
        crouch_sent_ = true;
      }
      if (j > jp.push_jerk && a > -jp.dip_g * 0.5f) {
        emit(EV_JUMP, 0.7f, t);  // jump out of a squat
        jstate_ = J_BUSY;
        busy_since_us_ = t;
        busy_flight_ = false;
        busy_land_us_ = 0;
      } else if (t - dip_start_us_ > kSquatTimeoutUs) {
        jstate_ = J_IDLE;
      }
      break;

    case J_BUSY:
      // Wait out push-off, flight and landing so none of it counts as steps or a new jump.
      if (a <= jp.freefall_g) busy_flight_ = true;
      if (busy_flight_ && a > 0.5f && busy_land_us_ == 0) busy_land_us_ = t;
      if ((busy_land_us_ && t - busy_land_us_ > kLandQuietUs) || t - busy_since_us_ > kBusyTimeoutUs) {
        jstate_ = J_IDLE;
      }
      break;
  }
}

void BeltGestures::update_steps(uint32_t t) {
  const StepParams& sp = p_.step;
  if (have_step_ && running_) {
    float interval = last_interval_s_ > 0 ? last_interval_s_ : 1.0f / p_.cadence_curve[0];
    if ((t - last_step_us_) * 1e-6f > sp.stop_after_intervals * interval) {
      running_ = false;
      cadence_hz_ = 0;
      have_step_ = false;
      emit(EV_RUN_STOP, 1.0f, t);
    }
  }
  float a = a_lp_;
  bool jumping = jstate_ != J_IDLE;
  if (!step_armed_) {
    if (a < sp.peak_g * 0.5f) step_armed_ = true;
    return;
  }
  if (a <= sp.peak_g || jumping) return;
  step_armed_ = false;
  if (have_step_ && (t - last_step_us_) / 1000 < sp.refractory_ms) return;
  float conf = clamp01(0.6f + 0.4f * (a / sp.peak_g - 1.0f));
  if (!running_ || !have_step_) {
    running_ = true;
    cadence_hz_ = p_.cadence_curve[0];
    last_interval_s_ = 0;
    emit(EV_RUN_START, conf, t);
  } else {
    float dt = (t - last_step_us_) * 1e-6f;
    last_interval_s_ = dt;
    cadence_hz_ += 0.5f * (1.0f / dt - cadence_hz_);
  }
  have_step_ = true;
  last_step_us_ = t;
  emit(EV_STEP, conf, t);
}

void BeltGestures::update_orientation(const Vec3& a, const Vec3& g, float dt, uint32_t t) {
  // Lean: complementary filter on roll (gyro x integrates, gravity corrects slowly
  // and only when the body isn't accelerating hard).
  lean_deg_ += g.x * dt;
  float an = a.norm();
  if (an > 0.85f && an < 1.15f && dt > 0) {
    float roll_acc = atan2f(a.y, a.z) * kRadToDeg;
    float alpha = dt / (kLeanTauS + dt);
    lean_deg_ += alpha * (roll_acc - lean_deg_);
  }
  // Twist: integrate yaw rate; re-zero drift after standing still facing the TV for a while.
  yaw_deg_ += g.z * dt;
  bool still = g.norm() < 5.0f && fabsf(an - 1.0f) < 0.05f;
  if (still && !still_) still_since_us_ = t;
  still_ = still;
  // Only inside the deadzone: holding a twist to keep turning must not reset it.
  if (still && (t - still_since_us_) / 1000 >= p_.twist.rezero_still_ms &&
      fabsf(yaw_deg_) < p_.twist.deadzone_deg) {
    yaw_deg_ = 0;
  }
}

}  // namespace bp

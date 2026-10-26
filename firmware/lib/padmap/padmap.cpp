#include "padmap.h"

#include <math.h>

#include "protocol.h"

namespace bp {

// --- PressScheduler -----------------------------------------------------------------

void PressScheduler::press(uint32_t now_us) {
  if (!held_ && pending_ < max_queue_) ++pending_;
  tick(now_us);
}

void PressScheduler::hold(bool on, uint32_t now_us) {
  held_ = on;
  if (on) pending_ = 0;
  tick(now_us);
}

void PressScheduler::clear(uint32_t now_us) {
  pending_ = 0;
  held_ = false;
  tick(now_us);
}

bool PressScheduler::tick(uint32_t now_us) {
  uint32_t since = now_us - edge_us_;
  if (level_) {
    if (!held_ && since >= hold_us_) {
      level_ = false;
      edge_us_ = now_us;
    }
  } else if ((held_ || pending_) && (!started_ || since >= release_us_)) {
    if (!held_) --pending_;
    level_ = true;
    started_ = true;
    edge_us_ = now_us;
  }
  return level_;
}

// --- PadMapper ----------------------------------------------------------------------

namespace {
constexpr float kStickNeutral = 0.02f;
constexpr int kSlotL2 = kLastButton - kFirstButton + 1;
constexpr int kSlotR2 = kSlotL2 + 1;

uint8_t pulse_event(uint8_t pulse) {
  (void)pulse;  // step rhythm, stroke rhythm and fast feet all pulse on steps for now
  return EV_STEP;
}

float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }
}  // namespace

void PadMapper::set_profile(const ProfileBin& p) {
  profile_ = p;
  for (auto& s : sched_) s.configure(p.hold_ms * 1000u, p.release_ms * 1000u);
}

bool PadMapper::set_mode(uint8_t game_mode, uint32_t now_us) {
  if (!profile_.mode(game_mode)) return false;
  if (game_mode != mode_) {
    mode_ = game_mode;
    release_all(now_us);
  }
  return true;
}

void PadMapper::release_all(uint32_t now_us) {
  for (auto& s : sched_) s.clear(now_us);
  analog_[0] = analog_[1] = 0;
  stick_[0][0] = stick_[0][1] = stick_[1][0] = stick_[1][1] = 0;
}

int PadMapper::slot(uint8_t output) const {
  if (output == OUT_L2) return kSlotL2;
  if (output == OUT_R2) return kSlotR2;
  if (output >= kFirstButton && output <= kLastButton) return output - kFirstButton;
  return -1;
}

void PadMapper::drive_button(const Binding* b, uint8_t port, uint8_t output, uint8_t type, const BodyState& st,
                             const uint8_t* events, int n, uint32_t now) {
  int s = slot(output);
  if (s < 0) return;
  PressScheduler& sc = sched_[s];
  float* analog = output == OUT_L2 ? &analog_[0] : output == OUT_R2 ? &analog_[1] : nullptr;
  uint8_t kind = b ? b->kind : BIND_NEUTRAL;
  switch (kind) {
    case BIND_EVENT:
      sc.hold(false, now);
      if (analog) *analog = 0;
      for (int i = 0; i < n; ++i)
        if (events[i] == b->a && (b->cond == COND_NONE || st.condition(b->cond))) sc.press(now);
      break;
    case BIND_PULSE: {
      sc.hold(false, now);
      if (b->cond != COND_NONE && !st.condition(b->cond)) break;
      uint8_t ev = pulse_event(b->a);
      for (int i = 0; i < n; ++i) {
        if (events[i] != ev) continue;
        if (b->max_hz && pulsed_[port] && now - last_pulse_us_[port] < 1000000u / b->max_hz) continue;
        last_pulse_us_[port] = now;
        pulsed_[port] = true;
        sc.press(now);
      }
      break;
    }
    case BIND_HOLD:
      sc.hold(st.condition(b->a), now);
      break;
    case BIND_ANALOG:
      sc.hold(false, now);
      if (analog) *analog = clampf(st.source(b->a), 0, 1);
      break;
    default:
      sc.hold(false, now);
      if (analog) *analog = 0;
      break;
  }
  (void)type;
}

bool PadMapper::apply(const BodyState& st, const uint8_t* events, int n, uint32_t now, bool* time_critical) {
  for (uint8_t port = 0; port < kMaxPorts; ++port) {
    const PortDef& pd = profile_.ports[port];
    if (!pd.present) continue;
    const Binding* b = profile_.binding(mode_, port);
    if (pd.type == PORT_STICK) {
      int idx = pd.output == OUT_RIGHT_STICK ? 1 : 0;
      bool axes = b && b->kind == BIND_AXES;
      stick_[idx][0] = axes && b->a ? clampf(st.source(b->a), -1, 1) : 0;
      stick_[idx][1] = axes && b->b ? clampf(st.source(b->b), -1, 1) : 0;
    } else {
      drive_button(b, port, pd.output, pd.type, st, events, n, now);
    }
  }
  PadReport r;
  for (uint8_t out = kFirstButton; out <= kLastButton; ++out)
    if (sched_[out - kFirstButton].tick(now)) r.buttons |= 1u << (out - kFirstButton);
  r.l2 = sched_[kSlotL2].tick(now) ? 1.0f : analog_[0];
  r.r2 = sched_[kSlotR2].tick(now) ? 1.0f : analog_[1];
  r.lx = stick_[0][0];
  r.ly = stick_[0][1];
  r.rx = stick_[1][0];
  r.ry = stick_[1][1];
  if (r == report_) {
    if (time_critical) *time_critical = false;
    return false;
  }
  if (time_critical) {
    bool crit = (r.buttons & ~report_.buttons) != 0;
    crit |= (r.l2 > 0.5f && report_.l2 <= 0.5f) || (r.r2 > 0.5f && report_.r2 <= 0.5f);
    float old_l = fmaxf(fabsf(report_.lx), fabsf(report_.ly)), new_l = fmaxf(fabsf(r.lx), fabsf(r.ly));
    float old_r = fmaxf(fabsf(report_.rx), fabsf(report_.ry)), new_r = fmaxf(fabsf(r.rx), fabsf(r.ry));
    crit |= (old_l <= kStickNeutral && new_l > kStickNeutral) || (old_r <= kStickNeutral && new_r > kStickNeutral);
    *time_critical = crit;
  }
  report_ = r;
  return true;
}

}  // namespace bp

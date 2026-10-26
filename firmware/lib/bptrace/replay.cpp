#include "replay.h"

namespace bp {

std::vector<uint32_t> ReplayResult::times(uint8_t code) const {
  std::vector<uint32_t> out;
  for (const auto& e : events)
    if (e.code == code) out.push_back(e.t_us);
  return out;
}

ReplayResult replay_belt(const Trace& trace, const ReplayOptions& opt, const SampleHook& hook) {
  ReplayResult r;
  MountCalibration cal(opt.forward, opt.calibration_us);
  BeltGestures g(opt.params);
  bool started = false;
  for (const auto& s : trace.samples) {
    if (s.node_id != opt.node_id) continue;
    if (!started) {
      cal.start(s.t_us);
      started = true;
    }
    if (!cal.done()) {
      if (cal.feed(s.accel_g, s.gyro_dps, s.t_us)) {
        r.calibrated = true;
        r.calibrated_at_us = s.t_us;
      }
      continue;
    }
    g.update(cal.to_body(s.accel_g), cal.gyro_to_body(s.gyro_dps), s.t_us);
    GestureEvent e;
    while (g.pop(&e)) r.events.push_back(e);
    if (hook) hook(g, s.t_us);
  }
  return r;
}

}  // namespace bp

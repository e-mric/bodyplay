// Replays a trace through the belt gestures exactly as the node runs them:
// standing calibration first, then body-frame samples into BeltGestures.
#pragma once

#include <functional>
#include <vector>

#include "belt.h"
#include "bptrace.h"
#include "mount.h"

namespace bp {

struct ReplayOptions {
  BeltParams params;
  MountAxis forward = AXIS_POS_X;
  uint32_t calibration_us = 800000;  // synthetic traces start with 1-1.5 s of standing
  uint8_t node_id = 1;               // NODE_BELT
};

struct ReplayResult {
  bool calibrated = false;
  uint32_t calibrated_at_us = 0;
  std::vector<GestureEvent> events;

  std::vector<uint32_t> times(uint8_t code) const;
};

using SampleHook = std::function<void(const BeltGestures&, uint32_t t_us)>;

ReplayResult replay_belt(const Trace& trace, const ReplayOptions& opt, const SampleHook& hook = nullptr);

}  // namespace bp

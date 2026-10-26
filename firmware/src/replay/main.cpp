// Host tool: replay a .bptrace through the belt gestures, print events as JSON lines.
//   pio run -e replay && .pio/build/replay/program trace.bptrace [--forward +x] [--calib-ms 800]
//       [--set jump.dip_g=-0.35 ...]
// Wrapped by `uv run bodyctl replay`, which also scores events against labels.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>

#include "bptrace.h"
#include "param_table.h"
#include "protocol.h"
#include "replay.h"

using namespace bp;

static const char* event_name(uint8_t code) {
  switch (code) {
    case EV_JUMP: return "jump";
    case EV_CROUCH: return "crouch";
    case EV_STEP: return "step";
    case EV_RUN_START: return "run_start";
    case EV_RUN_STOP: return "run_stop";
    default: return "other";
  }
}

static bool parse_axis(const char* s, MountAxis* out) {
  static const char* names[] = {"+x", "-x", "+y", "-y", "+z", "-z"};
  for (int i = 0; i < 6; ++i) {
    if (strcmp(s, names[i]) == 0) {
      *out = static_cast<MountAxis>(i);
      return true;
    }
  }
  return false;
}

int main(int argc, char** argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: %s trace.bptrace [--forward +x] [--calib-ms 800] [--set name=value ...]\n", argv[0]);
    return 2;
  }
  ReplayOptions opt;
  const char* path = nullptr;
  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], "--forward") == 0 && i + 1 < argc) {
      if (!parse_axis(argv[++i], &opt.forward)) {
        fprintf(stderr, "bad --forward %s\n", argv[i]);
        return 2;
      }
    } else if (strcmp(argv[i], "--calib-ms") == 0 && i + 1 < argc) {
      opt.calibration_us = static_cast<uint32_t>(atoi(argv[++i])) * 1000;
    } else if (strcmp(argv[i], "--set") == 0 && i + 1 < argc) {
      std::string kv = argv[++i];
      size_t eq = kv.find('=');
      if (eq == std::string::npos || !belt_param_set(&opt.params, kv.substr(0, eq).c_str(),
                                                     static_cast<float>(atof(kv.c_str() + eq + 1)))) {
        fprintf(stderr, "unknown parameter %s\n", kv.c_str());
        return 2;
      }
    } else {
      path = argv[i];
    }
  }
  Trace trace;
  std::string err;
  if (!path || !trace.load(path, &err)) {
    fprintf(stderr, "%s\n", err.c_str());
    return 1;
  }
  ReplayResult r = replay_belt(trace, opt);
  if (!r.calibrated) {
    fprintf(stderr, "never calibrated: the trace needs %u ms of standing still at the start\n",
            opt.calibration_us / 1000);
    return 1;
  }
  printf("{\"calibrated_at_us\": %u}\n", r.calibrated_at_us);
  for (const auto& e : r.events) {
    printf("{\"t_us\": %u, \"event\": \"%s\", \"confidence\": %.3f}\n", e.t_us, event_name(e.code),
           e.confidence / 255.0);
  }
  return 0;
}

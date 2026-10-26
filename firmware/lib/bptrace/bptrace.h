// Host-side .bptrace reader (format: pc/bodyctl/trace.py) for native tests and
// the replay tool. Uses the C/C++ standard library; never built into firmware.
#pragma once

#include <stdint.h>

#include <string>
#include <vector>

#include "vec3.h"

namespace bp {

struct TraceSample {
  uint8_t node_id;
  uint32_t t_us;
  Vec3 accel_g;   // sensor frame
  Vec3 gyro_dps;  // sensor frame
  int16_t emg;
};

struct TraceLabel {
  std::string event;
  uint32_t t_us;
};

struct Trace {
  std::string header_json;
  float accel_lsb_g = 0.000244f;
  float gyro_lsb_dps = 0.070f;
  std::vector<TraceSample> samples;  // RAW samples, in file order
  std::vector<TraceLabel> labels;

  // Loads a trace; returns false and sets error on failure.
  bool load(const std::string& path, std::string* error);
  std::vector<uint32_t> label_times(const std::string& event) const;
};

// Pulls a number out of flat JSON text ("key": 1.5). Returns fallback if absent.
double json_number(const std::string& json, const std::string& key, double fallback);
// Pulls a string value ("key": "text"). Returns empty if absent.
std::string json_string(const std::string& json, const std::string& key);

}  // namespace bp

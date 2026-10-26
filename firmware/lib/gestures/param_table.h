// Gesture parameters by id and name. The profile compiler (pc/bodyctl/config/
// compile.py) emits (id, value) pairs from the YAML; the replay tool accepts
// name=value. Ids are part of the profile binary format: append, never renumber.
#pragma once

#include <stdint.h>

#include "params.h"

namespace bp {

struct ParamInfo {
  uint8_t id;
  const char* name;  // matches the YAML path under `gestures:`
};

// All known belt parameters, in id order.
const ParamInfo* belt_param_table(int* count);

// Set by id or name. Return false if unknown.
bool belt_param_set(BeltParams* p, uint8_t id, float value);
bool belt_param_set(BeltParams* p, const char* name, float value);
bool belt_param_get(const BeltParams& p, uint8_t id, float* value);

}  // namespace bp

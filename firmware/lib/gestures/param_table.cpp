#include "param_table.h"

#include <string.h>

namespace bp {

namespace {

const ParamInfo kTable[] = {
    {1, "step.peak_g"},
    {2, "step.refractory_ms"},
    {3, "step.stop_after_intervals"},
    {4, "cadence_curve.0"},
    {5, "cadence_curve.1"},
    {6, "cadence_curve.2"},
    {7, "jump.dip_g"},
    {8, "jump.dip_min_ms"},
    {9, "jump.push_jerk"},
    {10, "jump.crouch_hold_ms"},
    {11, "jump.min_conf"},
    {12, "jump.freefall_g"},
    {13, "jump.running_dip_min_ms"},
    {14, "lean.deadzone_deg"},
    {15, "lean.full_deg"},
    {16, "twist.deadzone_deg"},
    {17, "twist.full_deg"},
    {18, "twist.max_rate"},
    {19, "twist.rezero_still_ms"},
};

float* float_slot(BeltParams* p, uint8_t id) {
  switch (id) {
    case 1: return &p->step.peak_g;
    case 3: return &p->step.stop_after_intervals;
    case 4: return &p->cadence_curve[0];
    case 5: return &p->cadence_curve[1];
    case 6: return &p->cadence_curve[2];
    case 7: return &p->jump.dip_g;
    case 9: return &p->jump.push_jerk;
    case 11: return &p->jump.min_conf;
    case 12: return &p->jump.freefall_g;
    case 14: return &p->lean.deadzone_deg;
    case 15: return &p->lean.full_deg;
    case 16: return &p->twist.deadzone_deg;
    case 17: return &p->twist.full_deg;
    case 18: return &p->twist.max_rate;
    default: return nullptr;
  }
}

uint32_t* u32_slot(BeltParams* p, uint8_t id) {
  switch (id) {
    case 2: return &p->step.refractory_ms;
    case 8: return &p->jump.dip_min_ms;
    case 10: return &p->jump.crouch_hold_ms;
    case 13: return &p->jump.running_dip_min_ms;
    case 19: return &p->twist.rezero_still_ms;
    default: return nullptr;
  }
}

}  // namespace

const ParamInfo* belt_param_table(int* count) {
  *count = static_cast<int>(sizeof(kTable) / sizeof(kTable[0]));
  return kTable;
}

bool belt_param_set(BeltParams* p, uint8_t id, float value) {
  if (float* f = float_slot(p, id)) {
    *f = value;
    return true;
  }
  if (uint32_t* u = u32_slot(p, id)) {
    *u = value < 0 ? 0 : static_cast<uint32_t>(value + 0.5f);
    return true;
  }
  return false;
}

bool belt_param_set(BeltParams* p, const char* name, float value) {
  for (const auto& e : kTable)
    if (strcmp(e.name, name) == 0) return belt_param_set(p, e.id, value);
  return false;
}

bool belt_param_get(const BeltParams& p, uint8_t id, float* value) {
  BeltParams* mp = const_cast<BeltParams*>(&p);
  if (float* f = float_slot(mp, id)) {
    *value = *f;
    return true;
  }
  if (uint32_t* u = u32_slot(mp, id)) {
    *value = static_cast<float>(*u);
    return true;
  }
  return false;
}

}  // namespace bp

// PadReport -> the Arduino-ESP32 USBHIDGamepad report (Tier 1 node / dongle).
//
// Button bits follow the Linux gamepad convention the USB library uses, so the
// SDL mapping in pc/bodyctl/output/sdl_mapping.py lines up:
//   cross b0, circle b1, touchpad b2, triangle b3, square b4, L1 b6, R1 b7,
//   L2 b8, R2 b9, create b10, options b11, PS b12, L3 b13, R3 b14; d-pad = hat.
// Axes: X/Y left stick, Z/Rz right stick, Rx/Ry = L2/R2 (0..127, released 0).
#pragma once

#include <stdint.h>

#include "padmap.h"

namespace bp {

struct __attribute__((packed)) HidGamepad {
  int8_t x = 0, y = 0, z = 0, rz = 0, rx = 0, ry = 0;
  uint8_t hat = 0;  // 0 center, 1 up, 2 up-right ... 8 up-left
  uint32_t buttons = 0;
};
static_assert(sizeof(HidGamepad) == 11, "ST_PAD payload layout (pc/bodyctl/output/device.py)");

inline int8_t hid_axis(float v) {
  if (v > 1) v = 1;
  if (v < -1) v = -1;
  return static_cast<int8_t>(v * 127.0f + (v >= 0 ? 0.5f : -0.5f));
}

inline HidGamepad to_hid(const PadReport& r) {
  static const struct {
    uint8_t output;
    uint8_t bit;
  } kButtons[] = {
      {OUT_CROSS, 0},    {OUT_CIRCLE, 1},  {OUT_TOUCHPAD, 2}, {OUT_TRIANGLE, 3}, {OUT_SQUARE, 4},
      {OUT_L1, 6},       {OUT_R1, 7},      {OUT_CREATE, 10},  {OUT_OPTIONS, 11}, {OUT_PS, 12},
      {OUT_L3, 13},      {OUT_R3, 14},
  };
  HidGamepad h;
  for (const auto& b : kButtons)
    if (r.button(b.output)) h.buttons |= 1u << b.bit;
  if (r.l2 > 0.5f) h.buttons |= 1u << 8;
  if (r.r2 > 0.5f) h.buttons |= 1u << 9;
  h.x = hid_axis(r.lx);
  h.y = hid_axis(-r.ly);  // HID y grows downward; our y is forward/up
  h.z = hid_axis(r.rx);
  h.rz = hid_axis(-r.ry);
  h.rx = hid_axis(r.l2);
  h.ry = hid_axis(r.r2);
  bool up = r.button(OUT_DPAD_UP), down = r.button(OUT_DPAD_DOWN);
  bool left = r.button(OUT_DPAD_LEFT), right = r.button(OUT_DPAD_RIGHT);
  if (up && right) h.hat = 2;
  else if (down && right) h.hat = 4;
  else if (down && left) h.hat = 6;
  else if (up && left) h.hat = 8;
  else if (up) h.hat = 1;
  else if (right) h.hat = 3;
  else if (down) h.hat = 5;
  else if (left) h.hat = 7;
  return h;
}

}  // namespace bp

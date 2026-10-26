#include "dac.h"

namespace bp {

uint16_t stick_code(float value, const StickAxisCal& cal) {
  if (value != value) value = 0;  // NaN -> neutral
  if (value > 1) value = 1;
  if (value < -1) value = -1;
  if (cal.invert) value = -value;
  int32_t mv = static_cast<int32_t>(cal.neutral_mv) + static_cast<int32_t>(value * cal.span_mv);
  return code_for_mv(mv);
}

uint16_t trigger_code(float value, const TriggerCal& cal) {
  if (value != value) value = 0;
  if (value > 1) value = 1;
  if (value < 0) value = 0;
  int32_t mv = static_cast<int32_t>(cal.released_mv) +
               static_cast<int32_t>(value * (static_cast<int32_t>(cal.full_mv) - cal.released_mv));
  return code_for_mv(mv);
}

// Multi-write (datasheet 5.6.2): 0b01000 DAC1 DAC0 UDAC | VREF PD1 PD0 GX D11..D8 | D7..D0.
// VREF = 1 (internal 2.048 V), PD = 00 (on), GX = 0 (gain 1), UDAC = 0 (update now).
// We never use "fast write": it is referenced to VDD (3.3 V), where code 3499 would be 2.8 V.
size_t mcp4728_channel_write(uint8_t channel, uint16_t code, uint8_t* out, size_t cap) {
  if (cap < 3 || channel > 3) return 0;
  code = clamp_code(code);
  out[0] = static_cast<uint8_t>(0x40 | (channel << 1));
  out[1] = static_cast<uint8_t>(0x80 | ((code >> 8) & 0x0F));
  out[2] = static_cast<uint8_t>(code & 0xFF);
  return 3;
}

size_t mcp4728_write_all(const uint16_t codes[4], uint8_t* out, size_t cap) {
  if (cap < 12) return 0;
  for (uint8_t ch = 0; ch < 4; ++ch) mcp4728_channel_write(ch, codes[ch], out + 3 * ch, 3);
  return 12;
}

}  // namespace bp

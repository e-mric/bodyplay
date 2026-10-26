// Hard rule 1: no DAC code, from any input, may put more than 1.75 V on a port.
#include <unity.h>

#include <math.h>

#include <initializer_list>

#include "dac.h"

using namespace bp;

void setUp() {}
void tearDown() {}

static uint16_t decoded(const uint8_t* b) { return static_cast<uint16_t>(((b[1] & 0x0F) << 8) | b[2]); }

void test_clamp_constant() {
  TEST_ASSERT_EQUAL_UINT16(3499, kMaxCode);
  TEST_ASSERT_TRUE(mv_for_code(kMaxCode) <= 1750);
  TEST_ASSERT_TRUE(mv_for_code(kMaxCode) < 1800);
}

void test_clamp_every_int16_and_beyond() {
  for (int32_t c = -70000; c <= 70000; ++c) TEST_ASSERT_TRUE(clamp_code(c) <= kMaxCode);
  TEST_ASSERT_EQUAL_UINT16(kMaxCode, code_for_mv(3300));
  TEST_ASSERT_EQUAL_UINT16(0, code_for_mv(-500));
  TEST_ASSERT_UINT16_WITHIN(1, 1800, code_for_mv(900));
}

void test_encoders_clamp_raw_codes() {
  uint8_t b[12];
  for (uint32_t code = 0; code <= 0xFFFF; ++code) {
    TEST_ASSERT_EQUAL(3, mcp4728_channel_write(code % 4, static_cast<uint16_t>(code), b, 3));
    TEST_ASSERT_TRUE(decoded(b) <= kMaxCode);
    TEST_ASSERT_EQUAL_HEX8(0x80, b[1] & 0xF0);  // internal Vref, powered, gain 1
  }
  uint16_t codes[4] = {0, 4095, 65535, 1800};
  TEST_ASSERT_EQUAL(12, mcp4728_write_all(codes, b, sizeof(b)));
  for (int ch = 0; ch < 4; ++ch) {
    TEST_ASSERT_EQUAL_HEX8(0x40 | (ch << 1), b[3 * ch]);
    TEST_ASSERT_TRUE(decoded(b + 3 * ch) <= kMaxCode);
  }
  TEST_ASSERT_EQUAL(0, mcp4728_channel_write(4, 100, b, 3));
}

void test_stick_and_trigger_never_exceed_clamp_whatever_the_calibration() {
  const float values[] = {-1e9f, -2, -1, -0.5f, 0, 0.5f, 1, 2, 1e9f, NAN, INFINITY, -INFINITY};
  for (uint16_t neutral = 0; neutral <= 3000; neutral += 150) {
    for (uint16_t span = 0; span <= 3000; span += 250) {
      for (bool inv : {false, true}) {
        StickAxisCal cal{neutral, span, inv};
        for (float v : values) TEST_ASSERT_TRUE(stick_code(v, cal) <= kMaxCode);
      }
      TriggerCal t{neutral, span};
      for (float v : values) TEST_ASSERT_TRUE(trigger_code(v, t) <= kMaxCode);
    }
  }
}

void test_stick_mapping() {
  StickAxisCal cal;
  TEST_ASSERT_UINT16_WITHIN(1, code_for_mv(900), stick_code(0, cal));
  TEST_ASSERT_UINT16_WITHIN(1, code_for_mv(1750), stick_code(1, cal));
  TEST_ASSERT_UINT16_WITHIN(1, code_for_mv(50), stick_code(-1, cal));
  cal.invert = true;
  TEST_ASSERT_UINT16_WITHIN(1, code_for_mv(50), stick_code(1, cal));
  TEST_ASSERT_EQUAL_UINT16(code_for_mv(900), stick_code(NAN, StickAxisCal()));
  TriggerCal t;
  TEST_ASSERT_EQUAL_UINT16(0, trigger_code(0, t));
  TEST_ASSERT_EQUAL_UINT16(kMaxCode, trigger_code(1, t));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_clamp_constant);
  RUN_TEST(test_clamp_every_int16_and_beyond);
  RUN_TEST(test_encoders_clamp_raw_codes);
  RUN_TEST(test_stick_and_trigger_never_exceed_clamp_whatever_the_calibration);
  RUN_TEST(test_stick_mapping);
  return UNITY_END();
}

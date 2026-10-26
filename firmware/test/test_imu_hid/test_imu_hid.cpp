#include <unity.h>

#include "hid_report.h"
#include "lsm6dsox.h"

using namespace bp;
using namespace bp::lsm6dsox;

void setUp() {}
void tearDown() {}

static void word(uint8_t* w, uint8_t tag, int16_t x, int16_t y, int16_t z) {
  w[0] = static_cast<uint8_t>(tag << 3 | 0x02);  // tag counter bits set: must be ignored
  int16_t v[3] = {x, y, z};
  for (int i = 0; i < 3; ++i) {
    w[1 + 2 * i] = static_cast<uint8_t>(v[i] & 0xFF);
    w[2 + 2 * i] = static_cast<uint8_t>((v[i] >> 8) & 0xFF);
  }
}

void test_fifo_pairs_gyro_and_accel() {
  FifoParser p;
  uint8_t w[7];
  RawImu s;
  word(w, TAG_GYRO, 100, -200, 300);
  TEST_ASSERT_FALSE(p.feed(w, &s));
  word(w, TAG_ACCEL, 4096, -1, -32768);
  TEST_ASSERT_TRUE(p.feed(w, &s));
  TEST_ASSERT_EQUAL_INT16(4096, s.accel[0]);
  TEST_ASSERT_EQUAL_INT16(-32768, s.accel[2]);
  TEST_ASSERT_EQUAL_INT16(-200, s.gyro[1]);
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 0.99942f, accel_g(s).x);
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 21.0f, gyro_dps(s).z);
  word(w, 0x03, 1, 2, 3);  // timestamp word: skipped
  TEST_ASSERT_FALSE(p.feed(w, &s));
  TEST_ASSERT_EQUAL_UINT32(1, p.unknown_tags());
}

void test_fifo_status_and_config() {
  bool ovr = false;
  TEST_ASSERT_EQUAL_UINT16(0x123, fifo_words(0x23, 0x41, &ovr));
  TEST_ASSERT_TRUE(ovr);
  // ODR 1666 Hz (0b1000) in the top nibble, +-8 g (0b11) and +-2000 dps (0b11) in FS bits.
  TEST_ASSERT_EQUAL_HEX8(0x8C, kConfig[1].value);
  TEST_ASSERT_EQUAL_HEX8(0x8C, kConfig[2].value);
}

void test_hid_report_mapping() {
  PadReport r;
  r.buttons |= 1u << (OUT_SQUARE - kFirstButton);
  r.buttons |= 1u << (OUT_CROSS - kFirstButton);
  r.buttons |= 1u << (OUT_DPAD_UP - kFirstButton);
  r.buttons |= 1u << (OUT_DPAD_RIGHT - kFirstButton);
  r.ly = 1.0f;
  r.lx = -0.5f;
  r.r2 = 1.0f;
  HidGamepad h = to_hid(r);
  TEST_ASSERT_EQUAL_HEX32((1u << 0) | (1u << 4) | (1u << 9), h.buttons);
  TEST_ASSERT_EQUAL_INT8(-127, h.y);  // forward = HID up
  TEST_ASSERT_EQUAL_INT8(-64, h.x);
  TEST_ASSERT_EQUAL_INT8(127, h.ry);
  TEST_ASSERT_EQUAL_INT8(0, h.rx);  // released trigger rests at 0
  TEST_ASSERT_EQUAL_UINT8(2, h.hat);
  TEST_ASSERT_EQUAL_INT8(127, hid_axis(3.0f));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_fifo_pairs_gyro_and_accel);
  RUN_TEST(test_fifo_status_and_config);
  RUN_TEST(test_hid_report_mapping);
  return UNITY_END();
}

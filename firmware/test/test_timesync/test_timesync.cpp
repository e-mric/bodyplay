#include <unity.h>

#include "timesync.h"

using namespace bp;

void setUp() {}
void tearDown() {}

// Hub clock runs 5 s ahead of the node; link delay varies 1-3 ms.
void test_picks_least_delayed_sample() {
  ClockSync cs;
  const int32_t true_offset = 5000000;
  const uint32_t delays[] = {3000, 1800, 2500, 1000, 2900, 1500};
  uint32_t local = 1000;
  for (uint32_t d : delays) {
    uint32_t hub_send = local + true_offset;
    cs.on_sync(hub_send, local + d);
    local += 100000;
  }
  TEST_ASSERT_TRUE(cs.valid());
  TEST_ASSERT_EQUAL_INT32(true_offset - 1000, cs.offset_us());
  cs.on_rtt(2100);
  cs.on_rtt(2000);
  TEST_ASSERT_EQUAL_INT32(true_offset, cs.offset_us());  // within +-0.5 ms target, here exact
}

void test_window_forgets_old_samples() {
  ClockSync cs(2000000);
  cs.on_sync(10000 + 900, 10000);  // unusually good (negative delay = clock jump) sample
  uint32_t local = 10000;
  for (int i = 0; i < 40; ++i) {  // 4 s of SYNC at 100 ms
    local += 100000;
    cs.on_sync(local + 500, local);
  }
  TEST_ASSERT_EQUAL_INT32(500, cs.offset_us());
}

void test_handles_clock_wrap() {
  ClockSync cs;
  uint32_t local = 0xFFFFFF00u;
  cs.on_sync(local + 250, local);
  local += 0x200;  // wraps past zero
  cs.on_sync(local + 200, local);
  TEST_ASSERT_EQUAL_INT32(250, cs.offset_us());
  TEST_ASSERT_EQUAL_UINT32(local + 250, cs.to_remote(local));
  TEST_ASSERT_EQUAL_UINT32(local, cs.to_local(local + 250));
}

void test_invalid_until_first_sync() {
  ClockSync cs;
  TEST_ASSERT_FALSE(cs.valid());
  TEST_ASSERT_EQUAL_INT32(0, cs.offset_us());
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_picks_least_delayed_sample);
  RUN_TEST(test_window_forgets_old_samples);
  RUN_TEST(test_handles_clock_wrap);
  RUN_TEST(test_invalid_until_first_sync);
  return UNITY_END();
}

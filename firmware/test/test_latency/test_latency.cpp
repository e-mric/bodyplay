#include <unity.h>

#include <string.h>

#include "latency.h"

using namespace bp;

void setUp() {}
void tearDown() {}

void test_percentiles() {
  LatencyHistogram h;
  for (uint32_t i = 1; i <= 100; ++i) h.add(i * 100);  // 0.1 .. 10 ms
  TEST_ASSERT_EQUAL_UINT32(100, h.count());
  TEST_ASSERT_EQUAL_UINT32(100, h.min_us());
  TEST_ASSERT_EQUAL_UINT32(10000, h.max_us());
  // Bucket upper edges: sample i*100 lands in bucket i, edge (i+1)*100 (capped at max).
  TEST_ASSERT_UINT32_WITHIN(100, 5000, h.percentile_us(50));
  TEST_ASSERT_UINT32_WITHIN(100, 9900, h.percentile_us(99));
  TEST_ASSERT_EQUAL_UINT32(10000, h.percentile_us(100));
}

void test_overflow_counts_as_max() {
  LatencyHistogram h;
  h.add(1000);
  h.add(80000);  // 80 ms: beyond the 50 ms range
  TEST_ASSERT_EQUAL_UINT32(1, h.overflow());
  TEST_ASSERT_EQUAL_UINT32(80000, h.percentile_us(99));
  TEST_ASSERT_EQUAL_UINT32(1100, h.percentile_us(50));
}

void test_empty_and_reset() {
  LatencyHistogram h;
  TEST_ASSERT_EQUAL_UINT32(0, h.percentile_us(50));
  h.add(500);
  h.reset();
  TEST_ASSERT_EQUAL_UINT32(0, h.count());
  TEST_ASSERT_EQUAL_UINT32(0, h.min_us());
}

void test_format_lists_only_used_stages() {
  LatencyStats s;
  s.add(STAGE_DETECT_TO_SEND, 300);
  s.add(STAGE_DETECT_TO_SEND, 800);
  s.add(STAGE_COUNT, 5);  // ignored
  char buf[256];
  size_t n = s.format(buf, sizeof(buf));
  TEST_ASSERT_EQUAL(strlen(buf), n);
  TEST_ASSERT_NOT_NULL(strstr(buf, "detect->send n=2"));
  TEST_ASSERT_NULL(strstr(buf, "sample->detect"));
  char tiny[10];
  TEST_ASSERT_EQUAL(9, s.format(tiny, sizeof(tiny)));  // truncates safely
}

void test_marker_pulse() {
  MarkerPulse m;
  TEST_ASSERT_FALSE(m.update(0));
  m.trigger(1000);
  TEST_ASSERT_TRUE(m.update(1000));
  TEST_ASSERT_TRUE(m.update(20999));
  TEST_ASSERT_FALSE(m.update(21000));
  m.trigger(0xFFFFF000u);  // wraps
  TEST_ASSERT_TRUE(m.update(0x00001000u));
  TEST_ASSERT_FALSE(m.update(0x00004000u + MarkerPulse::kOnUs));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_percentiles);
  RUN_TEST(test_overflow_counts_as_max);
  RUN_TEST(test_empty_and_reset);
  RUN_TEST(test_format_lists_only_used_stages);
  RUN_TEST(test_marker_pulse);
  return UNITY_END();
}

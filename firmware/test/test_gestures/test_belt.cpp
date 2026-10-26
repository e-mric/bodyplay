// Belt gestures replayed on the synthetic fixtures (firmware/test/fixtures,
// regenerated with `uv run bodyctl synth --fixtures`). Real recorded fixtures
// join these in M3; the synthetic ones stay as regression tests.
#include <unity.h>

#include <math.h>
#include <stdio.h>

#include <string>

#include "belt.h"
#include "bptrace.h"
#include "mount.h"
#include "protocol.h"
#include "replay.h"

using namespace bp;

static Trace load(const char* name) {
  // pio test runs from the firmware/ folder; fall back to the repo root.
  Trace t;
  std::string err;
  if (t.load(std::string("test/fixtures/") + name, &err)) return t;
  Trace t2;
  if (t2.load(std::string("firmware/test/fixtures/") + name, &err)) return t2;
  TEST_FAIL_MESSAGE(err.c_str());
  return t;
}

static ReplayResult replay(const char* name) {
  Trace t = load(name);
  ReplayOptions opt;
  ReplayResult r = replay_belt(t, opt);
  TEST_ASSERT_TRUE_MESSAGE(r.calibrated, name);
  return r;
}

static int count(const ReplayResult& r, uint8_t code) { return static_cast<int>(r.times(code).size()); }

// Every label must be matched by one event within [label + lo_ms, label + hi_ms].
static void expect_near(const std::vector<uint32_t>& labels, const std::vector<uint32_t>& fired, int lo_ms,
                        int hi_ms, const char* what) {
  char msg[160];
  snprintf(msg, sizeof(msg), "%s: %zu labels vs %zu events", what, labels.size(), fired.size());
  TEST_ASSERT_EQUAL_MESSAGE(labels.size(), fired.size(), msg);
  for (size_t i = 0; i < labels.size(); ++i) {
    long d_ms = (static_cast<long>(fired[i]) - static_cast<long>(labels[i])) / 1000;
    snprintf(msg, sizeof(msg), "%s #%zu fired %ld ms from its label", what, i, d_ms);
    TEST_ASSERT_TRUE_MESSAGE(d_ms >= lo_ms && d_ms <= hi_ms, msg);
  }
}

void setUp() {}
void tearDown() {}

void test_deadzone_and_cadence_maps() {
  TEST_ASSERT_EQUAL_FLOAT(0, deadzone_map(5, 6, 25));
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 0.5f, deadzone_map(15.5f, 6, 25));
  TEST_ASSERT_EQUAL_FLOAT(-1, deadzone_map(-40, 6, 25));
  const float curve[3] = {1.5f, 2.5f, 3.5f};
  TEST_ASSERT_EQUAL_FLOAT(0, cadence_to_stick(0, curve));
  TEST_ASSERT_EQUAL_FLOAT(0.5f, cadence_to_stick(1.5f, curve));
  TEST_ASSERT_EQUAL_FLOAT(0.75f, cadence_to_stick(2.0f, curve));
  TEST_ASSERT_EQUAL_FLOAT(1, cadence_to_stick(3.0f, curve));
}

void test_mount_calibration_levels_a_tilted_sensor() {
  MountCalibration cal(AXIS_POS_X, 100000);
  cal.start(0);
  // Sensor tilted 20 degrees about y: gravity leaks into x.
  float th = 20.0f * 3.14159265f / 180.0f;
  Vec3 g(sinf(th), 0, cosf(th));
  bool done = false;
  for (uint32_t t = 0; t <= 200000 && !done; t += 600) done = cal.feed(g, Vec3(0.5f, -0.2f, 0.1f), t);
  TEST_ASSERT_TRUE(done);
  Vec3 b = cal.to_body(g);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 0, b.x);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 0, b.y);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 1, b.z);
  Vec3 gb = cal.gyro_to_body(Vec3(0.5f, -0.2f, 0.1f));
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 0, gb.norm());  // bias removed
  // The mount's forward axis stays forward after levelling.
  Vec3 fwd = cal.to_body(Vec3(cosf(th), 0, -sinf(th)));
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 1, fwd.x);
}

void test_calibration_restarts_on_movement() {
  MountCalibration cal(AXIS_POS_X, 100000);
  cal.start(0);
  bool done = false;
  for (uint32_t t = 0; t < 90000; t += 600) done = cal.feed(Vec3(0, 0, 1), Vec3(), t);
  cal.feed(Vec3(0, 0, 1.6f), Vec3(), 90000);  // a bump
  for (uint32_t t = 90600; t < 150000; t += 600) done = cal.feed(Vec3(0, 0, 1), Vec3(), t);
  TEST_ASSERT_FALSE(done);
}

void test_still_is_silent() {
  ReplayResult r = replay("synth_still.bptrace");
  TEST_ASSERT_EQUAL(0, r.events.size());
}

void test_weight_shift_fires_nothing() {
  for (const char* f : {"synth_weight_shift.bptrace", "synth_weight_shift_noisy0.bptrace"}) {
    ReplayResult r = replay(f);
    TEST_ASSERT_EQUAL_MESSAGE(0, r.events.size(), f);
  }
}

void test_weight_shift_lean_tracks_roll() {
  Trace t = load("synth_weight_shift.bptrace");
  float max_lean = 0, max_stick = 0;
  replay_belt(t, ReplayOptions(), [&](const BeltGestures& g, uint32_t) {
    max_lean = fmaxf(max_lean, fabsf(g.lean_deg()));
    max_stick = fmaxf(max_stick, fabsf(g.lean_stick()));
  });
  TEST_ASSERT_FLOAT_WITHIN(1.5f, 5.0f, max_lean);  // +-5 degree sway
  TEST_ASSERT_TRUE(max_stick < 0.05f);             // inside the 6 degree deadzone
}

void test_jump_fires_on_the_dip_reversal() {
  for (const char* f : {"synth_jump.bptrace", "synth_jump_noisy0.bptrace"}) {
    Trace t = load(f);
    ReplayResult r = replay_belt(t, ReplayOptions());
    expect_near(t.label_times("jump"), r.times(EV_JUMP), -40, 20, f);
    TEST_ASSERT_EQUAL_MESSAGE(0, count(r, EV_STEP), f);  // push-off and landing aren't steps
    TEST_ASSERT_EQUAL_MESSAGE(0, count(r, EV_CROUCH), f);
    // The point of the belt node: it fires long before take-off.
    uint32_t takeoff = t.label_times("takeoff")[0];
    TEST_ASSERT_TRUE(takeoff - r.times(EV_JUMP)[0] > 200000);
  }
}

void test_long_dip_crouches_then_jumps() {
  Trace t = load("synth_jump_long_dip.bptrace");
  ReplayResult r = replay_belt(t, ReplayOptions());
  expect_near(t.label_times("squat_hold"), r.times(EV_CROUCH), -20, 150, "crouch");
  expect_near(t.label_times("jump"), r.times(EV_JUMP), 0, 30, "jump from squat");
  TEST_ASSERT_EQUAL(0, count(r, EV_STEP));
}

void test_run_start_steps_and_stop() {
  Trace t = load("synth_run_start.bptrace");
  ReplayResult r = replay_belt(t, ReplayOptions());
  expect_near(t.label_times("run_start"), r.times(EV_RUN_START), 0, 30, "run_start");
  expect_near(t.label_times("step"), r.times(EV_STEP), 0, 30, "steps");
  expect_near(t.label_times("run_stop"), r.times(EV_RUN_STOP), -50, 150, "run_stop");
  TEST_ASSERT_EQUAL(0, count(r, EV_JUMP));
}

void test_steady_runs_measure_cadence() {
  struct Case {
    const char* file;
    float cadence;
  } cases[] = {{"synth_run_1.5.bptrace", 1.5f},
               {"synth_run_2.5.bptrace", 2.5f},
               {"synth_run_2.5_noisy0.bptrace", 2.5f},
               {"synth_run_3.5.bptrace", 3.5f}};
  for (const auto& c : cases) {
    Trace t = load(c.file);
    float cadence_mid = 0;
    bool sprint_mid = false;
    uint32_t mid = t.label_times("step")[t.label_times("step").size() / 2];
    ReplayResult r = replay_belt(t, ReplayOptions(), [&](const BeltGestures& g, uint32_t ts) {
      if (ts <= mid) {
        cadence_mid = g.cadence_hz();
        sprint_mid = g.sprinting();
      }
    });
    expect_near(t.label_times("step"), r.times(EV_STEP), 0, 30, c.file);
    TEST_ASSERT_EQUAL_MESSAGE(0, count(r, EV_JUMP), c.file);
    TEST_ASSERT_EQUAL_MESSAGE(0, count(r, EV_CROUCH), c.file);
    TEST_ASSERT_FLOAT_WITHIN_MESSAGE(0.1f, c.cadence, cadence_mid, c.file);
    if (c.cadence < 3.0f) TEST_ASSERT_FALSE_MESSAGE(sprint_mid, c.file);
  }
}

void test_twist_turns_camera_and_returns() {
  Trace t = load("synth_twist.bptrace");
  uint32_t left = t.label_times("twist_left")[0];
  uint32_t back = t.label_times("twist_back")[0];
  float during = 0, after = 1;
  replay_belt(t, ReplayOptions(), [&](const BeltGestures& g, uint32_t ts) {
    if (ts > left + 500000 && ts < back) during = g.twist_stick();
    if (ts > back + 800000) after = g.twist_stick();
  });
  // Turned 30 degrees left: (30 - 15) / (45 - 15) = 0.5 of full rate, stick to the left.
  TEST_ASSERT_FLOAT_WITHIN(0.08f, -0.5f, during);
  TEST_ASSERT_FLOAT_WITHIN(0.02f, 0.0f, after);
}

void test_event_queue_drops_oldest() {
  EventQueue q;
  for (uint8_t i = 0; i < 10; ++i) q.push({i, 0, i});
  GestureEvent e;
  TEST_ASSERT_TRUE(q.pop(&e));
  TEST_ASSERT_EQUAL(2, e.code);
  int n = 1;
  while (q.pop(&e)) ++n;
  TEST_ASSERT_EQUAL(8, n);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_deadzone_and_cadence_maps);
  RUN_TEST(test_mount_calibration_levels_a_tilted_sensor);
  RUN_TEST(test_calibration_restarts_on_movement);
  RUN_TEST(test_still_is_silent);
  RUN_TEST(test_weight_shift_fires_nothing);
  RUN_TEST(test_weight_shift_lean_tracks_roll);
  RUN_TEST(test_jump_fires_on_the_dip_reversal);
  RUN_TEST(test_long_dip_crouches_then_jumps);
  RUN_TEST(test_run_start_steps_and_stop);
  RUN_TEST(test_steady_runs_measure_cadence);
  RUN_TEST(test_twist_turns_camera_and_returns);
  RUN_TEST(test_event_queue_drops_oldest);
  return UNITY_END();
}

// NodeCore end to end on the host: profile push over "CDC", calibration, the
// synthetic jump fixture through gestures to the HID report, RAW streaming.
#include <unity.h>

#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

#include "bptrace.h"
#include "framing.h"
#include "nodecore.h"

using namespace bp;

struct FakeIo : NodeIo {
  uint32_t t = 0;
  std::vector<std::vector<uint8_t>> frames;  // decoded bodies the node sent
  std::vector<std::pair<uint32_t, HidGamepad>> hid;
  int markers = 0;
  std::vector<uint8_t> saved;
  FrameDecoder dec;

  void send_frame(const uint8_t* wire, size_t len) override {
    for (size_t i = 0; i < len; ++i)
      if (dec.feed(wire[i])) frames.emplace_back(dec.body(), dec.body() + dec.body_len());
  }
  void send_hid(const HidGamepad& r) override { hid.push_back({t, r}); }
  void marker_pulse() override { ++markers; }
  void save_profile(const uint8_t* d, size_t n) override { saved.assign(d, d + n); }
  uint32_t now_us() override { return t; }

  std::vector<Packet> packets(uint8_t kind) const {
    std::vector<Packet> out;
    for (const auto& f : frames) {
      Packet p;
      if (unpack(f.data(), f.size(), &p, nullptr, nullptr) && p.kind == kind) out.push_back(p);
    }
    return out;
  }
};

static std::vector<uint8_t> read_file(const char* name) {
  for (const char* dir : {"test/fixtures/", "firmware/test/fixtures/"}) {
    std::string path = std::string(dir) + name;
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) continue;
    std::vector<uint8_t> out(4096);
    out.resize(fread(out.data(), 1, out.size(), f));
    fclose(f);
    return out;
  }
  TEST_FAIL_MESSAGE("fixture missing");
  return {};
}

static Trace load_trace(const char* name) {
  Trace t;
  std::string err;
  if (!t.load(std::string("test/fixtures/") + name, &err)) t.load(std::string("firmware/test/fixtures/") + name, &err);
  TEST_ASSERT_TRUE_MESSAGE(!t.samples.empty(), err.c_str());
  return t;
}

// Push a profile the way bodyctl does: PROFILE_CHUNK packets of <= 234 bytes.
static void push_profile(NodeCore& core, const std::vector<uint8_t>& prof, uint16_t seq0 = 100) {
  int count = static_cast<int>((prof.size() + kMaxPayloadBytes - 1) / kMaxPayloadBytes);
  for (int i = 0; i < count; ++i) {
    size_t off = i * kMaxPayloadBytes;
    size_t len = prof.size() - off < kMaxPayloadBytes ? prof.size() - off : kMaxPayloadBytes;
    Packet p = make_packet(NODE_PC, seq0 + i, 0, KIND_PROFILE_CHUNK, 0, 0, 0, i, count);
    uint8_t body[kMaxFrameBytes];
    size_t n = pack(p, prof.data() + off, len, body, sizeof(body));
    core.on_frame(body, n);
  }
}

static void command(NodeCore& core, uint8_t cmd, int16_t v0 = 0) {
  Packet p = make_packet(NODE_PC, 7, 0, KIND_CMD, cmd, 0, 0, v0);
  core.on_frame(reinterpret_cast<uint8_t*>(&p), sizeof(p));
}

// Feed trace samples in FIFO-sized bursts of 8, ticking between bursts.
static void play(NodeCore& core, FakeIo& io, const Trace& t) {
  std::vector<NodeSample> burst;
  for (const auto& s : t.samples) {
    NodeSample ns;
    ns.t_us = s.t_us;
    ns.accel_g = s.accel_g;
    ns.gyro_dps = s.gyro_dps;
    for (int i = 0; i < 3; ++i) {
      ns.raw.accel[i] = static_cast<int16_t>((&s.accel_g.x)[i] / t.accel_lsb_g);
      ns.raw.gyro[i] = static_cast<int16_t>((&s.gyro_dps.x)[i] / t.gyro_lsb_dps);
    }
    ns.raw.emg = 0;
    burst.push_back(ns);
    if (burst.size() == 8) {
      io.t = s.t_us + 200;  // read 0.2 ms after the last sample
      core.on_samples(burst.data(), burst.size());
      core.tick();
      burst.clear();
    }
  }
}

void setUp() {}
void tearDown() {}

void test_profile_push_acks_and_saves() {
  FakeIo io;
  NodeCore core(io, NODE_BELT, "test", AXIS_POS_X, 800000);
  auto prof = read_file("gta5.bpprofile");
  push_profile(core, prof);
  auto acks = io.packets(KIND_ACK);
  TEST_ASSERT_EQUAL(2, acks.size());  // 325 bytes = 2 chunks
  TEST_ASSERT_EQUAL(0, acks.back().value[1]);
  TEST_ASSERT_TRUE(core.has_profile());
  TEST_ASSERT_EQUAL(prof.size(), io.saved.size());
}

void test_bad_profile_rejected() {
  FakeIo io;
  NodeCore core(io, NODE_BELT, "test", AXIS_POS_X, 800000);
  auto prof = read_file("gta5.bpprofile");
  prof[30] ^= 0xFF;
  push_profile(core, prof);
  TEST_ASSERT_EQUAL(3, io.packets(KIND_ACK).back().value[1]);
  TEST_ASSERT_FALSE(core.has_profile());
  TEST_ASSERT_TRUE(io.saved.empty());
}

void test_out_of_order_chunk_restarts() {
  FakeIo io;
  NodeCore core(io, NODE_BELT, "test", AXIS_POS_X, 800000);
  Packet p = make_packet(NODE_PC, 1, 0, KIND_PROFILE_CHUNK, 0, 0, 0, 1, 2);  // chunk 1 first
  uint8_t body[32];
  size_t n = pack(p, reinterpret_cast<const uint8_t*>("xx"), 2, body, sizeof(body));
  core.on_frame(body, n);
  TEST_ASSERT_EQUAL(2, io.packets(KIND_ACK).back().value[1]);
}

void test_jump_reaches_hid_with_marker() {
  FakeIo io;
  NodeCore core(io, NODE_BELT, "test", AXIS_POS_X, 800000);
  core.load_profile(read_file("gta5.bpprofile").data(), read_file("gta5.bpprofile").size());
  core.start_calibration();
  Trace t = load_trace("synth_jump.bptrace");
  play(core, io, t);
  TEST_ASSERT_TRUE(core.calibrated());
  // CALIBRATED then JUMP events went out over CDC.
  bool calibrated = false, jumped = false;
  for (const auto& e : io.packets(KIND_EVENT)) {
    calibrated |= e.code == EV_CALIBRATED;
    jumped |= e.code == EV_JUMP;
  }
  TEST_ASSERT_TRUE(calibrated && jumped);
  // Square (bit 4) pressed once, ~30 ms, close to the labeled reversal.
  uint32_t label = t.label_times("jump")[0];
  int presses = 0;
  uint32_t down = 0, up = 0;
  bool prev = false;
  for (const auto& h : io.hid) {
    bool sq = h.second.buttons & (1u << 4);
    if (sq && !prev) {
      ++presses;
      down = h.first;
    }
    if (!sq && prev) up = h.first;
    prev = sq;
  }
  TEST_ASSERT_EQUAL(1, presses);
  TEST_ASSERT_INT_WITHIN(30000, static_cast<int>(label), static_cast<int>(down));
  TEST_ASSERT_TRUE(up - down >= 30000 && up - down < 40000);
  TEST_ASSERT_EQUAL(1, io.markers);
  TEST_ASSERT_TRUE(core.stats().stage(STAGE_SAMPLE_TO_DETECT).count() > 0);
}

void test_state_stream_and_stats_command() {
  FakeIo io;
  NodeCore core(io, NODE_BELT, "test", AXIS_POS_X, 800000);
  core.start_calibration();
  play(core, io, load_trace("synth_run_2.5.bptrace"));
  auto states = io.packets(KIND_STATE);
  TEST_ASSERT_TRUE(states.size() > 300);  // ~100 Hz x 3 fields over several seconds
  int16_t max_cadence = 0;
  for (const auto& s : states)
    if (s.code == ST_CADENCE && s.value[0] > max_cadence) max_cadence = s.value[0];
  TEST_ASSERT_INT_WITHIN(15, 250, max_cadence);
  command(core, CMD_STATS);
  bool log = false;
  for (const auto& f : io.frames) {
    Packet p;
    const uint8_t* pl;
    size_t n;
    if (unpack(f.data(), f.size(), &p, &pl, &n) && p.kind == KIND_LOG)
      log |= std::string(reinterpret_cast<const char*>(pl), n).find("sample->detect") != std::string::npos;
  }
  TEST_ASSERT_TRUE(log);
}

void test_raw_stream_round_trips() {
  FakeIo io;
  NodeCore core(io, NODE_BELT, "test", AXIS_POS_X, 800000);
  core.start_calibration();
  command(core, CMD_RAW_STREAM, 1);
  Trace t = load_trace("synth_still.bptrace");
  play(core, io, t);
  command(core, CMD_RAW_STREAM, 0);
  size_t samples = 0;
  for (const auto& f : io.frames) {
    Packet p;
    const uint8_t* pl;
    size_t n;
    if (unpack(f.data(), f.size(), &p, &pl, &n) && p.kind == KIND_RAW) {
      TEST_ASSERT_EQUAL(p.value[0] * sizeof(RawSample), n);
      samples += p.value[0];
    }
  }
  // Every sample fed in a full burst of 8 was streamed.
  TEST_ASSERT_EQUAL(t.samples.size() / 8 * 8, samples);
}

void test_mode_switch_and_hello() {
  FakeIo io;
  NodeCore core(io, NODE_BELT, "1.2.3", AXIS_POS_X, 800000);
  core.load_profile(read_file("gta5.bpprofile").data(), read_file("gta5.bpprofile").size());
  Packet m = make_packet(NODE_PC, 9, 0, KIND_MODE, MODE_CAR);
  core.on_frame(reinterpret_cast<uint8_t*>(&m), sizeof(m));
  TEST_ASSERT_EQUAL(MODE_CAR, core.mapper().mode());
  TEST_ASSERT_EQUAL(0, io.packets(KIND_ACK).back().value[1]);
  command(core, CMD_HELLO);
  TEST_ASSERT_EQUAL(1, io.packets(KIND_HELLO).size());
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_profile_push_acks_and_saves);
  RUN_TEST(test_bad_profile_rejected);
  RUN_TEST(test_out_of_order_chunk_restarts);
  RUN_TEST(test_jump_reaches_hid_with_marker);
  RUN_TEST(test_state_stream_and_stats_command);
  RUN_TEST(test_raw_stream_round_trips);
  RUN_TEST(test_mode_switch_and_hello);
  return UNITY_END();
}

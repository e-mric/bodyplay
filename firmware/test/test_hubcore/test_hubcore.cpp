// Node -> hub -> Access controller ports, on the host: a NodeCore wired to a
// HubCore like the UART tether, playing the synthetic fixtures.
#include <unity.h>

#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

#include "bptrace.h"
#include "framing.h"
#include "hubcore.h"
#include "nodecore.h"

using namespace bp;

static uint32_t g_now = 0;

struct FakeHubIo : HubIo {
  std::vector<std::pair<uint32_t, OutputFrame>> outputs;
  std::vector<std::vector<uint8_t>> to_nodes;
  std::vector<std::vector<uint8_t>> to_pc;
  int markers = 0;
  FrameDecoder dec;
  void send_pc(const uint8_t* w, size_t n) override {
    for (size_t i = 0; i < n; ++i)
      if (dec.feed(w[i])) to_pc.emplace_back(dec.body(), dec.body() + dec.body_len());
  }
  void send_nodes(const uint8_t* b, size_t n) override { to_nodes.emplace_back(b, b + n); }
  void write_outputs(const OutputFrame& f) override { outputs.push_back({g_now, f}); }
  void marker_pulse() override { ++markers; }
  void save_profile(const uint8_t*, size_t) override {}
  uint32_t now_us() override { return g_now; }

  std::vector<Packet> pc_packets(uint8_t kind) const {
    std::vector<Packet> out;
    for (const auto& f : to_pc) {
      Packet p;
      if (unpack(f.data(), f.size(), &p, nullptr, nullptr) && p.kind == kind) out.push_back(p);
    }
    return out;
  }
};

// The belt node's hub link goes straight into the hub ("UART tether").
struct TetheredNodeIo : NodeIo {
  HubCore* hub = nullptr;
  bool duplicates = false;
  void send_frame(const uint8_t*, size_t) override {}
  void send_hid(const HidGamepad&) override {}
  void marker_pulse() override {}
  void save_profile(const uint8_t*, size_t) override {}
  uint32_t now_us() override { return g_now; }
  void send_hub(const uint8_t* body, size_t len) override { hub->on_node_frame(body, len, g_now); }
  bool hub_wants_duplicates() override { return duplicates; }
};

static std::vector<uint8_t> read_file(const char* name) {
  for (const char* dir : {"test/fixtures/", "firmware/test/fixtures/"}) {
    FILE* f = fopen((std::string(dir) + name).c_str(), "rb");
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
  return t;
}

struct Rig {
  FakeHubIo hub_io;
  HubCore hub{hub_io, "test"};
  TetheredNodeIo node_io;
  NodeCore node{node_io, NODE_BELT, "test", AXIS_POS_X, 800000};
  Rig() {
    auto prof = read_file("gta5.bpprofile");
    TEST_ASSERT_TRUE(hub.load_profile(prof.data(), prof.size()));
    node_io.hub = &hub;
    node.start_calibration();
  }
  // Node bursts of 8 samples; hub ticks every ms in between; SYNC goes back to the node.
  void play(const Trace& t) {
    std::vector<NodeSample> burst;
    uint32_t next_tick = 0;
    for (const auto& s : t.samples) {
      NodeSample ns{};
      ns.t_us = s.t_us;
      ns.accel_g = s.accel_g;
      ns.gyro_dps = s.gyro_dps;
      burst.push_back(ns);
      if (burst.size() < 8) continue;
      uint32_t t_read = s.t_us + 200;
      while (next_tick < t_read) {
        g_now = next_tick;
        hub.tick();
        for (const auto& b : hub_io.to_nodes) node.on_hub_frame(b.data(), b.size());
        hub_io.to_nodes.clear();
        next_tick += 1000;
      }
      g_now = t_read;
      node.on_samples(burst.data(), burst.size());
      node.tick();
      burst.clear();
    }
  }
  // Relay index 1 = A-E3 (square = jump).
  std::vector<std::pair<uint32_t, uint32_t>> relay_pulses(int relay) const {
    std::vector<std::pair<uint32_t, uint32_t>> out;
    bool prev = false;
    uint32_t down = 0;
    for (const auto& o : hub_io.outputs) {
      bool on = o.second.relay[relay];
      if (on && !prev) down = o.first;
      if (!on && prev) out.push_back({down, o.first});
      prev = on;
    }
    return out;
  }
};

void setUp() { g_now = 0; }
void tearDown() {}

void test_jump_closes_square_relay_on_time() {
  Rig rig;
  Trace t = load_trace("synth_jump.bptrace");
  rig.play(t);
  auto pulses = rig.relay_pulses(1);
  TEST_ASSERT_EQUAL(1, pulses.size());
  uint32_t label = t.label_times("jump")[0];
  TEST_ASSERT_INT_WITHIN(30000, static_cast<int>(label), static_cast<int>(pulses[0].first));
  uint32_t held = pulses[0].second - pulses[0].first;
  TEST_ASSERT_TRUE_MESSAGE(held >= 30000 && held <= 32000, "relay held 30 ms (hard rule 8)");
  TEST_ASSERT_EQUAL(1, rig.hub_io.markers);
  // Node clock synced to the hub: the hub logged link and tell->output latencies.
  TEST_ASSERT_TRUE(rig.node.hub_clock().valid());
  TEST_ASSERT_TRUE(rig.hub.stats().stage(STAGE_TELL_TO_OUTPUT).count() >= 1);
  TEST_ASSERT_TRUE(rig.hub.stats().stage(STAGE_RECEIVE_TO_OUTPUT).max_us() < 1000);
}

void test_run_drives_left_stick_dac_then_returns_to_neutral() {
  Rig rig;
  rig.play(load_trace("synth_run_2.5.bptrace"));
  StickAxisCal cal;
  uint16_t neutral = stick_code(0, cal), full_fwd = stick_code(1, cal);
  uint16_t max_y = 0;
  for (const auto& o : rig.hub_io.outputs) {
    for (int b = 0; b < 2; ++b)
      for (int ch = 0; ch < 4; ++ch) TEST_ASSERT_TRUE(o.second.dac[b][ch] <= kMaxCode);  // hard rule 1
    if (o.second.dac[0][1] > max_y) max_y = o.second.dac[0][1];
  }
  TEST_ASSERT_UINT16_WITHIN(5, full_fwd, max_y);  // jog cadence = full forward
  TEST_ASSERT_UINT16_WITHIN(2, neutral, rig.hub.outputs().dac[0][1]);  // stopped at the end
  TEST_ASSERT_EQUAL(0, rig.relay_pulses(1).size());  // no jump while running
}

void test_pc_cannot_inject_gameplay_events() {
  Rig rig;
  Packet ev = make_packet(NODE_PC, 1, 0, KIND_EVENT, EV_JUMP, 255);
  rig.hub.on_pc_frame(reinterpret_cast<uint8_t*>(&ev), sizeof(ev));
  rig.hub.on_node_frame(reinterpret_cast<uint8_t*>(&ev), sizeof(ev), 0);  // even on the node link
  g_now = 5000;
  rig.hub.tick();
  TEST_ASSERT_EQUAL(0, rig.relay_pulses(1).size());
  for (const auto& o : rig.hub_io.outputs) TEST_ASSERT_FALSE(o.second.relay[1]);
}

void test_duplicates_dropped_and_counted() {
  Rig rig;
  rig.node_io.duplicates = true;  // ESP-NOW style: every event twice
  rig.play(load_trace("synth_jump.bptrace"));
  TEST_ASSERT_EQUAL(1, rig.relay_pulses(1).size());
  TEST_ASSERT_TRUE(rig.hub.link(NODE_BELT).duplicates >= 1);
  TEST_ASSERT_EQUAL(0, rig.hub.link(NODE_BELT).lost);
}

void test_stale_body_state_goes_neutral() {
  Rig rig;
  BodyStatePayload b{1000, 500, 0, BODY_RUNNING, 0};
  Packet p = make_packet(NODE_BELT, 1, 0, KIND_STATE, ST_BODY, 255);
  uint8_t body[64];
  size_t n = pack(p, reinterpret_cast<uint8_t*>(&b), sizeof(b), body, sizeof(body));
  g_now = 1000;
  rig.hub.on_node_frame(body, n, g_now);
  StickAxisCal cal;
  TEST_ASSERT_UINT16_WITHIN(2, stick_code(1, cal), rig.hub.outputs().dac[0][1]);
  g_now = 1000 + HubCore::kStateStaleUs + 1000;  // link silent
  rig.hub.tick();
  TEST_ASSERT_UINT16_WITHIN(2, stick_code(0, cal), rig.hub.outputs().dac[0][1]);
}

void test_enter_exit_switches_mode_after_the_press() {
  Rig rig;
  Packet p = make_packet(NODE_BELT, 10, 0, KIND_EVENT, EV_ENTER_EXIT, 255);
  g_now = 1000;
  rig.hub.on_node_frame(reinterpret_cast<uint8_t*>(&p), sizeof(p), g_now);
  TEST_ASSERT_TRUE(rig.hub.outputs().relay[2]);  // A-E4 triangle pressed
  TEST_ASSERT_EQUAL(MODE_ON_FOOT, rig.hub.mode());  // not cut short
  for (uint32_t t = 2000; t <= 40000; t += 1000) {
    g_now = t;
    rig.hub.tick();
  }
  TEST_ASSERT_FALSE(rig.hub.outputs().relay[2]);
  TEST_ASSERT_EQUAL(MODE_CAR, rig.hub.mode());
  // Exit needs "stopped": belt state says running -> no press, stays in the car.
  BodyStatePayload b{800, 0, 0, BODY_RUNNING, 0};
  Packet s = make_packet(NODE_BELT, 11, 0, KIND_STATE, ST_BODY, 255);
  uint8_t body[64];
  size_t n = pack(s, reinterpret_cast<uint8_t*>(&b), sizeof(b), body, sizeof(body));
  rig.hub.on_node_frame(body, n, g_now);
  p.seq = 12;
  rig.hub.on_node_frame(reinterpret_cast<uint8_t*>(&p), sizeof(p), g_now);
  TEST_ASSERT_FALSE(rig.hub.outputs().relay[2]);
  TEST_ASSERT_EQUAL(MODE_CAR, rig.hub.mode());
}

void test_profile_must_fit_wiring_and_commands() {
  auto prof = read_file("gta5.bpprofile");
  ProfileBin p;
  TEST_ASSERT_TRUE(p.parse(prof.data(), prof.size(), nullptr));
  TEST_ASSERT_EQUAL(-1, check_profile_fits(p));
  p.ports[2].type = PORT_STICK;  // A-E3 is a relay on this board
  TEST_ASSERT_EQUAL(2, check_profile_fits(p));
  Rig rig;
  Packet c = make_packet(NODE_PC, 3, 0, KIND_CMD, CMD_STATS);
  rig.hub.on_pc_frame(reinterpret_cast<uint8_t*>(&c), sizeof(c));
  TEST_ASSERT_EQUAL(1, rig.hub_io.pc_packets(KIND_LOG).size());
  TEST_ASSERT_EQUAL(0, rig.hub_io.pc_packets(KIND_ACK).back().value[1]);
}

void test_hil_drives_one_port_and_blocks_body_input() {
  Rig rig;
  auto cmd = [&](int16_t port, int16_t level) {
    Packet c = make_packet(NODE_PC, 1, 0, KIND_CMD, CMD_HIL, 0, 0, port, level);
    rig.hub.on_pc_frame(reinterpret_cast<uint8_t*>(&c), sizeof(c));
    return rig.hub_io.pc_packets(KIND_ACK).back().value[1];
  };
  g_now = 1000;
  TEST_ASSERT_EQUAL(0, cmd(3, 1));  // A-E4 triangle relay
  TEST_ASSERT_TRUE(rig.hub.outputs().relay[2]);
  Packet ev = make_packet(NODE_BELT, 1, 0, KIND_EVENT, EV_JUMP, 255);
  rig.hub.on_node_frame(reinterpret_cast<uint8_t*>(&ev), sizeof(ev), g_now);
  TEST_ASSERT_FALSE(rig.hub.outputs().relay[1]);  // body ignored during the bench test
  TEST_ASSERT_EQUAL(0, cmd(5, 1000));               // B-E2 R2 full
  TEST_ASSERT_EQUAL_UINT16(kMaxCode, rig.hub.outputs().dac[1][2]);
  TEST_ASSERT_EQUAL(0, cmd(0, 1));  // A-E1 up
  TEST_ASSERT_UINT16_WITHIN(2, stick_code(1, StickAxisCal()), rig.hub.outputs().dac[0][1]);
  TEST_ASSERT_EQUAL(2, cmd(9, 1));  // no such port
  g_now = 1000 + HubCore::kHilTimeoutUs + 2000;
  rig.hub.tick();  // times out back to normal
  TEST_ASSERT_FALSE(rig.hub.hil_active());
  TEST_ASSERT_UINT16_WITHIN(2, stick_code(0, StickAxisCal()), rig.hub.outputs().dac[0][1]);
}

void test_dedup_filter() {
  DedupFilter d;
  TEST_ASSERT_TRUE(d.accept(1, 5));
  TEST_ASSERT_FALSE(d.accept(1, 5));
  TEST_ASSERT_TRUE(d.accept(2, 5));  // other node, same seq
  for (uint16_t s = 6; s < 6 + 40; ++s) TEST_ASSERT_TRUE(d.accept(1, s));
  TEST_ASSERT_TRUE(d.accept(1, 5));  // fell out of the window: accepted again
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_jump_closes_square_relay_on_time);
  RUN_TEST(test_run_drives_left_stick_dac_then_returns_to_neutral);
  RUN_TEST(test_pc_cannot_inject_gameplay_events);
  RUN_TEST(test_duplicates_dropped_and_counted);
  RUN_TEST(test_stale_body_state_goes_neutral);
  RUN_TEST(test_enter_exit_switches_mode_after_the_press);
  RUN_TEST(test_profile_must_fit_wiring_and_commands);
  RUN_TEST(test_hil_drives_one_port_and_blocks_body_input);
  RUN_TEST(test_dedup_filter);
  return UNITY_END();
}

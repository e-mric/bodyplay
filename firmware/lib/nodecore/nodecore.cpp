#include "nodecore.h"

#include <stdio.h>
#include <string.h>

#include "param_table.h"

namespace bp {

namespace {
constexpr uint32_t kStatePeriodUs = 10000;  // 100 Hz STATE stream
constexpr int kMaxEventsPerBurst = 16;

int16_t clamp16(float v) {
  if (v > 32767) return 32767;
  if (v < -32768) return -32768;
  return static_cast<int16_t>(v);
}
}  // namespace

float NodeCore::State::source(uint8_t src) const {
  switch (src) {
    case SRC_RUN_CADENCE:
    case SRC_STROKE_RHYTHM: return g_.run_stick();
    case SRC_LEAN: return g_.lean_stick();
    case SRC_TWIST: return g_.twist_stick();
    default: return 0;  // step-back, hand height: wrist / mat sensors (M6-M7)
  }
}

bool NodeCore::State::condition(uint8_t cond) const {
  switch (cond) {
    case COND_STOPPED: return !g_.running();
    case COND_SPRINTING: return g_.sprinting();
    default: return false;  // aiming comes from the wrist node (M6)
  }
}

NodeCore::NodeCore(NodeIo& io, uint8_t node_id, const char* fw_version, MountAxis forward,
                   uint32_t calibration_us)
    : io_(io), node_id_(node_id), fw_version_(fw_version), cal_(forward, calibration_us) {}

bool NodeCore::load_profile(const uint8_t* data, size_t len) {
  ProfileBin p;
  const char* err = nullptr;
  if (!p.parse(data, len, &err)) return false;
  BeltParams bpar;
  for (int i = 0; i < p.n_params; ++i) {
    belt_param_set(&bpar, p.params[i].id, p.params[i].value);
    if (p.params[i].id == 11) jump_min_conf_ = p.params[i].value;  // jump.min_conf
  }
  gestures_.set_params(bpar);
  mapper_.set_profile(p);
  has_profile_ = true;
  return true;
}

void NodeCore::start_calibration() {
  cal_.start(io_.now_us());
  gestures_.reset();
  uint32_t now = io_.now_us();
  mapper_.release_all(now);
  map_and_output(nullptr, 0, now);
}

void NodeCore::send(const Packet& p, const uint8_t* payload, size_t len) {
  uint8_t wire[kMaxWireFrameBytes];
  size_t n = encode_packet(p, payload, len, wire, sizeof(wire));
  if (n) io_.send_frame(wire, n);
}

void NodeCore::send_event(const GestureEvent& e) {
  send(make_packet(node_id_, seq_++, e.t_us, KIND_EVENT, e.code, e.confidence));
}

void NodeCore::send_log(const char* text) {
  Packet p = make_packet(node_id_, seq_++, io_.now_us(), KIND_LOG, 0);
  send(p, reinterpret_cast<const uint8_t*>(text), strlen(text) > kMaxPayloadBytes ? kMaxPayloadBytes : strlen(text));
}

void NodeCore::ack(uint8_t kind, uint16_t seq, int16_t status) {
  send(make_packet(node_id_, seq_++, io_.now_us(), KIND_ACK, kind, 0, 0, static_cast<int16_t>(seq), status));
}

void NodeCore::on_samples(const NodeSample* s, size_t n) {
  uint8_t events[kMaxEventsPerBurst];
  int n_events = 0;
  uint32_t first_event_t = 0;
  for (size_t i = 0; i < n; ++i) {
    if (raw_stream_) {
      if (raw_n_ == 0) raw_t0_us_ = s[i].t_us;
      raw_buf_[raw_n_++] = s[i].raw;
      if (raw_n_ == kMaxRawSamplesPerFrame) flush_raw();
    }
    if (!cal_.done()) {
      if (cal_.feed(s[i].accel_g, s[i].gyro_dps, s[i].t_us))
        send(make_packet(node_id_, seq_++, s[i].t_us, KIND_EVENT, EV_CALIBRATED, 255));
      continue;
    }
    gestures_.update(cal_.to_body(s[i].accel_g), cal_.gyro_to_body(s[i].gyro_dps), s[i].t_us);
    GestureEvent e;
    while (gestures_.pop(&e)) {
      uint32_t now = io_.now_us();
      stats_.add(STAGE_SAMPLE_TO_DETECT, now - e.t_us);
      send_event(e);
      bool gated = e.code == EV_JUMP && e.confidence < static_cast<uint8_t>(jump_min_conf_ * 255.0f);
      if (!gated && n_events < kMaxEventsPerBurst) {
        if (n_events == 0) first_event_t = e.t_us;
        events[n_events++] = e.code;
      }
    }
  }
  if (cal_.done()) map_and_output(events, n_events, first_event_t);
}

void NodeCore::map_and_output(const uint8_t* events, int n, uint32_t sample_t_us) {
  if (!has_profile_) return;
  uint32_t now = io_.now_us();
  bool critical = false;
  if (!mapper_.apply(state_, events, n, now, &critical)) return;
  HidGamepad h = to_hid(mapper_.report());
  if (hid_sent_ && memcmp(&h, &last_hid_, sizeof(h)) == 0) return;
  io_.send_hid(h);
  if (critical) io_.marker_pulse();
  if (n > 0) stats_.add(STAGE_TELL_TO_OUTPUT, io_.now_us() - sample_t_us);
  last_hid_ = h;
  hid_sent_ = true;
  // Mirror the report to the PC for the dashboard (after the HID send: off the hot path).
  send(make_packet(node_id_, seq_++, now, KIND_STATE, ST_PAD, 255), reinterpret_cast<const uint8_t*>(&h),
       sizeof(h));
}

void NodeCore::flush_raw() {
  if (raw_n_ == 0) return;
  Packet p = make_packet(node_id_, seq_++, raw_t0_us_, KIND_RAW, 0, 0, 0, raw_n_, 600);
  send(p, reinterpret_cast<const uint8_t*>(raw_buf_), raw_n_ * sizeof(RawSample));
  raw_n_ = 0;
}

void NodeCore::tick() {
  uint32_t now = io_.now_us();
  map_and_output(nullptr, 0, now);  // releases presses on time between IMU bursts
  if (!cal_.done() || now - last_state_us_ < kStatePeriodUs) return;
  last_state_us_ = now;
  const BeltGestures& g = gestures_;
  send(make_packet(node_id_, seq_++, now, KIND_STATE, ST_CADENCE, 255, 0, clamp16(g.cadence_hz() * 100),
                   clamp16(g.run_stick() * 1000)));
  send(make_packet(node_id_, seq_++, now, KIND_STATE, ST_LEAN, 255, 0, clamp16(g.lean_deg() * 100),
                   clamp16(g.lean_stick() * 1000)));
  send(make_packet(node_id_, seq_++, now, KIND_STATE, ST_TWIST, 255, 0, clamp16(g.twist_deg() * 100),
                   clamp16(g.twist_stick() * 1000)));
}

void NodeCore::on_frame(const uint8_t* body, size_t len) {
  Packet p;
  const uint8_t* payload;
  size_t plen;
  if (!unpack(body, len, &p, &payload, &plen)) return;
  switch (p.kind) {
    case KIND_CMD: on_command(p); break;
    case KIND_PROFILE_CHUNK: on_profile_chunk(p, payload, plen); break;
    case KIND_MODE: {
      bool ok = mapper_.set_mode(p.code, io_.now_us());
      ack(KIND_MODE, p.seq, ok ? 0 : 1);
      break;
    }
    case KIND_PING: {
      // Tier 1: the PC plays the hub's clock role. Echo the ping with our clock.
      uint8_t orig[sizeof(Packet)];
      memcpy(orig, &p, sizeof(Packet));
      send(make_packet(node_id_, seq_++, io_.now_us(), KIND_PONG), orig, sizeof(orig));
      break;
    }
    default: break;
  }
}

void NodeCore::on_command(const Packet& p) {
  switch (p.code) {
    case CMD_HELLO: {
      uint8_t wire[kMaxWireFrameBytes];
      size_t n = encode_hello(node_id_, seq_++, io_.now_us(), fw_version_, wire, sizeof(wire));
      if (n) io_.send_frame(wire, n);
      break;
    }
    case CMD_STATS: {
      char text[kMaxPayloadBytes + 1];
      stats_.format(text, sizeof(text));
      send_log(text[0] ? text : "no latency samples yet");
      break;
    }
    case CMD_STATS_RESET: stats_.reset(); break;
    case CMD_MARKER: io_.marker_pulse(); break;
    case CMD_RAW_STREAM:
      if (!p.value[0]) flush_raw();
      raw_stream_ = p.value[0] != 0;
      raw_n_ = 0;
      break;
    case CMD_CALIBRATE: start_calibration(); break;
    default: ack(KIND_CMD, p.seq, 1); return;
  }
  ack(KIND_CMD, p.seq, 0);
}

void NodeCore::on_profile_chunk(const Packet& p, const uint8_t* payload, size_t len) {
  int16_t index = p.value[0], count = p.value[1];
  if (index == 0) {
    prof_len_ = 0;
    prof_next_ = 0;
  }
  if (index != prof_next_ || count <= 0 || prof_len_ + len > sizeof(prof_buf_)) {
    prof_next_ = 0;
    ack(KIND_PROFILE_CHUNK, p.seq, 2);  // out of order or too big: restart from chunk 0
    return;
  }
  memcpy(prof_buf_ + prof_len_, payload, len);
  prof_len_ += len;
  ++prof_next_;
  if (prof_next_ < count) {
    ack(KIND_PROFILE_CHUNK, p.seq, 0);
    return;
  }
  bool ok = load_profile(prof_buf_, prof_len_);
  if (ok) {
    io_.save_profile(prof_buf_, prof_len_);
    mapper_.release_all(io_.now_us());
  }
  prof_next_ = 0;
  ack(KIND_PROFILE_CHUNK, p.seq, ok ? 0 : 3);
}

}  // namespace bp

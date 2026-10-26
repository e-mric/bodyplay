// Everything the Tier 1 body node does between "IMU samples arrived" and
// "HID report + packets go out", with no Arduino in it so it runs natively in
// tests. src/node/main.cpp owns the I/O: I2C/FIFO, USB HID, CDC, NVS, LED.
//
// Threading: call every method from one task (the node calls them from its
// IMU task and forwards CDC frames to it through a queue).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "belt.h"
#include "framing.h"
#include "hid_report.h"
#include "latency.h"
#include "mount.h"
#include "padmap.h"
#include "profile_bin.h"
#include "protocol.h"

namespace bp {

// What the core needs from the board.
class NodeIo {
 public:
  virtual ~NodeIo() = default;
  virtual void send_frame(const uint8_t* wire, size_t len) = 0;  // already COBS-framed
  virtual void send_hid(const HidGamepad& report) = 0;
  virtual void marker_pulse() = 0;
  virtual void save_profile(const uint8_t* data, size_t len) = 0;
  virtual uint32_t now_us() = 0;
};

struct NodeSample {
  uint32_t t_us;
  Vec3 accel_g;   // sensor frame
  Vec3 gyro_dps;  // sensor frame
  RawSample raw;  // as read, for RAW streaming
};

class NodeCore {
 public:
  NodeCore(NodeIo& io, uint8_t node_id, const char* fw_version, MountAxis forward = AXIS_POS_X,
           uint32_t calibration_us = 2000000);

  // Boot: load a stored profile (falls back to none) and start calibrating.
  bool load_profile(const uint8_t* data, size_t len);
  void start_calibration();

  // A burst of samples from the FIFO, oldest first.
  void on_samples(const NodeSample* s, size_t n);
  // A decoded frame body from the PC (CDC).
  void on_frame(const uint8_t* body, size_t len);
  // Call every ~10 ms: STATE packets, report re-evaluation for press timing.
  void tick();

  bool calibrated() const { return cal_.done(); }
  bool has_profile() const { return has_profile_; }
  bool raw_streaming() const { return raw_stream_; }
  const BeltGestures& gestures() const { return gestures_; }
  const PadMapper& mapper() const { return mapper_; }
  const LatencyStats& stats() const { return stats_; }

 private:
  class State : public BodyState {
   public:
    explicit State(const BeltGestures& g) : g_(g) {}
    float source(uint8_t src) const override;
    bool condition(uint8_t cond) const override;

   private:
    const BeltGestures& g_;
  };

  void send(const Packet& p, const uint8_t* payload = nullptr, size_t len = 0);
  void send_event(const GestureEvent& e);
  void send_log(const char* text);
  void ack(uint8_t kind, uint16_t seq, int16_t status);
  void map_and_output(const uint8_t* events, int n, uint32_t sample_t_us);
  void flush_raw();
  void on_profile_chunk(const Packet& p, const uint8_t* payload, size_t len);
  void on_command(const Packet& p);

  NodeIo& io_;
  uint8_t node_id_;
  const char* fw_version_;
  uint16_t seq_ = 0;
  MountCalibration cal_;
  BeltGestures gestures_;
  State state_{gestures_};
  PadMapper mapper_;
  bool has_profile_ = false;
  float jump_min_conf_ = 0.6f;
  LatencyStats stats_;
  HidGamepad last_hid_;
  bool hid_sent_ = false;
  uint32_t last_state_us_ = 0;

  // RAW streaming.
  bool raw_stream_ = false;
  RawSample raw_buf_[kMaxRawSamplesPerFrame];
  uint8_t raw_n_ = 0;
  uint32_t raw_t0_us_ = 0;

  // Profile push.
  uint8_t prof_buf_[kMaxProfileBytes];
  size_t prof_len_ = 0;
  int16_t prof_next_ = 0;
};

}  // namespace bp

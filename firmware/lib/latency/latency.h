// Timing instrumentation (hard rule 6): per-stage latency histograms and the
// marker LED pulse the 240 fps camera sees. Pure C++, no heap, no Arduino.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace bp {

enum LatencyStage : uint8_t {
  STAGE_SAMPLE_TO_DETECT = 0,   // IMU sample time -> gesture event fired
  STAGE_DETECT_TO_SEND = 1,     // event fired -> packet handed to USB / ESP-NOW
  STAGE_SEND_TO_RECEIVE = 2,    // link latency (hub clock, needs time sync)
  STAGE_RECEIVE_TO_OUTPUT = 3,  // hub receive -> relay / DAC / HID report
  STAGE_TELL_TO_OUTPUT = 4,     // end to end inside the device
  STAGE_COUNT = 5,
};

const char* stage_name(uint8_t stage);

// Fixed-bucket histogram: 100 us buckets up to 50 ms, plus an overflow count.
class LatencyHistogram {
 public:
  static constexpr uint32_t kBucketUs = 100;
  static constexpr size_t kBuckets = 500;

  void add(uint32_t us);
  void reset();

  uint32_t count() const { return count_; }
  uint32_t overflow() const { return overflow_; }
  uint32_t min_us() const { return count_ ? min_us_ : 0; }
  uint32_t max_us() const { return max_us_; }
  // Upper edge of the bucket holding the p-th percentile (p in 0..100). Overflowed
  // samples count as max_us(). Returns 0 when empty.
  uint32_t percentile_us(float p) const;

 private:
  uint16_t buckets_[kBuckets] = {};
  uint32_t count_ = 0;
  uint32_t overflow_ = 0;
  uint32_t min_us_ = UINT32_MAX;
  uint32_t max_us_ = 0;
};

class LatencyStats {
 public:
  void add(uint8_t stage, uint32_t us) {
    if (stage < STAGE_COUNT) h_[stage].add(us);
  }
  const LatencyHistogram& stage(uint8_t s) const { return h_[s < STAGE_COUNT ? s : 0]; }
  void reset() {
    for (auto& h : h_) h.reset();
  }
  // One line per non-empty stage: "detect->send n=120 p50=0.3 p99=0.8 max=1.2 ms".
  // Returns characters written (excluding NUL), truncated to cap.
  size_t format(char* out, size_t cap) const;

 private:
  LatencyHistogram h_[STAGE_COUNT];
};

// The marker LED lights for kOnUs on every output change. A trigger while lit
// restarts the pulse (the camera can't separate two edges < 20 ms apart anyway).
class MarkerPulse {
 public:
  static constexpr uint32_t kOnUs = 20000;

  void trigger(uint32_t now_us) {
    on_ = true;
    since_us_ = now_us;
  }
  // Call from a timer or loop; returns the LED level.
  bool update(uint32_t now_us) {
    if (on_ && now_us - since_us_ >= kOnUs) on_ = false;
    return on_;
  }
  bool on() const { return on_; }

 private:
  bool on_ = false;
  uint32_t since_us_ = 0;
};

}  // namespace bp

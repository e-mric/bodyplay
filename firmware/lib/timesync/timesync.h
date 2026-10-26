// Node <-> hub clock sync (hub = reference clock; on Tier 1 the PC plays the hub).
//
// The hub broadcasts SYNC packets carrying its clock. A SYNC that arrives with
// the least delay gives the best offset estimate, so we keep the largest
// (remote - local) sample over a sliding window (two half-window buckets, no
// heap). An occasional PING/PONG round trip adds the one-way delay back in:
// offset ~= best_sample + min_rtt / 2.
//
// All clocks are uint32 microseconds and wrap every ~71 min; differences are
// taken as int32, which is valid while samples are < 35 min apart.
#pragma once

#include <stdint.h>

namespace bp {

class ClockSync {
 public:
  explicit ClockSync(uint32_t window_us = 2000000) : half_window_us_(window_us / 2) {}

  // A SYNC packet: remote_us is the hub clock in the packet, local_rx_us is our
  // clock when it arrived.
  void on_sync(uint32_t remote_us, uint32_t local_rx_us);

  // A completed PING/PONG round trip measured on our clock.
  void on_rtt(uint32_t rtt_us);

  bool valid() const { return have_cur_ || have_prev_; }

  // Estimated hub - local offset in microseconds.
  int32_t offset_us() const;

  uint32_t to_remote(uint32_t local_us) const { return local_us + static_cast<uint32_t>(offset_us()); }
  uint32_t to_local(uint32_t remote_us) const { return remote_us - static_cast<uint32_t>(offset_us()); }

  uint32_t min_rtt_us() const { return min_rtt_us_; }

 private:
  uint32_t half_window_us_;
  uint32_t bucket_start_us_ = 0;
  bool have_cur_ = false;
  bool have_prev_ = false;
  int32_t cur_best_ = 0;
  int32_t prev_best_ = 0;
  uint32_t min_rtt_us_ = 0;  // 0 = unknown
};

}  // namespace bp

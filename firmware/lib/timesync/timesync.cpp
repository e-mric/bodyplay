#include "timesync.h"

namespace bp {

void ClockSync::on_sync(uint32_t remote_us, uint32_t local_rx_us) {
  int32_t sample = static_cast<int32_t>(remote_us - local_rx_us);
  if (!have_cur_ && !have_prev_) {
    bucket_start_us_ = local_rx_us;
  } else if (static_cast<int32_t>(local_rx_us - bucket_start_us_) >= static_cast<int32_t>(half_window_us_)) {
    // Rotate buckets; if more than a full window passed, the old best is stale too.
    bool skipped = static_cast<int32_t>(local_rx_us - bucket_start_us_) >= static_cast<int32_t>(2 * half_window_us_);
    prev_best_ = cur_best_;
    have_prev_ = have_cur_ && !skipped;
    have_cur_ = false;
    bucket_start_us_ = local_rx_us;
  }
  if (!have_cur_ || sample > cur_best_) {
    cur_best_ = sample;
    have_cur_ = true;
  }
}

void ClockSync::on_rtt(uint32_t rtt_us) {
  if (min_rtt_us_ == 0 || rtt_us < min_rtt_us_) min_rtt_us_ = rtt_us;
}

int32_t ClockSync::offset_us() const {
  int32_t best;
  if (have_cur_ && have_prev_) {
    best = cur_best_ > prev_best_ ? cur_best_ : prev_best_;
  } else if (have_cur_) {
    best = cur_best_;
  } else if (have_prev_) {
    best = prev_best_;
  } else {
    return 0;
  }
  return best + static_cast<int32_t>(min_rtt_us_ / 2);
}

}  // namespace bp

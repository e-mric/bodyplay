#include "latency.h"

#include <stdio.h>

namespace bp {

const char* stage_name(uint8_t stage) {
  switch (stage) {
    case STAGE_SAMPLE_TO_DETECT: return "sample->detect";
    case STAGE_DETECT_TO_SEND: return "detect->send";
    case STAGE_SEND_TO_RECEIVE: return "send->receive";
    case STAGE_RECEIVE_TO_OUTPUT: return "receive->output";
    case STAGE_TELL_TO_OUTPUT: return "tell->output";
    default: return "?";
  }
}

void LatencyHistogram::add(uint32_t us) {
  ++count_;
  if (us < min_us_) min_us_ = us;
  if (us > max_us_) max_us_ = us;
  size_t b = us / kBucketUs;
  if (b >= kBuckets) {
    ++overflow_;
  } else if (buckets_[b] < UINT16_MAX) {
    ++buckets_[b];
  }
}

void LatencyHistogram::reset() {
  for (auto& b : buckets_) b = 0;
  count_ = overflow_ = max_us_ = 0;
  min_us_ = UINT32_MAX;
}

uint32_t LatencyHistogram::percentile_us(float p) const {
  if (count_ == 0) return 0;
  if (p < 0) p = 0;
  if (p > 100) p = 100;
  // Rank of the sample we want, 1-based, rounding up (nearest-rank method).
  uint32_t rank = static_cast<uint32_t>(p / 100.0f * count_ + 0.999f);
  if (rank == 0) rank = 1;
  uint32_t seen = 0;
  for (size_t b = 0; b < kBuckets; ++b) {
    seen += buckets_[b];
    if (seen >= rank) {
      uint32_t edge = static_cast<uint32_t>((b + 1) * kBucketUs);
      return edge < max_us_ ? edge : max_us_;
    }
  }
  return max_us_;
}

size_t LatencyStats::format(char* out, size_t cap) const {
  if (cap == 0) return 0;
  size_t n = 0;
  out[0] = '\0';
  for (uint8_t s = 0; s < STAGE_COUNT; ++s) {
    const LatencyHistogram& h = h_[s];
    if (h.count() == 0) continue;
    int w = snprintf(out + n, cap - n, "%s n=%lu p50=%.1f p99=%.1f max=%.1f ms%s\n", stage_name(s),
                     static_cast<unsigned long>(h.count()), h.percentile_us(50) / 1000.0,
                     h.percentile_us(99) / 1000.0, h.max_us() / 1000.0, h.overflow() ? " (overflow)" : "");
    if (w < 0) break;
    if (static_cast<size_t>(w) >= cap - n) {
      n = cap - 1;
      break;
    }
    n += static_cast<size_t>(w);
  }
  return n;
}

}  // namespace bp

// Reassembles a profile pushed as PROFILE_CHUNK packets (value[0] = index,
// value[1] = count). Shared by the node and the hub.
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "profile_bin.h"

namespace bp {

class ProfileReceiver {
 public:
  enum Status : int16_t { MORE = 0, COMPLETE = 1, OUT_OF_ORDER = 2 };

  Status feed(int16_t index, int16_t count, const uint8_t* payload, size_t len) {
    if (index == 0) {
      len_ = 0;
      next_ = 0;
    }
    if (index != next_ || count <= 0 || len_ + len > sizeof(buf_)) {
      next_ = 0;
      return OUT_OF_ORDER;
    }
    memcpy(buf_ + len_, payload, len);
    len_ += len;
    if (++next_ < count) return MORE;
    next_ = 0;
    return COMPLETE;
  }
  const uint8_t* data() const { return buf_; }
  size_t size() const { return len_; }

 private:
  uint8_t buf_[kMaxProfileBytes];
  size_t len_ = 0;
  int16_t next_ = 0;
};

// ACK status codes for PROFILE_CHUNK (pc/bodyctl/output/device.py explains them).
enum ProfileAck : int16_t { PROFILE_ACK_OK = 0, PROFILE_ACK_ORDER = 2, PROFILE_ACK_REJECTED = 3 };

}  // namespace bp

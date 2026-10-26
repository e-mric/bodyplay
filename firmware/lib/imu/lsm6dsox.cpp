#include "lsm6dsox.h"

namespace bp {
namespace lsm6dsox {

bool FifoParser::feed(const uint8_t word[kFifoWordBytes], RawImu* out) {
  uint8_t tag = word[0] >> 3;
  int16_t v[3];
  for (int i = 0; i < 3; ++i) v[i] = static_cast<int16_t>(word[1 + 2 * i] | (word[2 + 2 * i] << 8));
  if (tag == TAG_ACCEL) {
    for (int i = 0; i < 3; ++i) cur_.accel[i] = v[i];
    have_accel_ = true;
  } else if (tag == TAG_GYRO) {
    for (int i = 0; i < 3; ++i) cur_.gyro[i] = v[i];
    have_gyro_ = true;
  } else {
    ++unknown_tags_;  // timestamp / temperature words if ever enabled: skip
    return false;
  }
  if (have_accel_ && have_gyro_) {
    *out = cur_;
    have_accel_ = have_gyro_ = false;
    return true;
  }
  return false;
}

}  // namespace lsm6dsox
}  // namespace bp

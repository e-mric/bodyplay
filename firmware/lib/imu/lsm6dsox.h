// ST LSM6DSOX: register map subset, configuration sequence and FIFO parser.
// Pure C++ (no Wire) so the parser is native-tested; src/node/imu.cpp does the I/O.
//
// Configuration: accel +-8 g, gyro +-2000 dps, both at 1666 Hz, batched into the
// FIFO in continuous mode; INT1 fires at the watermark so the node reads in bursts
// from an interrupt-driven task (hard rule 9: no loop() polling).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "vec3.h"

namespace bp {
namespace lsm6dsox {

constexpr uint8_t kI2cAddr = 0x6A;  // Adafruit breakout default (SDO/SA0 pulled low)
constexpr uint8_t kI2cAddrAlt = 0x6B;
constexpr uint8_t kWhoAmIValue = 0x6C;

enum Reg : uint8_t {
  FIFO_CTRL1 = 0x07,  // WTM[7:0]
  FIFO_CTRL2 = 0x08,  // bit0 = WTM[8]
  FIFO_CTRL3 = 0x09,  // BDR_GY[7:4] | BDR_XL[3:0]
  FIFO_CTRL4 = 0x0A,  // FIFO_MODE[2:0]
  INT1_CTRL = 0x0D,
  WHO_AM_I = 0x0F,
  CTRL1_XL = 0x10,
  CTRL2_G = 0x11,
  CTRL3_C = 0x12,
  CTRL4_C = 0x13,
  FIFO_STATUS1 = 0x3A,
  FIFO_STATUS2 = 0x3B,
  FIFO_DATA_OUT_TAG = 0x78,  // then 6 data bytes X_L..Z_H
};

constexpr float kAccelLsbG = 0.000244f;  // +-8 g
constexpr float kGyroLsbDps = 0.070f;    // +-2000 dps
constexpr uint32_t kOdrHz = 1666;
constexpr uint32_t kSamplePeriodUs = 600;  // nominal 1/1666.7 Hz
constexpr uint16_t kWatermarkWords = 16;   // 8 samples (accel + gyro words) ~ 4.8 ms
constexpr size_t kFifoWordBytes = 7;       // tag + 6 data bytes

struct RegWrite {
  uint8_t reg;
  uint8_t value;
};

// Written in order after a software reset (CTRL3_C = 0x01, wait ~10 ms).
constexpr RegWrite kConfig[] = {
    {CTRL3_C, 0x44},                          // BDU + register auto-increment
    {CTRL1_XL, 0x8C},                         // ODR 1666 Hz, FS +-8 g
    {CTRL2_G, 0x8C},                          // ODR 1666 Hz, FS +-2000 dps
    {FIFO_CTRL1, kWatermarkWords & 0xFF},     // watermark
    {FIFO_CTRL2, (kWatermarkWords >> 8) & 1},
    {FIFO_CTRL3, 0x88},                       // batch gyro and accel at 1667 Hz
    {FIFO_CTRL4, 0x06},                       // continuous mode
    {INT1_CTRL, 0x08},                        // INT1 = FIFO watermark
};

// FIFO_STATUS1/2 -> number of unread words; overrun flag.
inline uint16_t fifo_words(uint8_t status1, uint8_t status2, bool* overrun) {
  if (overrun) *overrun = status2 & 0x40;
  return static_cast<uint16_t>(status1 | ((status2 & 0x03) << 8));
}

enum Tag : uint8_t { TAG_GYRO = 0x01, TAG_ACCEL = 0x02 };

struct RawImu {
  int16_t accel[3];
  int16_t gyro[3];
};

// Pairs tagged FIFO words into samples. Feed words in FIFO order; a sample comes
// out each time both an accel and a gyro word have arrived.
class FifoParser {
 public:
  // Returns true and fills *out when a full sample is ready.
  bool feed(const uint8_t word[kFifoWordBytes], RawImu* out);
  uint32_t unknown_tags() const { return unknown_tags_; }

 private:
  bool have_accel_ = false;
  bool have_gyro_ = false;
  RawImu cur_{};
  uint32_t unknown_tags_ = 0;
};

inline Vec3 accel_g(const RawImu& r) {
  return {r.accel[0] * kAccelLsbG, r.accel[1] * kAccelLsbG, r.accel[2] * kAccelLsbG};
}
inline Vec3 gyro_dps(const RawImu& r) {
  return {r.gyro[0] * kGyroLsbDps, r.gyro[1] * kGyroLsbDps, r.gyro[2] * kGyroLsbDps};
}

}  // namespace lsm6dsox
}  // namespace bp

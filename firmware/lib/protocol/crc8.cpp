#include "crc8.h"

namespace bp {

namespace {

struct Crc8Table {
  uint8_t t[256];
  constexpr Crc8Table() : t() {
    for (int i = 0; i < 256; ++i) {
      uint8_t c = static_cast<uint8_t>(i);
      for (int b = 0; b < 8; ++b) c = (c & 0x80) ? static_cast<uint8_t>((c << 1) ^ 0x07) : static_cast<uint8_t>(c << 1);
      t[i] = c;
    }
  }
};

constexpr Crc8Table kTable;

}  // namespace

uint8_t crc8(const uint8_t* data, size_t len, uint8_t crc) {
  for (size_t i = 0; i < len; ++i) crc = kTable.t[crc ^ data[i]];
  return crc;
}

}  // namespace bp

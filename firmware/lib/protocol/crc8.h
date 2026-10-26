#pragma once

#include <stddef.h>
#include <stdint.h>

namespace bp {

// CRC-8, polynomial 0x07, init 0x00, no reflection (CRC-8/SMBUS).
// Check value: crc8("123456789") == 0xF4.
uint8_t crc8(const uint8_t* data, size_t len, uint8_t crc = 0x00);

}  // namespace bp

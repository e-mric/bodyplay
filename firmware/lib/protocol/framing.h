// Serial framing: COBS(body + crc8(body)) followed by a 0x00 delimiter.
// Used on USB CDC and the UART tether; ESP-NOW frames carry the bare body.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "protocol.h"

namespace bp {

// Worst case COBS overhead is one byte per 254, plus the leading code byte.
constexpr size_t cobs_max_encoded(size_t len) { return len + len / 254 + 1; }
// Largest wire frame: body + crc, COBS-encoded, plus the delimiter.
constexpr size_t kMaxWireFrameBytes = cobs_max_encoded(kMaxFrameBytes + 1) + 1;

// Returns the encoded length, or 0 if out_cap is too small. Output contains no 0x00.
size_t cobs_encode(const uint8_t* src, size_t len, uint8_t* out, size_t out_cap);
// Returns the decoded length, or 0 on malformed input or overflow.
size_t cobs_decode(const uint8_t* src, size_t len, uint8_t* out, size_t out_cap);

// Encodes body into a complete wire frame (with CRC and trailing 0x00).
// Returns bytes written, or 0 if it doesn't fit.
size_t frame_encode(const uint8_t* body, size_t len, uint8_t* out, size_t out_cap);

// Packs header + payload and frames it in one step. Returns wire bytes, or 0.
size_t encode_packet(const Packet& p, const uint8_t* payload, size_t payload_len, uint8_t* out,
                     size_t out_cap);

// Boot announcement: HELLO with protocol version, node role and firmware version.
size_t encode_hello(uint8_t node_id, uint16_t seq, uint32_t t_us, const char* fw_version,
                    uint8_t* out, size_t out_cap);

// Byte-at-a-time frame decoder with a fixed buffer (no heap).
class FrameDecoder {
 public:
  // Feeds one byte. Returns true when a complete, CRC-valid frame is ready;
  // read it with body()/body_len() before the next feed().
  bool feed(uint8_t byte);
  const uint8_t* body() const { return decoded_; }
  size_t body_len() const { return decoded_len_; }

  uint32_t frames_ok() const { return frames_ok_; }
  uint32_t crc_errors() const { return crc_errors_; }
  uint32_t framing_errors() const { return framing_errors_; }

 private:
  uint8_t raw_[kMaxWireFrameBytes];
  size_t raw_len_ = 0;
  bool overflow_ = false;
  uint8_t decoded_[kMaxFrameBytes + 1];
  size_t decoded_len_ = 0;
  uint32_t frames_ok_ = 0;
  uint32_t crc_errors_ = 0;
  uint32_t framing_errors_ = 0;
};

}  // namespace bp

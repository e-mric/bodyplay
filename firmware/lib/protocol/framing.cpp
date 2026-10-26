#include "framing.h"

#include "crc8.h"

namespace bp {

size_t cobs_encode(const uint8_t* src, size_t len, uint8_t* out, size_t out_cap) {
  if (out_cap < cobs_max_encoded(len)) return 0;
  size_t code_idx = 0;
  size_t o = 1;
  uint8_t code = 1;
  for (size_t i = 0; i < len; ++i) {
    if (src[i] == 0) {
      out[code_idx] = code;
      code_idx = o++;
      code = 1;
    } else {
      out[o++] = src[i];
      if (++code == 0xFF) {
        out[code_idx] = code;
        code_idx = o++;
        code = 1;
      }
    }
  }
  out[code_idx] = code;
  return o;
}

size_t cobs_decode(const uint8_t* src, size_t len, uint8_t* out, size_t out_cap) {
  size_t i = 0;
  size_t o = 0;
  while (i < len) {
    uint8_t code = src[i++];
    if (code == 0) return 0;
    for (uint8_t k = 1; k < code; ++k) {
      if (i >= len || src[i] == 0 || o >= out_cap) return 0;
      out[o++] = src[i++];
    }
    if (code != 0xFF && i < len) {
      if (o >= out_cap) return 0;
      out[o++] = 0;
    }
  }
  return o;
}

size_t frame_encode(const uint8_t* body, size_t len, uint8_t* out, size_t out_cap) {
  if (len > kMaxFrameBytes) return 0;
  uint8_t tmp[kMaxFrameBytes + 1];
  for (size_t i = 0; i < len; ++i) tmp[i] = body[i];
  tmp[len] = crc8(body, len);
  size_t n = cobs_encode(tmp, len + 1, out, out_cap);
  if (n == 0 || n >= out_cap) return 0;
  out[n] = 0x00;
  return n + 1;
}

size_t encode_packet(const Packet& p, const uint8_t* payload, size_t payload_len, uint8_t* out,
                     size_t out_cap) {
  uint8_t body[kMaxFrameBytes];
  size_t n = pack(p, payload, payload_len, body, sizeof(body));
  if (n == 0) return 0;
  return frame_encode(body, n, out, out_cap);
}

size_t encode_hello(uint8_t node_id, uint16_t seq, uint32_t t_us, const char* fw_version,
                    uint8_t* out, size_t out_cap) {
  Packet p = make_packet(node_id, seq, t_us, KIND_HELLO, 0, 0, 0, kProtocolVersion, node_id);
  return encode_packet(p, reinterpret_cast<const uint8_t*>(fw_version), strlen(fw_version), out,
                       out_cap);
}

bool FrameDecoder::feed(uint8_t byte) {
  if (byte != 0x00) {
    if (raw_len_ < sizeof(raw_)) {
      raw_[raw_len_++] = byte;
    } else {
      overflow_ = true;
    }
    return false;
  }
  // Delimiter: try to decode what we have.
  size_t raw_len = raw_len_;
  bool overflow = overflow_;
  raw_len_ = 0;
  overflow_ = false;
  if (raw_len == 0) return false;  // back-to-back delimiters are harmless
  if (overflow) {
    ++framing_errors_;
    return false;
  }
  size_t n = cobs_decode(raw_, raw_len, decoded_, sizeof(decoded_));
  if (n < 2) {
    ++framing_errors_;
    return false;
  }
  if (crc8(decoded_, n - 1) != decoded_[n - 1]) {
    ++crc_errors_;
    return false;
  }
  decoded_len_ = n - 1;
  ++frames_ok_;
  return true;
}

}  // namespace bp

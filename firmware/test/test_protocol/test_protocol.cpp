#include <unity.h>

#include <string.h>

#include "crc8.h"
#include "framing.h"
#include "protocol.h"

using namespace bp;

// Golden vectors shared with pc/tests/test_protocol.py: both sides must produce
// these exact bytes, so the C++ and Python encoders can't drift apart.
static const uint8_t kEventBody[] = {0xb0, 0x01, 0x34, 0x12, 0xef, 0xcd, 0xab, 0x89,
                                     0x02, 0x01, 0xe6, 0x01, 0xfe, 0xff, 0x2c, 0x01};
static const uint8_t kEventWire[] = {0x12, 0xb0, 0x01, 0x34, 0x12, 0xef, 0xcd, 0xab, 0x89, 0x02,
                                     0x01, 0xe6, 0x01, 0xfe, 0xff, 0x2c, 0x01, 0x59, 0x00};
static const uint8_t kHelloWire[] = {0x04, 0xb0, 0x0a, 0x07, 0x03, 0xe8, 0x03, 0x01,
                                     0x02, 0x01, 0x01, 0x01, 0x02, 0x01, 0x02, 0x0a,
                                     0x07, 0x30, 0x2e, 0x31, 0x2e, 0x30, 0x3b, 0x00};

static Packet golden_event() {
  return make_packet(NODE_BELT, 0x1234, 0x89ABCDEF, KIND_EVENT, EV_JUMP, 230, FLAG_DUPLICATE, -2,
                     300);
}

void setUp() {}
void tearDown() {}

void test_crc8_check_value() {
  const char* s = "123456789";
  TEST_ASSERT_EQUAL_HEX8(0xF4, crc8(reinterpret_cast<const uint8_t*>(s), 9));
}

void test_pack_matches_golden() {
  uint8_t out[32];
  size_t n = pack(golden_event(), nullptr, 0, out, sizeof(out));
  TEST_ASSERT_EQUAL(16, n);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(kEventBody, out, 16);
}

void test_frame_matches_golden() {
  uint8_t out[kMaxWireFrameBytes];
  size_t n = encode_packet(golden_event(), nullptr, 0, out, sizeof(out));
  TEST_ASSERT_EQUAL(sizeof(kEventWire), n);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(kEventWire, out, n);
}

void test_hello_matches_golden() {
  uint8_t out[kMaxWireFrameBytes];
  size_t n = encode_hello(NODE_HUB, 7, 1000, "0.1.0", out, sizeof(out));
  TEST_ASSERT_EQUAL(sizeof(kHelloWire), n);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(kHelloWire, out, n);
}

void test_cobs_round_trip_edge_cases() {
  // Empty, all zeros, no zeros across the 254-byte block boundary, mixed.
  const size_t lens[] = {0, 1, 2, 253, 254, 255, 250};
  for (size_t li = 0; li < sizeof(lens) / sizeof(lens[0]); ++li) {
    for (int pattern = 0; pattern < 3; ++pattern) {
      uint8_t src[300];
      size_t len = lens[li];
      for (size_t i = 0; i < len; ++i) {
        src[i] = pattern == 0 ? 0 : pattern == 1 ? static_cast<uint8_t>(1 + i % 255)
                                                 : static_cast<uint8_t>(i % 7);
      }
      uint8_t enc[320];
      size_t e = cobs_encode(src, len, enc, sizeof(enc));
      TEST_ASSERT_TRUE(e > 0);
      for (size_t i = 0; i < e; ++i) TEST_ASSERT_NOT_EQUAL(0, enc[i]);
      uint8_t dec[300];
      size_t d = cobs_decode(enc, e, dec, sizeof(dec));
      TEST_ASSERT_EQUAL(len, d);
      if (len) TEST_ASSERT_EQUAL_HEX8_ARRAY(src, dec, len);
    }
  }
}

void test_decoder_round_trip_every_kind() {
  FrameDecoder dec;
  const uint8_t kinds[] = {KIND_HELLO, KIND_EVENT, KIND_STATE,         KIND_SYNC, KIND_PING, KIND_PONG,
                           KIND_MODE,  KIND_LOG,   KIND_PROFILE_CHUNK, KIND_RAW,  KIND_ACK, KIND_CMD};
  for (size_t k = 0; k < sizeof(kinds); ++k) {
    Packet p = make_packet(NODE_WRIST_R, static_cast<uint16_t>(k * 1000), 0x00000100u * k, kinds[k],
                           static_cast<uint8_t>(k), 255, FLAG_SYNCED, -32768, 32767);
    uint8_t payload[kMaxPayloadBytes];
    size_t payload_len = (kinds[k] == KIND_RAW) ? kMaxRawSamplesPerFrame * sizeof(RawSample) : k * 3;
    for (size_t i = 0; i < payload_len; ++i) payload[i] = static_cast<uint8_t>(i);  // includes zeros
    uint8_t wire[kMaxWireFrameBytes];
    size_t n = encode_packet(p, payload, payload_len, wire, sizeof(wire));
    TEST_ASSERT_TRUE(n > 0);
    bool done = false;
    for (size_t i = 0; i < n; ++i) {
      bool got = dec.feed(wire[i]);
      TEST_ASSERT_EQUAL(i == n - 1, got);
      done = done || got;
    }
    TEST_ASSERT_TRUE(done);
    Packet q;
    const uint8_t* pl;
    size_t pl_len;
    TEST_ASSERT_TRUE(unpack(dec.body(), dec.body_len(), &q, &pl, &pl_len));
    TEST_ASSERT_EQUAL_MEMORY(&p, &q, sizeof(Packet));
    TEST_ASSERT_EQUAL(payload_len, pl_len);
    if (payload_len) TEST_ASSERT_EQUAL_HEX8_ARRAY(payload, pl, pl_len);
  }
  TEST_ASSERT_EQUAL(sizeof(kinds), dec.frames_ok());
}

void test_decoder_rejects_corruption_and_resyncs() {
  FrameDecoder dec;
  uint8_t wire[kMaxWireFrameBytes];
  size_t n = encode_packet(golden_event(), nullptr, 0, wire, sizeof(wire));
  wire[5] ^= 0x40;  // flip a bit in the body
  for (size_t i = 0; i < n; ++i) TEST_ASSERT_FALSE(dec.feed(wire[i]));
  TEST_ASSERT_EQUAL(1, dec.crc_errors());
  // Garbage longer than any frame, then a clean frame: must recover.
  for (int i = 0; i < 600; ++i) dec.feed(0x55);
  dec.feed(0x00);
  TEST_ASSERT_EQUAL(1, dec.framing_errors());
  n = encode_packet(golden_event(), nullptr, 0, wire, sizeof(wire));
  bool got = false;
  for (size_t i = 0; i < n; ++i) got = dec.feed(wire[i]);
  TEST_ASSERT_TRUE(got);
}

void test_oversized_payload_rejected() {
  uint8_t payload[kMaxPayloadBytes + 1] = {0};
  uint8_t wire[kMaxWireFrameBytes];
  TEST_ASSERT_EQUAL(0, encode_packet(golden_event(), payload, sizeof(payload), wire, sizeof(wire)));
}

void test_unpack_rejects_bad_magic() {
  uint8_t body[16];
  memcpy(body, kEventBody, 16);
  body[0] = 0x00;
  Packet p;
  TEST_ASSERT_FALSE(unpack(body, 16, &p, nullptr, nullptr));
  TEST_ASSERT_FALSE(unpack(kEventBody, 15, &p, nullptr, nullptr));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_crc8_check_value);
  RUN_TEST(test_pack_matches_golden);
  RUN_TEST(test_frame_matches_golden);
  RUN_TEST(test_hello_matches_golden);
  RUN_TEST(test_cobs_round_trip_edge_cases);
  RUN_TEST(test_decoder_round_trip_every_kind);
  RUN_TEST(test_decoder_rejects_corruption_and_resyncs);
  RUN_TEST(test_oversized_payload_rejected);
  RUN_TEST(test_unpack_rejects_bad_magic);
  return UNITY_END();
}

// Body node (XIAO ESP32S3 + LSM6DSOX). Role is chosen at build time:
//   node_belt  -> BP_ROLE_BELT   (jump, run, lean, twist)
//   node_wrist -> BP_ROLE_WRIST  (aim, punch, shoot via EMG)
//
// Tier 1: the node is a USB composite device, HID gamepad (what chiaki-ng reads)
// + CDC serial (packets, RAW stream, profile push). M0 skeleton: enumerates and
// announces itself; IMU sampling and gestures arrive in M3.
#include <Arduino.h>

#include "USB.h"
#include "USBHIDGamepad.h"
#include "framing.h"
#include "protocol.h"

#if defined(BP_ROLE_BELT)
static constexpr uint8_t kNodeId = bp::NODE_BELT;
#elif defined(BP_ROLE_WRIST)
static constexpr uint8_t kNodeId = bp::NODE_WRIST_R;
#else
#error "Build with BP_ROLE_BELT or BP_ROLE_WRIST"
#endif

static USBHIDGamepad gamepad;
static uint16_t seq = 0;

static void send_hello() {
  uint8_t wire[bp::kMaxWireFrameBytes];
  size_t w = bp::encode_hello(kNodeId, seq++, micros(), BP_FW_VERSION, wire, sizeof(wire));
  if (w) Serial.write(wire, w);
}

void setup() {
  Serial.begin(115200);
  gamepad.begin();
  USB.begin();
}

void loop() {
  static uint32_t last_hello_ms = 0;
  uint32_t now = millis();
  if (now - last_hello_ms >= 1000) {
    last_hello_ms = now;
    send_hello();
  }
  delay(1);
}

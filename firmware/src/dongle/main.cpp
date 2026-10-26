// Tier 1 wireless dongle (second XIAO ESP32S3 plugged into the PC):
// ESP-NOW rx from the belt node -> the same HID gamepad report the wired node builds.
// M0 skeleton: enumerates as HID gamepad + CDC and announces itself.
#include <Arduino.h>

#include "USB.h"
#include "USBHIDGamepad.h"
#include "framing.h"
#include "protocol.h"

static USBHIDGamepad gamepad;
static uint16_t seq = 0;

static void send_hello() {
  uint8_t wire[bp::kMaxWireFrameBytes];
  size_t w = bp::encode_hello(bp::NODE_DONGLE, seq++, micros(), BP_FW_VERSION, wire, sizeof(wire));
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

// Hub (ESP32-S3-DevKitC-1): ESP-NOW rx from body nodes, foot mat, mode machine,
// outputs to the Access controller ports (photoMOS relays + MCP4728 DACs).
// M0 skeleton: announces itself over USB serial. Outputs arrive in M4.
#include <Arduino.h>

#include "framing.h"
#include "protocol.h"

static uint16_t seq = 0;

static void send_hello() {
  uint8_t wire[bp::kMaxWireFrameBytes];
  size_t w = bp::encode_hello(bp::NODE_HUB, seq++, micros(), BP_FW_VERSION, wire, sizeof(wire));
  if (w) Serial.write(wire, w);
}

void setup() { Serial.begin(115200); }

void loop() {
  static uint32_t last_hello_ms = 0;
  uint32_t now = millis();
  if (now - last_hello_ms >= 1000) {
    last_hello_ms = now;
    send_hello();
  }
  delay(1);
}

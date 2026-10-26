// Output marker LED for Tiers 0-1 (any spare XIAO ESP32S3).
// The PC sends a CMD_MARKER frame over USB CDC whenever it changes an output;
// this board lights the LED for 20 ms so the 240 fps camera sees the moment the
// input left the PC. Wire a bright LED + resistor from D0 to GND; the onboard
// LED mirrors it.
#include <Arduino.h>

#include "USB.h"
#include "esp_timer.h"
#include "framing.h"
#include "latency.h"
#include "protocol.h"

static constexpr int kLedPin = D0;          // external marker LED (active high)
static constexpr int kOnboardLedPin = 21;   // XIAO user LED (active low)

static bp::FrameDecoder decoder;
static esp_timer_handle_t off_timer;
static uint16_t seq = 0;

static void led(bool on) {
  digitalWrite(kLedPin, on ? HIGH : LOW);
  digitalWrite(kOnboardLedPin, on ? LOW : HIGH);
}

static void IRAM_ATTR led_off(void*) { led(false); }

static void pulse() {
  led(true);
  esp_timer_stop(off_timer);
  esp_timer_start_once(off_timer, bp::MarkerPulse::kOnUs);
}

// Runs in the USB task as bytes arrive: no loop() polling in the timing path.
static void on_cdc(void*, esp_event_base_t, int32_t event_id, void*) {
  if (event_id != ARDUINO_USB_CDC_RX_EVENT) return;
  while (Serial.available()) {
    if (!decoder.feed(static_cast<uint8_t>(Serial.read()))) continue;
    bp::Packet p;
    if (bp::unpack(decoder.body(), decoder.body_len(), &p, nullptr, nullptr) && p.kind == bp::KIND_CMD &&
        p.code == bp::CMD_MARKER) {
      pulse();
    }
  }
}

void setup() {
  pinMode(kLedPin, OUTPUT);
  pinMode(kOnboardLedPin, OUTPUT);
  led(false);
  esp_timer_create_args_t args = {};
  args.callback = led_off;
  args.name = "marker_off";
  esp_timer_create(&args, &off_timer);
  Serial.onEvent(on_cdc);
  Serial.begin(115200);
  USB.begin();
}

void loop() {
  static uint32_t last_hello_ms = 0;
  uint32_t now = millis();
  if (now - last_hello_ms >= 1000) {
    last_hello_ms = now;
    uint8_t wire[bp::kMaxWireFrameBytes];
    size_t w = bp::encode_hello(bp::NODE_PC, seq++, micros(), BP_FW_VERSION "-marker", wire, sizeof(wire));
    if (w) Serial.write(wire, w);
  }
  delay(10);
}

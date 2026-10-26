// Hub (ESP32-S3-DevKitC-1): body nodes in, PlayStation Access controller ports out.
// All logic is in lib/hubcore (native-tested); this file is board I/O.
//
// Pins (hardware/wiring_tier2.md):
//   I2C bus 0  SDA 8,  SCL 9   -> MCP4728 DAC A (controller A: A-E1 stick)
//   I2C bus 1  SDA 10, SCL 11  -> MCP4728 DAC B (controller B: B-E1 stick, R2, L2)
//   Relays     4 (A-E2), 5 (A-E3), 6 (A-E4), 7 (B-E4)   via 330 ohm -> AQY212EH LED
//   Marker LED 15
//   Belt tether UART1 RX 18, TX 17 @ 2 Mbaud (until ESP-NOW in M5)
//   PC: USB-UART port ("UART" on the board) @ 921600
//
// HARD RULE 1: DAC codes come only from lib/hubout (clamped to 3499 = 1.75 V).
// HARD RULE 2: nothing is powered from the ports; grounds are shared only.
#include <Arduino.h>
#include <Preferences.h>
#include <Wire.h>

#include "default_profile.h"
#include "esp_timer.h"
#include "framing.h"
#include "hubcore.h"

static constexpr int kSdaA = 8, kSclA = 9, kSdaB = 10, kSclB = 11;
static constexpr int kRelayPins[4] = {4, 5, 6, 7};
static constexpr int kMarkerPin = 15;
static constexpr int kTetherRx = 18, kTetherTx = 17;
static constexpr uint32_t kTetherBaud = 2000000;
static constexpr uint32_t kPcBaud = 921600;
static constexpr uint8_t kDacAddr = 0x60;
static constexpr uint32_t kI2cHz = 1000000;

enum Source : uint8_t { SRC_PC = 0, SRC_NODE = 1 };
struct RxItem {
  uint8_t source;
  uint8_t len;
  uint32_t t_rx_us;
  uint8_t body[bp::kMaxFrameBytes];
};

static QueueHandle_t rx_queue;
static esp_timer_handle_t marker_off_timer;
static Preferences prefs;
static TwoWire& wire_a = Wire;
static TwoWire& wire_b = Wire1;

class BoardIo : public bp::HubIo {
 public:
  void send_pc(const uint8_t* wire, size_t len) override { Serial.write(wire, len); }
  void send_nodes(const uint8_t* body, size_t len) override {
    uint8_t wire[bp::kMaxWireFrameBytes];
    size_t n = bp::frame_encode(body, len, wire, sizeof(wire));
    if (n) Serial1.write(wire, n);
    // M5: also esp_now_send() to the registered nodes.
  }
  void write_outputs(const bp::OutputFrame& f) override {
    // Relays first: they are the fastest path (GPIO, < 1 ms photoMOS operate time).
    for (int i = 0; i < 4; ++i) digitalWrite(kRelayPins[i], f.relay[i] ? HIGH : LOW);
    for (int d = 0; d < 2; ++d) {
      if (written_ && memcmp(f.dac[d], last_.dac[d], sizeof(f.dac[d])) == 0) continue;
      uint8_t buf[12];
      size_t n = bp::mcp4728_write_all(f.dac[d], buf, sizeof(buf));  // clamps again
      TwoWire& w = d == 0 ? wire_a : wire_b;
      w.beginTransmission(kDacAddr);
      w.write(buf, n);
      w.endTransmission();
    }
    last_ = f;
    written_ = true;
  }
  void marker_pulse() override {
    digitalWrite(kMarkerPin, HIGH);
    esp_timer_stop(marker_off_timer);
    esp_timer_start_once(marker_off_timer, bp::MarkerPulse::kOnUs);
  }
  void save_profile(const uint8_t* data, size_t len) override { prefs.putBytes("profile", data, len); }
  uint32_t now_us() override { return micros(); }

 private:
  bp::OutputFrame last_;
  bool written_ = false;
};

static BoardIo io;
static bp::HubCore hub(io, BP_FW_VERSION);

static void IRAM_ATTR marker_off(void*) { digitalWrite(kMarkerPin, LOW); }

// Byte streams from the PC and the tether are decoded in their UART callbacks and
// queued whole; the hub task owns HubCore.
static bp::FrameDecoder pc_decoder, tether_decoder;

static void drain(HardwareSerial& port, bp::FrameDecoder& dec, uint8_t source) {
  while (port.available()) {
    if (!dec.feed(static_cast<uint8_t>(port.read()))) continue;
    RxItem item;
    item.source = source;
    item.len = static_cast<uint8_t>(dec.body_len());
    item.t_rx_us = micros();
    memcpy(item.body, dec.body(), dec.body_len());
    xQueueSend(rx_queue, &item, 0);
  }
}

static void on_tether_rx() { drain(Serial1, tether_decoder, SRC_NODE); }
static void on_pc_rx() { drain(Serial, pc_decoder, SRC_PC); }

static void hub_task(void*) {
  RxItem item;
  uint32_t last_tick = micros();
  for (;;) {
    if (xQueueReceive(rx_queue, &item, pdMS_TO_TICKS(1)) == pdTRUE) {
      if (item.source == SRC_NODE) {
        hub.on_node_frame(item.body, item.len, item.t_rx_us);
      } else {
        hub.on_pc_frame(item.body, item.len);
      }
    }
    uint32_t now = micros();
    if (now - last_tick >= 1000) {
      last_tick = now;
      hub.tick();
    }
  }
}

void setup() {
  // Safe state before anything else: relays open, marker off.
  for (int pin : kRelayPins) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
  }
  pinMode(kMarkerPin, OUTPUT);
  digitalWrite(kMarkerPin, LOW);
  esp_timer_create_args_t targs = {};
  targs.callback = marker_off;
  targs.name = "marker_off";
  esp_timer_create(&targs, &marker_off_timer);

  wire_a.begin(kSdaA, kSclA, kI2cHz);
  wire_b.begin(kSdaB, kSclB, kI2cHz);
  // Idle levels (sticks neutral, triggers released) on both DACs right away.
  io.write_outputs(bp::build_output(bp::ProfileBin(), bp::PadReport(), bp::HubCalibration()));

  Serial.begin(kPcBaud);
  Serial1.begin(kTetherBaud, SERIAL_8N1, kTetherRx, kTetherTx);
  rx_queue = xQueueCreate(32, sizeof(RxItem));
  Serial.onReceive(on_pc_rx);
  Serial1.onReceive(on_tether_rx, true);  // fire on RX timeout too: frames are short

  prefs.begin("bodyplay", false);
  uint8_t stored[bp::kMaxProfileBytes];
  size_t len = prefs.getBytesLength("profile");
  bool loaded = len > 0 && len <= sizeof(stored) && prefs.getBytes("profile", stored, len) == len &&
                hub.load_profile(stored, len);
  if (!loaded) hub.load_profile(kDefaultProfile, kDefaultProfileLen);  // built-in GTA V profile

  xTaskCreatePinnedToCore(hub_task, "hub", 8192, nullptr, configMAX_PRIORITIES - 2, nullptr, 1);
}

void loop() { vTaskDelay(portMAX_DELAY); }

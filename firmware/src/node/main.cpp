// Body node (XIAO ESP32S3 + LSM6DSOX). Role is chosen at build time:
//   node_belt  -> BP_ROLE_BELT   (jump, run, lean, twist)
//   node_wrist -> BP_ROLE_WRIST  (M6: aim, punch, shoot via EMG; streams RAW until then)
//
// Tier 1: a USB composite device, HID gamepad (what chiaki-ng reads) + CDC serial
// (events, state, RAW stream, profile push). All gesture and mapping logic is in
// lib/nodecore (native-tested); this file is the board I/O.
//
// Wiring (hardware/wiring_tier1.md): LSM6DSOX SDA -> D4, SCL -> D5, INT1 -> D1,
// 3V3 and GND; marker LED + 220 ohm on D0.
#include <Arduino.h>
#include <Preferences.h>
#include <Wire.h>

#include "USB.h"
#include "USBHIDGamepad.h"
#include "default_profile.h"
#include "esp_timer.h"
#include "freertos/stream_buffer.h"
#include "lsm6dsox.h"
#include "nodecore.h"

#if defined(BP_ROLE_BELT)
static constexpr uint8_t kNodeId = bp::NODE_BELT;
static const char* kProduct = "Bodyplay Belt";
#elif defined(BP_ROLE_WRIST)
static constexpr uint8_t kNodeId = bp::NODE_WRIST_R;
static const char* kProduct = "Bodyplay Wrist";
#else
#error "Build with BP_ROLE_BELT or BP_ROLE_WRIST"
#endif

static constexpr int kPinInt1 = D1;
static constexpr int kPinMarker = D0;
#ifndef BP_I2C_HZ
#define BP_I2C_HZ 1000000  // drop to 400000 if the IMU drops out on long wires
#endif
static constexpr uint32_t kI2cHz = BP_I2C_HZ;
static constexpr size_t kMaxWordsPerRead = 32;

namespace lsm = bp::lsm6dsox;

static USBHIDGamepad gamepad;
static Preferences prefs;
static TaskHandle_t imu_task_handle;
static StreamBufferHandle_t rx_stream;
static esp_timer_handle_t marker_off_timer;
static uint8_t imu_addr = lsm::kI2cAddr;

// --- board I/O for NodeCore ---------------------------------------------------

class BoardIo : public bp::NodeIo {
 public:
  void send_frame(const uint8_t* wire, size_t len) override {
    if (Serial) Serial.write(wire, len);  // non-blocking: TX timeout is 0
  }
  void send_hid(const bp::HidGamepad& r) override { gamepad.send(r.x, r.y, r.z, r.rz, r.rx, r.ry, r.hat, r.buttons); }
  void marker_pulse() override {
    digitalWrite(kPinMarker, HIGH);
    esp_timer_stop(marker_off_timer);
    esp_timer_start_once(marker_off_timer, bp::MarkerPulse::kOnUs);
  }
  void save_profile(const uint8_t* data, size_t len) override { prefs.putBytes("profile", data, len); }
  uint32_t now_us() override { return micros(); }
};

#ifndef BP_MOUNT_FORWARD
#define BP_MOUNT_FORWARD AXIS_NEG_Z
#endif

static BoardIo io;
static bp::NodeCore core(io, kNodeId, BP_FW_VERSION, bp::BP_MOUNT_FORWARD);

// --- LSM6DSOX over I2C ---------------------------------------------------------

static bool imu_write(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(imu_addr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

static bool imu_read(uint8_t reg, uint8_t* out, size_t n) {
  Wire.beginTransmission(imu_addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(imu_addr, static_cast<uint8_t>(n)) != n) return false;
  for (size_t i = 0; i < n; ++i) out[i] = Wire.read();
  return true;
}

static bool imu_init() {
  for (uint8_t addr : {lsm::kI2cAddr, lsm::kI2cAddrAlt}) {
    imu_addr = addr;
    uint8_t who = 0;
    if (imu_read(lsm::WHO_AM_I, &who, 1) && who == lsm::kWhoAmIValue) {
      imu_write(lsm::CTRL3_C, 0x01);  // software reset
      delay(20);
      for (const auto& w : lsm::kConfig)
        if (!imu_write(w.reg, w.value)) return false;
      return true;
    }
  }
  return false;
}

static void IRAM_ATTR on_int1() {
  BaseType_t woken = pdFALSE;
  vTaskNotifyGiveFromISR(imu_task_handle, &woken);
  portYIELD_FROM_ISR(woken);
}

static void IRAM_ATTR marker_off(void*) { digitalWrite(kPinMarker, LOW); }

// CDC bytes arrive in the USB task; hand them to the IMU task, which owns NodeCore.
static void on_cdc(void*, esp_event_base_t, int32_t event_id, void*) {
  if (event_id != ARDUINO_USB_CDC_RX_EVENT) return;
  uint8_t buf[64];
  while (Serial.available()) {
    size_t n = Serial.read(buf, sizeof(buf));
    if (n) xStreamBufferSend(rx_stream, buf, n, 0);
  }
}

static void imu_task(void*) {
  bp::FrameDecoder decoder;
  lsm::FifoParser parser;
  bp::NodeSample samples[kMaxWordsPerRead / 2];
  for (;;) {
    // Woken by the FIFO watermark (~every 4.8 ms); the timeout keeps tick() going.
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(10));

    uint8_t rx[64];
    size_t n_rx;
    while ((n_rx = xStreamBufferReceive(rx_stream, rx, sizeof(rx), 0)) > 0) {
      for (size_t i = 0; i < n_rx; ++i)
        if (decoder.feed(rx[i])) core.on_frame(decoder.body(), decoder.body_len());
    }

    uint8_t st[2];
    if (imu_read(lsm::FIFO_STATUS1, st, 2)) {
      uint16_t words = lsm::fifo_words(st[0], st[1], nullptr);
      if (words > kMaxWordsPerRead) words = kMaxWordsPerRead;
      size_t n = 0;
      for (uint16_t w = 0; w < words; ++w) {
        uint8_t word[lsm::kFifoWordBytes];
        if (!imu_read(lsm::FIFO_DATA_OUT_TAG, word, sizeof(word))) break;
        lsm::RawImu raw;
        if (parser.feed(word, &raw)) {
          bp::NodeSample& s = samples[n++];
          s.accel_g = lsm::accel_g(raw);
          s.gyro_dps = lsm::gyro_dps(raw);
          memcpy(s.raw.accel, raw.accel, sizeof(raw.accel));
          memcpy(s.raw.gyro, raw.gyro, sizeof(raw.gyro));
          s.raw.emg = 0;
        }
      }
      // The newest sample was taken about now; earlier ones one ODR period apart.
      uint32_t t_read = micros();
      for (size_t i = 0; i < n; ++i) samples[i].t_us = t_read - (n - 1 - i) * lsm::kSamplePeriodUs;
      if (n) core.on_samples(samples, n);
    }
    core.tick();
  }
}

void setup() {
  pinMode(kPinMarker, OUTPUT);
  digitalWrite(kPinMarker, LOW);
  esp_timer_create_args_t targs = {};
  targs.callback = marker_off;
  targs.name = "marker_off";
  esp_timer_create(&targs, &marker_off_timer);

  rx_stream = xStreamBufferCreate(2048, 1);
  Serial.onEvent(on_cdc);
  Serial.setTxTimeoutMs(0);
  Serial.begin(115200);
  USB.productName(kProduct);
  USB.manufacturerName("Bodyplay");
  gamepad.begin();
  USB.begin();

  prefs.begin("bodyplay", false);
  uint8_t stored[bp::kMaxProfileBytes];
  size_t len = prefs.getBytesLength("profile");
  bool loaded = len > 0 && len <= sizeof(stored) && prefs.getBytes("profile", stored, len) == len &&
                core.load_profile(stored, len);
  if (!loaded) core.load_profile(kDefaultProfile, kDefaultProfileLen);

  Wire.begin(SDA, SCL, kI2cHz);
  if (!imu_init()) {
    // No IMU: blink the marker LED so the problem is visible without a terminal.
    for (;;) {
      digitalWrite(kPinMarker, !digitalRead(kPinMarker));
      delay(200);
    }
  }
  core.start_calibration();
  xTaskCreatePinnedToCore(imu_task, "imu", 8192, nullptr, configMAX_PRIORITIES - 2, &imu_task_handle, 1);
  pinMode(kPinInt1, INPUT);
  attachInterrupt(kPinInt1, on_int1, RISING);
}

void loop() { vTaskDelay(portMAX_DELAY); }  // everything runs in imu_task

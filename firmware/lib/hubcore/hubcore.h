// The Tier 2+ hub without Arduino: node packets in (UART tether or ESP-NOW),
// Access controller port outputs out (DAC codes + relays), PC for config only.
//
// Hard rule 3: no PC in the hot path. Gameplay events and body state are taken
// only from body nodes; the PC may send profile, mode and commands.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "latency.h"
#include "padmap.h"
#include "ports.h"
#include "profile_bin.h"
#include "profile_rx.h"
#include "protocol.h"

namespace bp {

class HubIo {
 public:
  virtual ~HubIo() = default;
  virtual void send_pc(const uint8_t* wire, size_t len) = 0;     // framed, USB serial
  virtual void send_nodes(const uint8_t* body, size_t len) = 0;  // unframed broadcast (SYNC)
  virtual void write_outputs(const OutputFrame& f) = 0;          // DACs + relays
  virtual void marker_pulse() = 0;
  virtual void save_profile(const uint8_t* data, size_t len) = 0;
  virtual uint32_t now_us() = 0;
};

// Drops repeats of (node, seq): events arrive twice over ESP-NOW.
class DedupFilter {
 public:
  // True if this (node, seq) is new.
  bool accept(uint8_t node, uint16_t seq);

 private:
  static constexpr int kNodes = 4;
  static constexpr int kWindow = 32;
  struct Ring {
    uint8_t node = 0;
    bool used = false;
    uint16_t seqs[kWindow] = {};
    uint8_t n = 0;
    uint8_t head = 0;
  } rings_[kNodes];
};

struct LinkStats {
  uint32_t received = 0;
  uint32_t duplicates = 0;
  uint32_t lost = 0;  // gaps in seq
  uint32_t late = 0;  // synced link latency > 15 ms
  bool have_seq = false;
  uint16_t last_seq = 0;
};

class HubCore {
 public:
  static constexpr uint32_t kSyncPeriodUs = 100000;  // SYNC broadcast every 100 ms
  static constexpr uint32_t kStateStaleUs = 200000;  // no body state for 200 ms -> sticks neutral
  static constexpr uint32_t kLateUs = 15000;
  // Bench test (`bodyctl hil`): drive one port from the PC while body input is ignored.
  // Levels: button 0/1; trigger 0..1000; stick 0 center, 1 up, 2 right, 3 down, 4 left.
  // Ends on port -1 or after kHilTimeoutUs without a command. Never a gameplay path.
  static constexpr uint32_t kHilTimeoutUs = 10000000;

  HubCore(HubIo& io, const char* fw_version) : io_(io), fw_version_(fw_version) {}

  // Returns false (and keeps the old one) if the profile is invalid or doesn't fit the wiring.
  bool load_profile(const uint8_t* data, size_t len);
  void set_calibration(const HubCalibration& c) { cal_ = c; }
  void set_vehicle_mode(uint8_t m) { vehicle_mode_ = m; }

  void on_node_frame(const uint8_t* body, size_t len, uint32_t t_rx_us);
  void on_pc_frame(const uint8_t* body, size_t len);
  // Call at ~1 kHz: press timing, SYNC broadcast, stale-state safety.
  void tick();

  uint8_t mode() const { return mapper_.mode(); }
  bool hil_active() const { return hil_active_; }
  bool has_profile() const { return has_profile_; }
  const LatencyStats& stats() const { return stats_; }
  const LinkStats& link(uint8_t node_id) const;
  const OutputFrame& outputs() const { return out_; }

 private:
  class State : public BodyState {
   public:
    float source(uint8_t src) const override;
    bool condition(uint8_t cond) const override;
    BodyStatePayload belt{};
    bool belt_fresh = false;
    bool aiming = false;
  };

  void update(const uint8_t* events, int n, uint32_t t_rx_us, uint32_t t_sensor_us, bool synced);
  void send(const Packet& p, const uint8_t* payload = nullptr, size_t len = 0);
  void ack(uint8_t kind, uint16_t seq, int16_t status);
  void on_command(const Packet& p);
  bool hil(int16_t port, int16_t level);
  LinkStats& link_mut(uint8_t node_id);

  HubIo& io_;
  const char* fw_version_;
  uint16_t seq_ = 0;
  ProfileBin profile_;
  bool has_profile_ = false;
  PadMapper mapper_;
  HubCalibration cal_;
  State state_;
  uint32_t belt_state_us_ = 0;
  OutputFrame out_;
  bool out_written_ = false;
  DedupFilter dedup_;
  LinkStats links_[4];
  LatencyStats stats_;
  ProfileReceiver prof_rx_;
  uint32_t last_sync_us_ = 0;
  bool sync_started_ = false;
  uint8_t vehicle_mode_ = MODE_CAR;
  // Local enter/exit: switch mode once the Triangle press we sent has been released.
  bool mode_pending_ = false;
  uint8_t pending_mode_ = 0;
  bool exit_press_seen_ = false;
  bool hil_active_ = false;
  uint32_t hil_since_us_ = 0;
};

}  // namespace bp

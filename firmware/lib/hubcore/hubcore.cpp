#include "hubcore.h"

#include <stdio.h>
#include <string.h>

#include "framing.h"

namespace bp {

// --- DedupFilter --------------------------------------------------------------------

bool DedupFilter::accept(uint8_t node, uint16_t seq) {
  Ring* r = nullptr;
  for (auto& ring : rings_)
    if (ring.used && ring.node == node) r = &ring;
  if (!r) {
    for (auto& ring : rings_)
      if (!ring.used) {
        r = &ring;
        break;
      }
    if (!r) r = &rings_[0];  // more nodes than slots: recycle
    *r = Ring();
    r->used = true;
    r->node = node;
  }
  for (int i = 0; i < r->n; ++i)
    if (r->seqs[i] == seq) return false;
  r->seqs[r->head] = seq;
  r->head = (r->head + 1) % kWindow;
  if (r->n < kWindow) ++r->n;
  return true;
}

// --- body state ---------------------------------------------------------------------

float HubCore::State::source(uint8_t src) const {
  if (!belt_fresh) return 0;
  switch (src) {
    case SRC_RUN_CADENCE:
    case SRC_STROKE_RHYTHM: return belt.run_stick / 1000.0f;
    case SRC_LEAN: return belt.lean_stick / 1000.0f;
    case SRC_TWIST: return belt.twist_stick / 1000.0f;
    default: return 0;
  }
}

bool HubCore::State::condition(uint8_t cond) const {
  switch (cond) {
    case COND_STOPPED: return belt_fresh && !(belt.flags & BODY_RUNNING);
    case COND_SPRINTING: return belt_fresh && (belt.flags & BODY_SPRINTING);
    case COND_AIMING: return aiming;
    default: return false;
  }
}

// --- HubCore ------------------------------------------------------------------------

bool HubCore::load_profile(const uint8_t* data, size_t len) {
  ProfileBin p;
  const char* err = nullptr;
  if (!p.parse(data, len, &err) || check_profile_fits(p) >= 0) return false;
  profile_ = p;
  mapper_.set_profile(p);
  has_profile_ = true;
  return true;
}

LinkStats& HubCore::link_mut(uint8_t node_id) { return links_[node_id < 4 ? node_id : 0]; }

const LinkStats& HubCore::link(uint8_t node_id) const { return links_[node_id < 4 ? node_id : 0]; }

void HubCore::send(const Packet& p, const uint8_t* payload, size_t len) {
  uint8_t wire[kMaxWireFrameBytes];
  size_t n = encode_packet(p, payload, len, wire, sizeof(wire));
  if (n) io_.send_pc(wire, n);
}

void HubCore::ack(uint8_t kind, uint16_t seq, int16_t status) {
  send(make_packet(NODE_HUB, seq_++, io_.now_us(), KIND_ACK, kind, 0, 0, static_cast<int16_t>(seq), status));
}

void HubCore::on_node_frame(const uint8_t* body, size_t len, uint32_t t_rx) {
  // Check freshness on arrival too: an event can arrive before the next timer tick.
  if (state_.belt_fresh && t_rx - belt_state_us_ > kStateStaleUs) state_.belt_fresh = false;
  Packet p;
  const uint8_t* payload;
  size_t plen;
  if (!unpack(body, len, &p, &payload, &plen)) return;
  if (p.node_id == NODE_PC || p.node_id == NODE_HUB) return;  // hard rule 3
  LinkStats& ls = link_mut(p.node_id);
  if (!dedup_.accept(p.node_id, p.seq)) {
    ++ls.duplicates;
    return;
  }
  ++ls.received;
  if (ls.have_seq) {
    uint16_t gap = static_cast<uint16_t>(p.seq - ls.last_seq);
    if (gap > 1 && gap < 1000) ls.lost += gap - 1;
  }
  if (!ls.have_seq || static_cast<int16_t>(p.seq - ls.last_seq) > 0) ls.last_seq = p.seq;
  ls.have_seq = true;
  bool synced = p.flags & FLAG_SYNCED;
  if (synced) {
    uint32_t link_us = t_rx - p.t_sensor_us;
    stats_.add(STAGE_SEND_TO_RECEIVE, link_us);
    if (link_us > kLateUs && link_us < 0x80000000u) ++ls.late;
  }

  if (p.kind == KIND_STATE && p.code == ST_BODY && plen >= sizeof(BodyStatePayload)) {
    if (p.node_id == NODE_BELT) {
      memcpy(&state_.belt, payload, sizeof(BodyStatePayload));
      state_.belt_fresh = true;
      belt_state_us_ = t_rx;
    }
    update(nullptr, 0, t_rx, p.t_sensor_us, false);
  } else if (p.kind == KIND_EVENT) {
    if (p.code == EV_AIM_START) state_.aiming = true;
    if (p.code == EV_AIM_END) state_.aiming = false;
    uint8_t ev = p.code;
    if (profile_.accepts_event(ev, p.confidence)) update(&ev, 1, t_rx, p.t_sensor_us, synced);
  }
  // Mirror to the PC dashboard after the outputs are written (off the hot path).
  send(p, payload, plen);
}

void HubCore::update(const uint8_t* events, int n, uint32_t t_rx, uint32_t t_sensor, bool synced) {
  if (!has_profile_ || hil_active_) return;
  uint32_t now = io_.now_us();
  bool critical = false;
  mapper_.apply(state_, events, n, now, &critical);

  // Local enter/exit: when the body fires enter_exit and the mapper actually presses
  // Triangle, switch modes once that press has been released (switching mid-press
  // would cut it short).
  const PadReport& r = mapper_.report();
  for (int i = 0; i < n; ++i) {
    if (events[i] == EV_ENTER_EXIT && r.button(OUT_TRIANGLE)) {
      bool in_vehicle = mapper_.mode() != MODE_ON_FOOT;
      pending_mode_ = in_vehicle ? MODE_ON_FOOT : vehicle_mode_;
      mode_pending_ = true;
      exit_press_seen_ = true;
    }
  }
  if (mode_pending_ && exit_press_seen_ && !r.button(OUT_TRIANGLE)) {
    mode_pending_ = false;
    exit_press_seen_ = false;
    mapper_.set_mode(pending_mode_, now);
    mapper_.apply(state_, nullptr, 0, now, nullptr);
  }

  OutputFrame f = build_output(profile_, mapper_.report(), cal_);
  if (!out_written_ || f != out_) {
    io_.write_outputs(f);
    uint32_t done = io_.now_us();
    if (critical) io_.marker_pulse();
    if (n > 0) {
      stats_.add(STAGE_RECEIVE_TO_OUTPUT, done - t_rx);
      if (synced) stats_.add(STAGE_TELL_TO_OUTPUT, done - t_sensor);
    }
    out_ = f;
    out_written_ = true;
  }
}

void HubCore::tick() {
  uint32_t now = io_.now_us();
  if (hil_active_ && now - hil_since_us_ > kHilTimeoutUs) hil(-1, 0);
  if (!sync_started_ || now - last_sync_us_ >= kSyncPeriodUs) {
    sync_started_ = true;
    last_sync_us_ = now;
    Packet s = make_packet(NODE_HUB, seq_++, now, KIND_SYNC);
    uint8_t body[sizeof(Packet)];
    size_t n = pack(s, nullptr, 0, body, sizeof(body));
    io_.send_nodes(body, n);
  }
  if (state_.belt_fresh && now - belt_state_us_ > kStateStaleUs) state_.belt_fresh = false;  // link lost: neutral
  update(nullptr, 0, now, now, false);
}

void HubCore::on_pc_frame(const uint8_t* body, size_t len) {
  Packet p;
  const uint8_t* payload;
  size_t plen;
  if (!unpack(body, len, &p, &payload, &plen)) return;
  switch (p.kind) {
    case KIND_CMD: on_command(p); break;
    case KIND_MODE: {
      bool ok = mapper_.set_mode(p.code, io_.now_us());
      mode_pending_ = false;
      ack(KIND_MODE, p.seq, ok ? 0 : 1);
      break;
    }
    case KIND_PROFILE_CHUNK: {
      ProfileReceiver::Status st = prof_rx_.feed(p.value[0], p.value[1], payload, plen);
      if (st == ProfileReceiver::OUT_OF_ORDER) {
        ack(KIND_PROFILE_CHUNK, p.seq, PROFILE_ACK_ORDER);
      } else if (st == ProfileReceiver::MORE) {
        ack(KIND_PROFILE_CHUNK, p.seq, PROFILE_ACK_OK);
      } else {
        bool ok = load_profile(prof_rx_.data(), prof_rx_.size());
        if (ok) {
          io_.save_profile(prof_rx_.data(), prof_rx_.size());
          mapper_.release_all(io_.now_us());
        }
        ack(KIND_PROFILE_CHUNK, p.seq, ok ? PROFILE_ACK_OK : PROFILE_ACK_REJECTED);
      }
      break;
    }
    default: break;  // EVENT / STATE from the PC are ignored: hard rule 3
  }
}

void HubCore::on_command(const Packet& p) {
  switch (p.code) {
    case CMD_HELLO: {
      uint8_t wire[kMaxWireFrameBytes];
      size_t n = encode_hello(NODE_HUB, seq_++, io_.now_us(), fw_version_, wire, sizeof(wire));
      if (n) io_.send_pc(wire, n);
      break;
    }
    case CMD_STATS: {
      char text[kMaxPayloadBytes + 1];
      size_t n = stats_.format(text, sizeof(text));
      const LinkStats& b = link(NODE_BELT);
      int extra = snprintf(text + n, sizeof(text) - n, "belt link: rx %lu dup %lu lost %lu late %lu\n",
                           static_cast<unsigned long>(b.received), static_cast<unsigned long>(b.duplicates),
                           static_cast<unsigned long>(b.lost), static_cast<unsigned long>(b.late));
      if (extra > 0) n += static_cast<size_t>(extra) < sizeof(text) - n ? extra : sizeof(text) - n - 1;
      send(make_packet(NODE_HUB, seq_++, io_.now_us(), KIND_LOG), reinterpret_cast<const uint8_t*>(text), n);
      break;
    }
    case CMD_STATS_RESET:
      stats_.reset();
      for (auto& l : links_) l = LinkStats();
      break;
    case CMD_MARKER: io_.marker_pulse(); break;
    case CMD_HIL:
      if (!hil(p.value[0], p.value[1])) {
        ack(KIND_CMD, p.seq, 2);
        return;
      }
      break;
    default: ack(KIND_CMD, p.seq, 1); return;
  }
  ack(KIND_CMD, p.seq, 0);
}

bool HubCore::hil(int16_t port, int16_t level) {
  uint32_t now = io_.now_us();
  PadReport r;
  if (port < 0) {
    hil_active_ = false;
    mapper_.release_all(now);
  } else {
    if (!has_profile_ || port >= kMaxPorts || !profile_.ports[port].present) return false;
    hil_active_ = true;
    hil_since_us_ = now;
    const PortDef& pd = profile_.ports[port];
    if (pd.type == PORT_BUTTON) {
      if (level && pd.output >= kFirstButton && pd.output <= kLastButton)
        r.buttons = 1u << (pd.output - kFirstButton);
    } else if (pd.type == PORT_TRIGGER) {
      float v = level / 1000.0f;
      (pd.output == OUT_L2 ? r.l2 : r.r2) = v;
    } else {
      static const float dir[5][2] = {{0, 0}, {0, 1}, {1, 0}, {0, -1}, {-1, 0}};
      int d = level >= 0 && level <= 4 ? level : 0;
      float* x = pd.output == OUT_RIGHT_STICK ? &r.rx : &r.lx;
      float* y = pd.output == OUT_RIGHT_STICK ? &r.ry : &r.ly;
      *x = dir[d][0];
      *y = dir[d][1];
    }
  }
  OutputFrame f = build_output(profile_, r, cal_);  // clamped like everything else
  io_.write_outputs(f);
  out_ = f;
  out_written_ = true;
  return true;
}

}  // namespace bp

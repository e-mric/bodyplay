#include "ports.h"

#include <string.h>

namespace bp {

namespace {

const PortWiring kWiring[kMaxPorts] = {
    {HW_STICK, 0, 0, 0},    // A-E1 left stick
    {HW_RELAY, 0, 0, 0},    // A-E2 cross
    {HW_RELAY, 0, 0, 1},    // A-E3 square
    {HW_RELAY, 0, 0, 2},    // A-E4 triangle
    {HW_STICK, 1, 0, 0},    // B-E1 right stick
    {HW_TRIGGER, 1, 2, 0},  // B-E2 R2
    {HW_TRIGGER, 1, 3, 0},  // B-E3 L2
    {HW_RELAY, 0, 0, 3},    // B-E4 circle
};

uint8_t hw_for(uint8_t type) {
  switch (type) {
    case PORT_STICK: return HW_STICK;
    case PORT_TRIGGER: return HW_TRIGGER;
    default: return HW_RELAY;
  }
}

}  // namespace

const PortWiring& port_wiring(uint8_t port) {
  static const PortWiring none;
  return port < kMaxPorts ? kWiring[port] : none;
}

bool OutputFrame::operator==(const OutputFrame& o) const {
  return memcmp(dac, o.dac, sizeof(dac)) == 0 && memcmp(relay, o.relay, sizeof(relay)) == 0;
}

int check_profile_fits(const ProfileBin& p) {
  for (uint8_t port = 0; port < kMaxPorts; ++port) {
    if (!p.ports[port].present) continue;
    if (hw_for(p.ports[port].type) != kWiring[port].hw) return port;
  }
  return -1;
}

OutputFrame build_output(const ProfileBin& p, const PadReport& r, const HubCalibration& cal) {
  OutputFrame f;
  // Idle levels first: every stick channel at neutral, triggers released.
  for (uint8_t port = 0; port < kMaxPorts; ++port) {
    const PortWiring& w = kWiring[port];
    if (w.hw == HW_STICK) {
      f.dac[w.dac][w.channel] = stick_code(0, cal.stick[port][0]);
      f.dac[w.dac][w.channel + 1] = stick_code(0, cal.stick[port][1]);
    } else if (w.hw == HW_TRIGGER) {
      f.dac[w.dac][w.channel] = trigger_code(0, cal.trigger[port]);
    }
  }
  // DAC A ch2/ch3 aren't wired: they stay at 0 V (zero-initialized), relays open.

  for (uint8_t port = 0; port < kMaxPorts; ++port) {
    if (!p.ports[port].present) continue;
    const PortWiring& w = kWiring[port];
    uint8_t out = p.ports[port].output;
    if (w.hw == HW_STICK) {
      float x = out == OUT_RIGHT_STICK ? r.rx : r.lx;
      float y = out == OUT_RIGHT_STICK ? r.ry : r.ly;
      if (cal.swap_xy[port]) {
        float t = x;
        x = y;
        y = t;
      }
      f.dac[w.dac][w.channel] = stick_code(x, cal.stick[port][0]);
      f.dac[w.dac][w.channel + 1] = stick_code(y, cal.stick[port][1]);
    } else if (w.hw == HW_TRIGGER) {
      float v = out == OUT_L2 ? r.l2 : out == OUT_R2 ? r.r2 : 0.0f;
      f.dac[w.dac][w.channel] = trigger_code(v, cal.trigger[port]);
    } else if (w.hw == HW_RELAY) {
      f.relay[w.relay] = out >= kFirstButton && out <= kLastButton && r.button(out);
    }
  }
  return f;
}

}  // namespace bp

#include "profile_bin.h"

#include <string.h>

#include "crc8.h"

namespace bp {

namespace {

struct Reader {
  const uint8_t* p;
  size_t len;
  size_t pos = 0;
  bool ok = true;

  uint8_t u8() {
    if (pos + 1 > len) {
      ok = false;
      return 0;
    }
    return p[pos++];
  }
  uint16_t u16() {
    uint16_t v = u8();
    v |= static_cast<uint16_t>(u8()) << 8;
    return v;
  }
  float f32() {
    if (pos + 4 > len) {
      ok = false;
      return 0;
    }
    float v;
    memcpy(&v, p + pos, 4);
    pos += 4;
    return v;
  }
};

}  // namespace

bool ProfileBin::parse(const uint8_t* data, size_t len, const char** error) {
  auto fail = [&](const char* why) {
    if (error) *error = why;
    return false;
  };
  if (len < 13 || len > kMaxProfileBytes) return fail("bad length");
  if (memcmp(data, "BPPF", 4) != 0) return fail("bad magic");
  if (crc8(data, len - 1) != data[len - 1]) return fail("bad crc");
  Reader r{data, len - 1, 4};
  ProfileBin out;
  if (r.u8() != kProfileVersion) return fail("unsupported version");
  uint8_t np = r.u8(), nports = r.u8(), nmodes = r.u8();
  out.hold_ms = r.u16();
  out.release_ms = r.u16();
  if (np > kMaxParams || nports > kMaxPorts || nmodes > kMaxModes) return fail("too many entries");
  if (out.hold_ms < 30 || out.release_ms < 30) return fail("press timing under 30 ms (hard rule 8)");
  out.n_params = np;
  for (int i = 0; i < np; ++i) {
    out.params[i].id = r.u8();
    out.params[i].value = r.f32();
  }
  for (int i = 0; i < nports; ++i) {
    uint8_t port = r.u8(), type = r.u8(), output = r.u8();
    if (port >= kMaxPorts || type > PORT_TRIGGER) return fail("bad port");
    out.ports[port] = {true, type, output};
  }
  out.n_modes = nmodes;
  for (int m = 0; m < nmodes; ++m) {
    ModeDef& md = out.modes[m];
    md.mask = r.u16();
    md.n = r.u8();
    if (md.n > kMaxBindings) return fail("too many bindings");
    for (int b = 0; b < md.n; ++b) {
      Binding& bd = md.bindings[b];
      bd.port = r.u8();
      bd.kind = r.u8();
      bd.a = r.u8();
      bd.b = r.u8();
      bd.cond = r.u8();
      bd.flags = r.u8();
      bd.max_hz = r.u8();
      if (bd.port >= kMaxPorts || !out.ports[bd.port].present || bd.kind > BIND_ANALOG)
        return fail("bad binding");
    }
  }
  if (!r.ok || r.pos != len - 1) return fail("truncated or trailing bytes");
  *this = out;
  return true;
}

const ModeDef* ProfileBin::mode(uint8_t game_mode) const {
  if (game_mode >= 16) return nullptr;
  for (int m = 0; m < n_modes; ++m)
    if (modes[m].mask & (1u << game_mode)) return &modes[m];
  return nullptr;
}

const Binding* ProfileBin::binding(uint8_t game_mode, uint8_t port) const {
  const ModeDef* md = mode(game_mode);
  if (!md) return nullptr;
  for (int b = 0; b < md->n; ++b)
    if (md->bindings[b].port == port) return &md->bindings[b];
  return nullptr;
}

}  // namespace bp

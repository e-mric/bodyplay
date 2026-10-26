#include "bptrace.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "protocol.h"

namespace bp {

namespace {

constexpr char kTraceMagic[8] = {'B', 'P', 'T', 'R', 'A', 'C', 'E', 1};
enum : uint8_t { REC_FRAME = 0, REC_LABEL = 1, REC_VIDEO = 2 };

size_t find_value(const std::string& json, const std::string& key) {
  std::string pat = "\"" + key + "\"";
  size_t k = json.find(pat);
  if (k == std::string::npos) return std::string::npos;
  size_t c = json.find(':', k + pat.size());
  if (c == std::string::npos) return std::string::npos;
  size_t v = c + 1;
  while (v < json.size() && (json[v] == ' ' || json[v] == '\t')) ++v;
  return v;
}

}  // namespace

double json_number(const std::string& json, const std::string& key, double fallback) {
  size_t v = find_value(json, key);
  if (v == std::string::npos) return fallback;
  char* end = nullptr;
  double d = strtod(json.c_str() + v, &end);
  return end == json.c_str() + v ? fallback : d;
}

std::string json_string(const std::string& json, const std::string& key) {
  size_t v = find_value(json, key);
  if (v == std::string::npos || json[v] != '"') return "";
  size_t e = json.find('"', v + 1);
  return e == std::string::npos ? "" : json.substr(v + 1, e - v - 1);
}

bool Trace::load(const std::string& path, std::string* error) {
  FILE* f = fopen(path.c_str(), "rb");
  if (!f) {
    if (error) *error = "can't open " + path;
    return false;
  }
  std::vector<uint8_t> buf;
  uint8_t chunk[65536];
  size_t n;
  while ((n = fread(chunk, 1, sizeof(chunk), f)) > 0) buf.insert(buf.end(), chunk, chunk + n);
  fclose(f);

  auto fail = [&](const char* why) {
    if (error) *error = path + ": " + why;
    return false;
  };
  if (buf.size() < 12 || memcmp(buf.data(), kTraceMagic, 8) != 0) return fail("not a .bptrace file");
  uint32_t hlen;
  memcpy(&hlen, buf.data() + 8, 4);
  if (12 + static_cast<size_t>(hlen) > buf.size()) return fail("truncated header");
  header_json.assign(reinterpret_cast<const char*>(buf.data() + 12), hlen);
  accel_lsb_g = static_cast<float>(json_number(header_json, "accel_lsb_g", accel_lsb_g));
  gyro_lsb_dps = static_cast<float>(json_number(header_json, "gyro_lsb_dps", gyro_lsb_dps));

  size_t pos = 12 + hlen;
  while (pos < buf.size()) {
    if (pos + 11 > buf.size()) return fail("truncated record header");
    uint8_t type = buf[pos + 8];
    uint16_t len;
    memcpy(&len, buf.data() + pos + 9, 2);
    pos += 11;
    if (pos + len > buf.size()) return fail("truncated record");
    const uint8_t* data = buf.data() + pos;
    pos += len;
    if (type == REC_FRAME) {
      Packet p;
      const uint8_t* payload;
      size_t payload_len;
      if (!unpack(data, len, &p, &payload, &payload_len) || p.kind != KIND_RAW) continue;
      size_t count = payload_len / sizeof(RawSample);
      uint32_t period = static_cast<uint16_t>(p.value[1]);
      for (size_t i = 0; i < count; ++i) {
        RawSample s;
        memcpy(&s, payload + i * sizeof(RawSample), sizeof(RawSample));
        TraceSample ts;
        ts.node_id = p.node_id;
        ts.t_us = p.t_sensor_us + static_cast<uint32_t>(i) * period;
        ts.accel_g = Vec3(s.accel[0] * accel_lsb_g, s.accel[1] * accel_lsb_g, s.accel[2] * accel_lsb_g);
        ts.gyro_dps = Vec3(s.gyro[0] * gyro_lsb_dps, s.gyro[1] * gyro_lsb_dps, s.gyro[2] * gyro_lsb_dps);
        ts.emg = s.emg;
        samples.push_back(ts);
      }
    } else if (type == REC_LABEL) {
      std::string js(reinterpret_cast<const char*>(data), len);
      labels.push_back({json_string(js, "event"), static_cast<uint32_t>(json_number(js, "t_us", 0))});
    }
  }
  return true;
}

std::vector<uint32_t> Trace::label_times(const std::string& event) const {
  std::vector<uint32_t> out;
  for (const auto& l : labels)
    if (l.event == event) out.push_back(l.t_us);
  return out;
}

}  // namespace bp

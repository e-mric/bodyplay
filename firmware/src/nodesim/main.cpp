// Host tool: a simulated belt node. Runs the real NodeCore and speaks the same
// framed protocol as the XIAO's CDC port, over stdin/stdout, while playing a
// .bptrace as its IMU. Lets `bodyctl device/record/dashboard --sim` and the
// pc tests exercise the device tools without hardware.
//
//   .pio/build/nodesim/program trace.bptrace [--realtime] [--loop] [--calib-ms 800]
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <chrono>
#include <string>
#include <thread>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#include <windows.h>
#else
#include <poll.h>
#include <unistd.h>
#endif

#include "bptrace.h"
#include "framing.h"
#include "nodecore.h"

using namespace bp;

namespace {

uint32_t g_now = 0;

class StdIo : public NodeIo {
 public:
  void send_frame(const uint8_t* wire, size_t len) override {
    fwrite(wire, 1, len, stdout);
    fflush(stdout);
  }
  void send_hid(const HidGamepad&) override {}  // mirrored as ST_PAD by NodeCore
  void marker_pulse() override {}
  void save_profile(const uint8_t*, size_t) override {}
  uint32_t now_us() override { return g_now; }
};

// Non-blocking read of whatever the PC sent.
size_t read_stdin(uint8_t* buf, size_t cap) {
#ifdef _WIN32
  HANDLE h = GetStdHandle(STD_INPUT_HANDLE);
  DWORD avail = 0;
  if (!PeekNamedPipe(h, nullptr, 0, nullptr, &avail, nullptr) || avail == 0) return 0;
  DWORD got = 0;
  ReadFile(h, buf, avail < cap ? avail : static_cast<DWORD>(cap), &got, nullptr);
  return got;
#else
  struct pollfd pfd = {0, POLLIN, 0};
  if (poll(&pfd, 1, 0) <= 0 || !(pfd.revents & POLLIN)) return 0;
  ssize_t n = read(0, buf, cap);
  return n > 0 ? static_cast<size_t>(n) : 0;
#endif
}

}  // namespace

int main(int argc, char** argv) {
#ifdef _WIN32
  _setmode(_fileno(stdout), _O_BINARY);
  _setmode(_fileno(stdin), _O_BINARY);
#endif
  const char* path = nullptr;
  bool realtime = false, loop = false;
  uint32_t calib_us = 800000;
  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], "--realtime") == 0) realtime = true;
    else if (strcmp(argv[i], "--loop") == 0) loop = true;
    else if (strcmp(argv[i], "--calib-ms") == 0 && i + 1 < argc) calib_us = atoi(argv[++i]) * 1000u;
    else path = argv[i];
  }
  Trace trace;
  std::string err;
  if (!path || !trace.load(path, &err) || trace.samples.empty()) {
    fprintf(stderr, "usage: %s trace.bptrace [--realtime] [--loop]\n%s\n", argv[0], err.c_str());
    return 2;
  }
  StdIo io;
  NodeCore core(io, NODE_BELT, "sim", AXIS_POS_X, calib_us);
  core.start_calibration();
  FrameDecoder dec;
  auto t0 = std::chrono::steady_clock::now();
  uint32_t offset = 0;  // keeps time increasing across loops
  const uint32_t span = trace.samples.back().t_us - trace.samples.front().t_us + 600;
  do {
    NodeSample burst[8];
    size_t n = 0;
    for (const auto& s : trace.samples) {
      NodeSample& ns = burst[n++];
      ns.t_us = s.t_us + offset;
      ns.accel_g = s.accel_g;
      ns.gyro_dps = s.gyro_dps;
      for (int i = 0; i < 3; ++i) {
        ns.raw.accel[i] = static_cast<int16_t>((&s.accel_g.x)[i] / trace.accel_lsb_g);
        ns.raw.gyro[i] = static_cast<int16_t>((&s.gyro_dps.x)[i] / trace.gyro_lsb_dps);
      }
      ns.raw.emg = 0;
      if (n < 8) continue;
      g_now = ns.t_us + 200;
      if (realtime) {
        std::this_thread::sleep_until(t0 + std::chrono::microseconds(g_now));
      }
      uint8_t rx[512];
      size_t got;
      while ((got = read_stdin(rx, sizeof(rx))) > 0)
        for (size_t i = 0; i < got; ++i)
          if (dec.feed(rx[i])) core.on_frame(dec.body(), dec.body_len());
      core.on_samples(burst, n);
      core.tick();
      n = 0;
    }
    offset += span;
  } while (loop);
  // Drain commands that arrived at the end (e.g. a final STATS request).
  for (int k = 0; k < 50; ++k) {
    uint8_t rx[512];
    size_t got = read_stdin(rx, sizeof(rx));
    for (size_t i = 0; i < got; ++i)
      if (dec.feed(rx[i])) core.on_frame(dec.body(), dec.body_len());
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  return 0;
}

# CLAUDE.md — Bodyplay

Play GTA on a PS5 with your body: run in place to run, dip and jump to jump, squeeze to shoot. Built tier by tier from €0 (webcam) to the full low-latency rig, and filmed as one YouTube video.

**Target:** every time-critical action (jump, sprint, shoot, punch, steer) reaches the PS5 **within 10 ms of the body's first tell** (median; p99 ≤ 15 ms) from Tier 2 onward.

## Read first

- `docs/IMPLEMENTATION_PLAN.md` — milestones M0–M9, tasks, acceptance criteria. Work one milestone at a time, in order, and tick tasks off in that file as you finish them.
- `docs/VIDEO_PLAN.md` — the companion video. Every tier must be demo-able and measurable on camera; check a milestone's "Video needs" list before calling it done.
- Design rationale (latency budget, Access controller pinout, BOM): the "Body-Controlled GTA 6: Ultra-Low-Latency Build Plan" doc, linked in `README.md`.

## Architecture by tier

| Tier | Cost | Path | Hot path latency target |
|---|---|---|---|
| 0 | €0 | Webcam → MediaPipe pose (Python) → gesture engine → virtual Xbox 360 pad (vgamepad + ViGEmBus) → chiaki-ng Remote Play → PS5 | Measure only |
| 1 | ~€30 | Belt node (XIAO ESP32S3 + LSM6DSOX) detects gestures **and is itself a USB HID gamepad** plugged into the PC (wired; +€8 for a second XIAO as wireless dongle) → chiaki-ng → PS5. No Python, no driver in the hot path | Measure only |
| 2 | ~€180 | Belt node → ESP-NOW → hub (ESP32-S3-DevKitC-1) → photoMOS relays + MCP4728 DAC → Access controller 3.5 mm ports → PS5 over USB | ≤ 10 ms median |
| 3 | ~€520 | Tier 2 + wrist nodes, MyoWare EMG, FSR foot mat, second Access controller, PC side path (voice, capture-card mode detection, QTE) | ≤ 10 ms median |

The DualSense stays paired with the Access controller(s) as one virtual controller and owns whatever the body doesn't (steering in vehicles, menus, crouch, reload…).

## Repo layout

```
firmware/              PlatformIO project (Arduino-ESP32 core 3.x)
  lib/protocol/        packet structs, COBS framing, CRC8 — shared by node + hub (+ Python mirror)
  lib/gestures/        IMU/EMG gesture state machines — pure C++, no Arduino deps, unit-tested natively
  lib/timesync/        node↔hub clock sync
  src/node/            XIAO ESP32S3 body node (belt / wrist), selects role at build time;
                       Tier 1: USB composite device = HID gamepad + CDC serial
  src/dongle/          XIAO ESP32S3 wireless dongle for Tier 1: ESP-NOW rx → USB HID gamepad
  src/hub/             ESP32-S3-DevKitC-1 hub: ESP-NOW rx, foot mat, mode machine, outputs
  test/                native unit tests (pio test -e native) incl. replay of recorded traces
pc/                    Python package `bodyctl` (uv)
  bodyctl/sensors/     webcam pose (Tier 0), serial node reader (Tier 1)
  bodyctl/gestures/    webcam-only gesture logic (Tier 0). IMU/EMG logic is NOT duplicated here
  bodyctl/output/      virtual gamepad backends (vgamepad on Windows, uinput, dry-run), device serial link
  bodyctl/config/      profile schema (pydantic) + compiler to hub binary format
  bodyctl/tools/       recorder, replay, latency_video analyzer, live dashboard
  tests/
config/profiles/       gta5.yaml, gta6.yaml (mode → gesture → port mappings)
ml/                    anticipation model training + export (M9)
hardware/              wiring notes, port pinout, BOM.csv, photos
docs/                  plans
```

## Dev and play machines

- **Development: macOS (Mac mini, Apple Silicon).** Code is written, built, unit-tested and flashed here: PlatformIO, uv, zsh. Serial ports look like `/dev/cu.usbmodem*`.
- **Play: Windows PC.** Runs chiaki-ng (Remote Play) and the PC side of Tiers 0–1 (`bodyctl webcam`, dashboard, recorder). Serial ports are `COMx`.
- So the Python package and tools must run on **both** macOS and Windows: use `pathlib`, no shell-specific scripts (write helpers in Python, not bash or PowerShell), serial ports passed as arguments.
- The `vgamepad` backend is Windows-only; on macOS use the `dry-run` backend for development and tests.
- From Tier 1 the node itself is a USB HID gamepad, so it works on either machine without drivers. Tier 2+ needs no PC in the hot path.

## Commands (create them in M0; keep this list current)

- Firmware: `pio run -e node_belt`, `pio run -e node_wrist`, `pio run -e dongle`, `pio run -e hub`, `pio run -e <env> -t upload`, `pio test -e native`
- PC: `uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run ruff format .` (the uv project is the root `pyproject.toml`; the package lives in `pc/bodyctl`)
- Run: `uv run bodyctl webcam` (Tier 0), `uv run bodyctl device push-profile --port COM5 config/profiles/gta5.yaml` (Tier 1 node/dongle and Tier 2+ hub; `/dev/cu.usbmodem*` on macOS), `uv run bodyctl dashboard --port COM5`
- Tools: `uv run bodyctl record`, `uv run bodyctl replay <trace>`, `uv run bodyctl latency-video <video.mp4>`
- Profiles and traces: `uv run bodyctl profile check config/profiles/gta5.yaml`, `uv run bodyctl synth -o data/synth`, `uv run bodyctl trace <file.bptrace>`
- `pio` on the Mac comes from the PlatformIO VS Code extension: `~/.platformio/penv/bin/pio` (see `docs/setup.md`)

## Hard rules (do not break; ask before changing)

1. **Never drive an Access controller expansion-port pin above 1.8 V.** DAC codes are clamped in firmware (max code derived from Vref, unit-tested), with a series resistor + Schottky clamp in hardware. Any change to hub output code must keep the clamp test passing.
2. **Never power anything from an expansion port.** The hub shares ground (Ring 2) only.
3. **Tier 2+: no PC in the hot path.** The PC may send mode, profile and QTE messages to the hub; it never relays gameplay-critical events.
4. **One owner per port per mode.** In vehicle modes, body-owned sticks are held at neutral. Each port emits one fixed PS5 button (set in the PS5's Access controller profile); modes change which gesture drives a port, never which button it sends.
5. **IMU/EMG gesture logic lives only in `firmware/lib/gestures`**, as pure C++ tested on recorded traces. Tier 1 runs it on the node, which also maps events to its own HID gamepad report from the pushed profile; the PC only configures, records and displays.
6. **Every packet carries `seq` and a sensor timestamp.** Never remove timing instrumentation; latency numbers are a deliverable.
7. **Costly actions** (shoot, exit vehicle) need high confidence plus a state gate (aiming, stopped/second cue).
8. **Virtual button presses:** ≥ 30 ms hold and ≥ 30 ms release, so a 30 fps game sees every press (mash cap ~15 Hz).
9. No timing-sensitive work in `loop()` polling: sample IMUs from FIFO/timer, no heap allocation or `Serial.print` in hot paths (use the log ring buffer).

## Conventions

- Firmware: C++17, PlatformIO, Arduino-ESP32 core 3.x (ESP-IDF APIs allowed where Arduino is too slow). `lib/` code must compile for `native`.
- Python: 3.11+, uv, ruff, pytest, type hints, pydantic for config.
- Units in identifiers: `_us`, `_ms`, `_hz`, `_g`, `_dps`, `_deg`.
- Thresholds are config, not constants: every gesture parameter lives in the profile YAML with a documented default.
- Recorded traces (`.bptrace`, see protocol) go in `data/` (git-ignored except small fixtures in `firmware/test/fixtures/`).
- `.gitattributes` enforces LF for source files so Windows checkouts don't break native tests or YAML.

## Open questions (verify, then record the answer here and in the plan)

- How the PS5 merges an Access controller stick and the DualSense stick mapped to the same function.
- Stick axis order on Tip vs Ring 1 (Sony's jack and plug tables disagree).
- Access controller → PS5 latency over USB (measure in M4).
- Whether Remote Play input coexists with local controllers (affects the side path).
- Tier 0 on Windows: `vgamepad` needs ViGEmBus, archived upstream since 2023 but still installable; confirm chiaki-ng sees the virtual Xbox 360 pad in M2.
- Tier 1: whether chiaki-ng (SDL) recognizes the node's HID gamepad out of the box, or needs an `SDL_GAMECONTROLLERCONFIG` mapping string (M3).
- GTA 6 controls and frame rate (known on 2026-11-19). Until then, develop and test on GTA V (PS5, Performance mode) with `config/profiles/gta5.yaml`.

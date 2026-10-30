# Implementation plan

Build Bodyplay in ten milestones, each ending in something you can play and measure on camera. Tiers 0–1 run through the PC and Remote Play; from Tier 2 the PC leaves the hot path and the hub drives the PlayStation Access controller directly.

Dates assume parts ordered on 2026-10-24 and GTA 6 launching 2026-11-19. Prices, pinout and latency rationale: see the design doc linked in `README.md`.

## Success criteria per tier

| Tier | Milestones | Must work | Latency target (tell → PS5 input) | Exercise check |
|---|---|---|---|---|
| 0 (€0) | M1–M2 | Run in place, jump, punch, aim in GTA V via Remote Play | Measured and reported, no target | 20 min session playable |
| 1 (~€30) | M3 | Same, from the belt IMU; lean and twist added | Measured; expect clearly better than Tier 0 | Steps counted per session |
| 2 (~€180) | M4–M5 | Move, sprint, jump, enter/exit through Access controller A, belt node wireless | Median ≤ 10 ms, p99 ≤ 15 ms | Full GTA V mission on foot |
| 3 (~€520) | M6–M7 | Shoot (EMG), punch, aim, all vehicle modes with the DualSense, foot mat, QTE | Same; < 1 false costly action / 10 min | 60 min session |

## Timeline

| Milestone | Dates (2026) | Depends on |
|---|---|---|
| M0 Repo and foundations | Oct 25 | — |
| M1 Latency toolkit | Oct 25–27 | M0 |
| M2 Tier 0: webcam | Oct 25–Nov 1 | M0 |
| M3 Tier 1: belt node via PC | Oct 29–Nov 3 | XIAO + IMU delivered |
| M4 Tier 2: hub + Access controller A | Oct 31–Nov 7 | Access controller, hub parts |
| M5 Tier 2: wireless belt node | Nov 6–10 | M3, M4 |
| M6 Tier 3: wrists, EMG, controller B, vehicles | Nov 10–18 | M5, MyoWare |
| M7 Tier 3 extras: foot mat, QTE, side path | Nov 14–18 (can slip past launch) | M5 |
| M8 GTA 6 launch profile | Nov 19–21 | GTA 6 |
| M9 Anticipation model | Nov 20–Dec 6 | Recorded sessions from M3+ |

---

## Test game: GTA V on PS5

All tiers are developed and filmed on GTA V before GTA 6 launches.

- **Graphics mode: Performance** (targets 60 fps; Fidelity targets 30 fps) for the lowest game-side latency. Note the mode in every latency result, and film one comparison in Fidelity for the video.
- **Controls: default layout** ([reference](https://www.gtaboom.com/gta-5-controls-pc-keyboard-playstation-and-xbox/)): left stick move, right stick camera, X tap to sprint, Square jump, Circle light melee and reload, R2 fire, L2 aim, R1 cover, L3 stealth, Triangle enter/exit, L1 weapon wheel, D-pad up phone. Vehicles: R2 accelerate, L2 brake, R1 handbrake, L3 horn. Aircraft: R2/L2 throttle, L1/R1 yaw, left stick pitch and roll.
- **Story mode only** for testing; keep GTA Online out of it until the rig is stable.
- A short, repeatable test route in story mode (e.g. a run from a safehouse to a fixed landmark) for comparable exercise numbers per tier.

## Shared foundations

### Packet format (`firmware/lib/protocol`, mirrored in `pc/bodyctl/protocol.py`)

One 16-byte packet for events and continuous state, on ESP-NOW and USB serial alike:

```c
struct __attribute__((packed)) Packet {   // 16 bytes
  uint8_t  magic;        // 0xB0
  uint8_t  node_id;      // 1 belt, 2 wrist_r, 3 wrist_l, 10 hub, 20 pc
  uint16_t seq;          // per node, wraps
  uint32_t t_sensor_us;  // node clock at the sample that triggered this packet (hub-synced)
  uint8_t  kind;         // EVENT, STATE, SYNC, MODE, PROFILE_CHUNK, LOG
  uint8_t  code;         // action id for EVENT (JUMP, SPRINT_START, PUNCH, SHOOT, ...), field id for STATE
  uint8_t  confidence;   // 0–255
  uint8_t  flags;        // DUPLICATE, PREDICTED, ...
  int16_t  value[2];     // continuous values, fixed-point (cadence ×100, lean_deg ×100, ...)
};
```

- **Serial framing:** COBS + CRC8, `0x00` delimiter. Same packet bytes inside.
- **Events** go out immediately, sent twice ~300 µs apart; receivers drop duplicates by `(node_id, seq)`.
- **State** (stick values, cadence, lean, yaw rate) streams at 250 Hz.
- **Version** the format with a `HELLO` packet at boot; Python tests round-trip every kind.

### Time sync (`firmware/lib/timesync`)

- The hub broadcasts `SYNC` with its clock every 100 ms; nodes keep the minimum observed offset over a 2 s window.
- An occasional ping/pong measures round trip, so the offset can be corrected by half the RTT.
- Target: node clocks within ±0.5 ms of the hub, so `hub_receive_us − t_sensor_us` gives link latency per packet.
- Tier 1: the PC plays the hub's role over serial.

### Trace format (`.bptrace`)

A recording session = header (JSON: date, profile, node firmware versions, sensor rates) + a stream of packets plus raw sensor frames (`kind = RAW`, IMU 6 × int16 + EMG int16 at full rate). The same file feeds unit tests (replay through `lib/gestures` natively), the dashboard and ML training.

### Profile config (`config/profiles/*.yaml`)

The starter profile is `config/profiles/gta5.yaml` (GTA V on PS5). Key ideas:

- **Each port emits one fixed PS5 button**, set once in the PS5's Access controller profile. Modes only change *which gesture drives a port*, never which button it sends. Vehicle-only buttons (handbrake R1, horn L3, aircraft yaw L1/R1, landing gear L3) therefore stay on the DualSense.
- **Controller A = locomotion** (left stick, X, Square, Triangle), so the one-controller Tier 2 start can move, sprint, jump and enter vehicles. **Controller B = camera and combat** (right stick, R2, L2, Circle).
- **GTA V sprints by tapping X repeatedly**, so sprint is a pulse train on A-E2 synced to your steps once cadence passes the sprint threshold, not a held button.
- GTA V conveniently reuses R2 (fire / accelerate / aircraft throttle up) and L2 (aim / brake / throttle down), so the body-driven triggers work in every mode.

The PC validates profiles (pydantic), compiles them to a compact binary and pushes them to the hub (`PROFILE_CHUNK` packets). Tier 0–1 use the same file to map events to virtual gamepad controls.

### Getting inputs into chiaki-ng on Windows (Tiers 0–1)

Development happens on the Mac; chiaki-ng and the Tier 0–1 PC side run on the Windows play PC. Two routes, one per tier:

- **Tier 0: `vgamepad` (Python) → ViGEmBus virtual Xbox 360 pad.** ViGEmBus has been archived upstream since 2023, but its signed installer is still published and `vgamepad` installs it. chiaki-ng sees an ordinary XInput controller. Wrapped in a `VirtualGamepad` interface (`set_axis()`, `press()/release()`, a press scheduler enforcing the 30 ms hold / 30 ms release rule) with `vgamepad`, `uinput` (Linux, optional) and `dry-run` backends.
- **Tier 1: the node is the gamepad.** The XIAO ESP32S3 has native USB, so it enumerates as a composite device: **HID gamepad** (what chiaki-ng reads) + **CDC serial** (profile push, recording, dashboard). No Python and no driver in the hot path, which also makes Tier 1 a cleaner latency story. Wired by default (a 3 m USB-C cable to the belt is fine for running in place); for wireless, a second XIAO plugged into the PC acts as an ESP-NOW → HID dongle (+€8).
- **Fallback if SDL doesn't map the HID gamepad:** set `SDL_GAMECONTROLLERCONFIG` from `pc/bodyctl/output/sdl_mapping.txt` before launching chiaki-ng (`bodyctl launch-chiaki` does this), or route node events over CDC serial into the Tier 0 `vgamepad` path.

---

## M0 — Repo and foundations (Oct 25)

- [x] PlatformIO project with envs `node_belt`, `node_wrist`, `dongle`, `hub`, `native`; Arduino-ESP32 core 3.x pinned. Native tests must build on macOS (dev) and Windows (play PC).
- [x] `pc/` uv project with ruff, pytest, pydantic; CLI entry point `bodyctl` (Typer or argparse). Serial ports passed as arguments (`COMx` on Windows, `/dev/cu.usbmodem*` on macOS).
- [x] `.gitattributes` forcing LF on source, YAML and fixtures.
- [x] `docs/setup.md`: macOS dev setup (VS Code + PlatformIO, uv, flashing the XIAO) and Windows play-PC setup (uv, chiaki-ng install and PS5 pairing, ViGEmBus install, running `bodyctl` from a git checkout).
- [x] `lib/protocol` + Python mirror, COBS/CRC8, round-trip tests on both sides.
- [x] Profile schema (pydantic) that validates the starter `config/profiles/gta5.yaml`; the schema may reshape the file, but keep its port → PS5 button table.
- [ ] CI (GitHub Actions, `macos-latest` + `windows-latest`): `pio run` all envs, `pio test -e native`, `uv run pytest`, `ruff`.
    - Oct 26: fixed the Windows dependency-install hang by excluding the interactive gamepad driver in CI. Python jobs now install PlatformIO so simulator/replay tests run instead of skipping. Both Python jobs pass; the full matrix is still being verified.
- [x] `hardware/access_port_pinout.md` copied from the design doc, including the axis-order warning.
- [x] **Synthetic trace generator** (`pc/bodyctl/tools/synth.py`): writes `.bptrace` files of fake IMU signals (standing still, weight shift, clean jump, jump after a long dip, run start, steady run at 1.5/2.5/3.5 steps/s, punch, noisy versions with drift and strap wobble). Lets gesture code and tests start before hardware arrives; replaced by real fixtures in M3 but kept as regression tests.

**Acceptance:** CI green on an empty-but-wired skeleton; `bodyctl --help` lists the planned commands.

## M1 — Latency toolkit (Oct 25–27)

Every video chapter shows a latency number, so this comes before anything else is "done".

- [ ] **Output marker LED:** whatever produces the input (PC in Tier 0, the node in Tier 1, the hub in Tier 2+) lights a bright LED for 20 ms on every output change. Tier 0–1: a USB-serial-driven LED on any spare microcontroller, or the screen corner flash in the dashboard as fallback.
    - Done in code: `firmware/src/marker` (XIAO, `CMD_MARKER` over CDC → 20 ms pulse via `esp_timer`), `MarkerPulse` in `lib/latency`, `bodyctl.output.marker`. To do: flash and check on a XIAO; node and hub hooks land with M3/M4.
- [ ] **Body marker:** a strip of bright tape on the belt; the analyzer tracks its vertical motion to find movement onset.
- [x] `bodyctl latency-video <file>`: OpenCV; user draws three ROIs (body marker, LED, TV region); detects onset frames (intensity/position change > k·σ of a 0.5 s baseline); outputs per-trial CSV (tell→LED, LED→screen, tell→screen in ms) and a histogram PNG.
- [x] Baseline protocol: the same analyzer on DualSense button presses (thumb marker ROI) gives the reference every tier is compared against.
- [ ] Firmware/PC latency log: per-stage histograms (sample→detect, detect→send, send→receive, receive→output) printed on demand.
    - Done in code: `lib/latency` (`LatencyStats`, native-tested), `CMD_STATS`, `bodyctl.tools.stats.StageStats` for the PC pipeline. To do: wire into node/hub firmware (M3/M4).

**Acceptance:** analyzer reproduces hand-counted frame numbers within ±1 frame on a 20-trial test clip at 240 fps.

*Status:* passes on a generated 20-trial 240 fps clip (`pc/tests/test_latency_video.py`); the real-clip check needs the rig. How-to: `docs/measuring.md`.

**Video needs:** the measurement rig shown once in the intro; the same framing reused in every chapter.

## M2 — Tier 0: webcam (Oct 25–Nov 1)

**First user test (Oct 26):** GTA V is installed directly on the Windows PC; a phone can supply the webcam. Follow `docs/tier0_windows.md` to test the dry-run dashboard, virtual controller, then native PC gameplay. This is a preliminary Tier 0 check; keep PC latency results separate and leave PS5/chiaki-ng acceptance unchecked. The actual MediaPipe lite model loads and processes blank frames on the Mac; real-person camera detection still needs this test.

- [ ] `sensors/webcam_pose.py`: OpenCV capture at the camera's max fps (prefer 60), MediaPipe Pose landmarks, timestamps per frame.
    - Done in code (MediaPipe 1.x Tasks API, `PoseLandmarker` in VIDEO mode, model fetched to `data/models/` on first run). To do: first run with a real webcam on the Windows PC (the Mac mini has no camera).
- [x] `gestures/webcam.py` (Tier 0 only; tested on synthetic poses in `pc/tests/posegen.py`, thresholds in the profile's `gestures.webcam`):
    - Run: alternating knee-height peaks → cadence → stick Y through the profile's curve.
    - Jump: hip landmark dips below standing baseline then accelerates upward → fire on reversal.
    - Punch: wrist-to-shoulder distance grows fast with the elbow extending.
    - Aim: forearm raised toward the camera above a height threshold; held = trigger held.
    - Lean: shoulder-line angle → stick X.
- [ ] Install ViGEmBus via `vgamepad`; confirm chiaki-ng lists the virtual Xbox 360 pad and that it drives the PS5 (record the answer in `CLAUDE.md`).
- [ ] Map to the virtual gamepad via the profile; run with chiaki-ng on GTA V.
    - Done in code: `bodyctl.mapping.BindingEngine`, `bodyctl.output.gamepad` (press scheduler for hard rule 8, `vgamepad` and `dry-run` backends), `bodyctl webcam`. To do: run on the Windows PC with chiaki-ng.
- [x] Dashboard: skeleton overlay, live events, cadence, confidence bars (built into `bodyctl webcam`; corner flash doubles as the fallback output marker; keys: m mode, p pause, r reset stats).
- [ ] Measure with M1: 20 trials each for jump and run start.
- [ ] Tune Tier 0 thresholds on real drills: `bodyctl webcam-drill` (cued reps per move, camera-placement guide, several angles) and `bodyctl webcam-tune` (grid search per gesture group, two passes, maximising the mean F1 over moves; writes a local tuned profile).
    - Done in code and tested on synthetic poses (`pc/tests/test_drill.py`). To do: record every move at front, right30 and left30 on the Windows PC; move values that hold across angles into `gta5.yaml`.
- [x] Pose model choice: MediaPipe lite/full/heavy, RTMPose-m/x and ViTPose-b/l compared on one recorded drill (left30, 36 cues). RTMPose-m won: 30/36 cues vs 20/36 for MediaPipe, knees tracked in 98 % of frames, ~4.5 ms on an RTX 5080. Added as `--pose rtmpose` (default when CUDA is available), with One Euro landmark smoothing tuned by `webcam-tune`.
- [ ] Tier 0 extras, done in code and tested on synthetic poses (`pc/tests/test_shoot_wheel_cover.py`), not yet tried in GTA: walk / jog / sprint drill with a tuned `cadence_curve`; shoot by recoil flick while aiming (aim survives the flick); weapon wheel (right hand over the shoulder holds L1, left hand points the right stick); crouch / stand taps R1 for cover; flight in plane / heli mode (arms spread: tilt rolls, hand height pitches; rule 4 exception approved 2026-10-29). Wheel, cover and flight live in the profile's `tier0` section: Tier 2 has no free port, so the DualSense keeps them there (and flies).

**Acceptance:** 20-minute GTA V session on foot using only the webcam; latency CSV saved for the video.

**Video needs:** screen recording of the dashboard + game side by side; the "late jump" clip; latency histogram.

## M3 — Tier 1: belt node via PC (Oct 29–Nov 3)

- [ ] Node firmware (`src/node`, role `belt`): LSM6DSOX on I2C at 1 MHz, ODR 1.66 kHz, FIFO read on watermark interrupt; gravity alignment during a 2 s standing calibration.
    - Done in code and building: `src/node/main.cpp` (I/O only) + `lib/nodecore` (calibration → gestures → mapper → HID + CDC, native-tested end to end), `lib/imu` (register setup, FIFO parser). Wiring: `hardware/wiring_tier1.md`. To do: flash a XIAO with the IMU and check it on the dashboard.
- [x] `lib/gestures` (pure C++, native-tested on synthetic fixtures; real-trace tuning comes with the labeled fixtures below):
    - Step detector: vertical acceleration peaks with refractory period → cadence (EMA of intervals); stop when no step for 1.5× the last interval.
    - Jump: counter-movement dip (below `dip_g` for ≥ `dip_min_ms`) followed by upward reversal with jerk > `push_jerk` → `JUMP`. A dip held > 300 ms without reversal → `CROUCH`.
    - Lean: roll from a complementary filter → deadzone/full-scale mapping.
    - Twist: gyro yaw integrated relative to a neutral that re-zeroes when the user stands still facing the TV for 1 s (counters drift).
- [x] USB composite device on the node (TinyUSB via Arduino-ESP32 `USBHIDGamepad` + CDC): the HID report is built on the node from gesture events and the pushed profile; CDC carries packets, RAW stream and profile chunks. HID poll interval 1 ms.
- [x] `bodyctl device push-profile` and `bodyctl dashboard` over CDC (`output/device.py`; `--sim trace.bptrace` runs the real node logic without hardware). Also `device info|stats|calibrate|mode|marker`.
- [ ] Check chiaki-ng picks up the HID gamepad; if not, generate the SDL mapping string and `bodyctl launch-chiaki`. Record the answer in `CLAUDE.md`.
    - Done in code: `bodyctl launch-chiaki` (+ `--print`), mapping in `pc/bodyctl/output/sdl_mapping.py`. To do: try on the Windows PC (axis indices are a best guess).
- [ ] Optional wireless: `src/dongle` on a second XIAO (ESP-NOW rx → same HID report code), belt node sends over ESP-NOW. Shares code with M5.
- [x] `bodyctl record` / `bodyctl replay`: capture `.bptrace`; replay through the native gesture lib (firmware env `replay`), scored against labels. Live labels from dashboard keys (`docs/tier1.md`); `bodyctl plot` draws the jump-tell graphic. Video-frame timestamps: not yet (the latency rig uses the marker LED instead).
- [ ] Labeled fixtures: 50 jumps, 50 run starts, 20 crouches, 20 "just shifting weight" → native tests asserting detection rate and false-positive rate.
- [ ] **Feint drills** recorded alongside: 50 dips without jumping, 30 run starts that stop after one step, 30 weight shifts while standing. These are the hardest negatives and the most valuable fixtures.
- [ ] Measure with M1.

**Acceptance:** jump detection ≥ 95 % with ≤ 1 false jump per 10 min of normal play on replayed traces; GTA V playable through chiaki-ng with the node as the only input device; Tier 1 latency measured and better than Tier 0.

**Video needs:** close-up of the belt node; trace plot of a jump showing the dip, the reversal and where the event fires (the "boxer tell" graphic: `uv run bodyctl plot <recording> --event jump`).

## M4 — Tier 2: hub + Access controller A (Oct 31–Nov 7)

**Wiring (hub = ESP32-S3-DevKitC-1):** pins, channel table and bring-up steps in `hardware/wiring_tier2.md` (to build when the parts arrive).

- [ ] Two I2C buses (both MCP4728 boards default to address 0x60): bus 0 → DAC A (controller A: left stick, 2 channels), bus 1 → DAC B (controller B, M6: right stick + R2 + L2, 4 channels). 400 kHz–1 MHz.
- [x] DAC config (firmware): internal Vref 2.048 V, gain 1, multi-write only (never VDD-referenced fast write). **Hard clamp: max code = floor(1.75 / 2.048 × 4095) = 3499** (0.05 V margin under 1.8 V). Stick neutral expected near Vdd/2 ≈ 0.9 V (code ≈ 1800); confirm against the spec's stick table and calibrate against what the PS5 shows. Clamp in `lib/hubout/dac.h`, tested exhaustively (`test_hubout`).
- [ ] Each analog output: DAC → 1 kΩ series resistor → port pin, Schottky clamp to the port's Vdd (Sleeve), ground to Ring 2.
- [ ] Buttons: GPIO → 330 Ω → AQY212EH LED; relay output across the button contacts given by the spec's button-device circuit (Tip and Sleeve per the pin table; verify on the figure). Operate time is under 1 ms (~0.65 ms datasheet class).
- [ ] Spare GPIO → marker LED for M1.
- [x] **Bring-up checklist written** (`hardware/wiring_tier2.md`); to do on the bench: multimeter on every output at code 0, neutral, max; scope or LED test of every relay; confirm no voltage on ports from the hub side when powered off.

**Firmware (`src/hub`):**

- [x] Output layer enforcing the clamp and the 30/30 ms rule: `lib/hubout` (DAC codes, port wiring, `build_output`) on top of the shared `PadMapper`/`PressScheduler`; native tests for the clamp, scheduler and ports.
- [x] Profile receiver (from PC over USB serial), stored in NVS (built-in GTA V profile until one is pushed); mode state machine (`on_foot`, `car`, `bike`, `boat`, `heli`, `plane`, `swim`, `qte`, `paused`), ownership rules. Local enter/exit switches mode after the Triangle press is released; stale body state (> 200 ms) drops the sticks to neutral and cannot satisfy the exit-vehicle stopped gate; hub events must pass the profile confidence threshold (shoot/enter-exit thresholds are included in compiled profiles); PC events are ignored (hard rule 3). `lib/hubcore`, native-tested with a NodeCore tethered to it.
- [x] Temporary input: belt node wired to the hub over UART (tethered) so M4 doesn't depend on ESP-NOW (`pio run -e node_belt_tether`; node syncs to the hub clock, events + 250 Hz `ST_BODY`).
- [x] PS5 setup notes in `hardware/ps5_setup.md`: Access controller profile mapping each port, USB communication mode, pairing with the DualSense as one virtual controller (menu names to verify on the console).
- [ ] Test the open questions: Access stick + DualSense stick on the same function; axis order; record answers in `CLAUDE.md`. Tool ready: `uv run bodyctl hil --port <hub>`.
- [ ] Measure: tell → LED, LED → screen; compare with the DualSense baseline.

**Acceptance:** median tell→LED ≤ 10 ms, p99 ≤ 15 ms over 50 trials (tethered); a GTA V on-foot mission playable with body + DualSense.

**Video needs:** the "PC leaves the loop" diagram; multimeter safety moment; first jump through the Access controller in slow motion next to Tier 0.

## M5 — Tier 2: wireless belt node (Nov 6–10)

- [ ] ESP-NOW: Wi-Fi STA mode without association, fixed channel, power save off, unicast to the hub MAC, higher PHY rate (`esp_wifi_config_espnow_rate`), events sent twice with dedup.
- [ ] Time sync running; per-packet link latency logged.
- [ ] Link health: lost/duplicate/late (> 15 ms) packet counters, shown in the dashboard via the hub's serial log.
- [ ] Battery: LiPo on the XIAO, voltage reported in `STATE` packets every second.
- [ ] Fallback: keep the UART tether path selectable at build time.

**Acceptance:** 10 min of play with p99 link latency ≤ 5 ms and no late packet > 15 ms; otherwise ship tethered and note it in the video.

**Video needs:** the wireless setup on body; link-latency histogram vs tethered.

## M6 — Tier 3: wrists, EMG, controller B, vehicles (Nov 10–18)

- [ ] Node role `wrist`: LSM6DSOX + MyoWare (Link Shield TRS: Tip = signal, Ring = VIN 3.3 V, Sleeve = GND) on the right wrist; IMU only on the left.
- [ ] Gestures: aim (forearm pitch past threshold, held), shoot (EMG envelope > k × baseline while aiming), punch (forward acceleration onset), flick (yaw-rate spike), air steering wheel (both wrists' roll difference) for boats/fun.
- [ ] EMG calibration routine: 3 s relaxed baseline, 3 squeezes; stored per session.
- [ ] Controller B on DAC B + relays; port map from the profile.
- [ ] Vehicle profiles: car, bike, boat, heli, plane per the design doc's ownership table (body throttles, DualSense steers/flies). Enter/exit gesture switches mode locally.
- [ ] False-trigger log: 10 min per mode, every costly action reviewed against video.

**Acceptance:** shoot median ≤ 10 ms from EMG onset; < 1 false costly action per 10 min; one full GTA V session using every vehicle type.

**Video needs:** EMG signal rising before the finger visibly moves (graph + slow-mo); driving with legs as throttle; helicopter segment.

## M7 — Tier 3 extras (Nov 14–18, may slip past launch)

- [ ] Foot mat: 4 × FSR 406 on hub ADC (two per foot), threshold + hysteresis, interrupt-like fast polling at ≥ 2 kHz; step timing replaces IMU cadence when present; stomp action.
- [ ] QTE mode: fast feet → mash presses on the prompted port (capped ~15 Hz); any sharp gesture → press the prompted button.
- [ ] **Paused mode** for cutscenes, menus and the phone: all body outputs released and sticks at neutral, so you can stand and breathe without sending inputs. Entered by voice ("pause") or a held pose (both arms crossed for 1 s), and automatically by the capture-card classifier if built.
- [ ] **Parachute and climbing:** add to `gta5.yaml` after checking GTA V's controls in-game (deploy and steer the parachute likely stay on the DualSense; arms-out pose could drive descent).
- [ ] Capture-card classifier labels: on_foot, swim, car, bike, boat, heli, plane, parachute, qte_prompt, cutscene, menu. Train only on your own PS5 captures (same HUD and settings), recorded during normal play sessions.
- [ ] PC side path (optional): capture-card classifier for on-foot / vehicle type / QTE prompt; offline voice commands for menus and mode override; both send `MODE` packets only.

**Acceptance:** a mashing QTE in GTA V cleared with fast feet; mode auto-detection correct ≥ 95 % of sampled frames (if built).

**Video needs:** QTE "10-second sprint" moment.

## M8 — GTA 6 launch profile (Nov 19–21)

- [ ] Write `config/profiles/gta6.yaml` from the real control scheme; update PS5 Access controller profiles.
- [ ] Re-measure frame rate and latency (performance mode if available); retune the mash cap if the game runs at 60 fps.
- [ ] First full session recorded for the video's final chapter.

**Acceptance:** first GTA 6 mission played with the full rig.

## M9 — Anticipation model (Nov 20–Dec 6)

- [ ] Label automatically from traces: each detected/confirmed action marks the preceding 150 ms window as positive "action within 100 ms".
- [ ] Training data = your own sessions only: normal GTA V play (realistic negatives), drill sessions (~50 per action) and feint drills (~50 per action). Also record tired, end-of-session movement and different shoes/floor. No simulated or third-party data for the first model.
- [ ] Optional experiment if data is short: pretrain on synthetic "virtual IMU" data derived from public motion-capture libraries (e.g. AMASS), then fine-tune on your own traces.
- [ ] Train a small 1D CNN per action on IMU (+EMG) windows (`ml/train.py`), evaluate lead time vs false positives, export int8 (ESP-DL or TFLite Micro).
- [ ] On-node inference with the state machine as fallback and sanity gate; measure inference time on the ESP32-S3.
- [ ] Compare lead time and false-positive rate against the hand-tuned state machines.

**Acceptance:** fires ≥ 30 ms earlier than the state machine on jump and punch at the same false-positive rate, or is left disabled.

**Video needs:** optional follow-up video ("I taught it to read my moves").

---

## Testing strategy

- **Unit (native):** protocol, clamp math, press scheduler, mode/ownership rules, every gesture state machine on fixture traces.
- **Replay:** any recorded session can be replayed through current firmware logic; regressions in detection rate or false positives fail CI on the fixtures.
- **Hardware-in-the-loop (manual, scripted):** `bodyctl hil` toggles each port and asks you to confirm what the PS5 shows.
- **Latency acceptance:** M1 analyzer on 50 trials per action per tier; results committed to `data/results/<tier>.csv` (small files) for the video.

## Risks

| Risk | Mitigation |
|---|---|
| ESP-NOW latency spikes | Unicast, fixed channel, duplicates, tether fallback (M5) |
| Yaw drift on torso twist | Auto re-zero when still facing the TV; camera fallback |
| EMG noise from sweat and movement | Aim-state gate, calibration per session, wrist-flick fallback |
| Access/DualSense stick merging misbehaves | Body sticks held neutral in vehicle modes; test in M4 |
| GTA 6 controls differ from GTA V | Everything mapped in profiles; M8 is a config change |
| Parts late | Tiers 0–1 don't need the Access controller; video can be filmed in tier order as parts arrive |

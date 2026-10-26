# Tier 1: belt node

The XIAO on your belt detects steps, jumps, lean and twist itself, and plugs into the PC as a **USB gamepad**. chiaki-ng reads that gamepad like any controller, so no Python and no driver sit in the hot path. The PC is only used to configure, record and watch.

Wiring and mounting: `hardware/wiring_tier1.md`.

## 1. Flash (Mac)

```bash
cd firmware
pio run -e node_belt -t upload
```

The node enumerates as **"Bodyplay Belt"**: a HID gamepad and a serial port (`/dev/cu.usbmodem*` on macOS, `COMx` on Windows). It boots with the GTA V profile built in (`firmware/lib/padmap/default_profile.h`).

## 2. Calibrate

On every boot, and with `bodyctl device calibrate`: **stand still facing the TV for 2 s**. Until then the pad stays neutral. The dashboard shows "calibrated" when it's done.

## 3. Check it on the dashboard

```bash
uv run bodyctl dashboard --port COM5
```

You'll see your vertical acceleration scrolling, every event (jump, crouch, run start…) with its confidence, cadence/lean/twist, and the exact gamepad report the node is sending. Keys: `m` switches mode (on foot → car → heli → swim → QTE → paused), `c` recalibrates, `r` records.

No hardware yet? Everything works against the **simulated node** (the real firmware logic playing a trace):

```bash
uv run bodyctl dashboard --sim firmware/test/fixtures/synth_run_start.bptrace
```

## 4. Push a profile after editing it

```bash
uv run bodyctl profile check config/profiles/gta5.yaml
uv run bodyctl device push-profile --port COM5 config/profiles/gta5.yaml
```

The node stores it in flash and uses it from then on (thresholds included: no reflash to retune).

## 5. Play through chiaki-ng (Windows)

```bash
uv run bodyctl launch-chiaki
```

This starts chiaki-ng with an `SDL_GAMECONTROLLERCONFIG` mapping for the node (`pc/bodyctl/output/sdl_mapping.py`). First check whether chiaki-ng sees "Bodyplay Belt" **without** it (plain launch); record the answer in `CLAUDE.md` (open question). If a stick or trigger comes out swapped, adjust `AXES` in `sdl_mapping.py` and run `uv run bodyctl launch-chiaki --print` to see the string.

Keep the DualSense connected: it still owns what the body doesn't (crouch, cover, reload, weapon wheel, menus).

## 6. Record drills → test fixtures

The detector is only as good as the traces it's tested on. In the dashboard, press `r` to record, then label each rep as you do it:

| Key | Label | Drill |
|---|---|---|
| `j` | jump | 50 jumps, standing and from a run |
| `s` | run_start | 50 run starts from standing |
| `k` | squat_hold | 20 crouches (dip and hold) |
| `f` | feint | 50 dips without jumping, 30 one-step false starts, 30 weight shifts |
| space | mark | anything worth finding later |

Press `r` again to stop; files land in `data/sessions/`. Score any recording against its labels with the current firmware logic:

```bash
uv run bodyctl replay data/sessions/session_20261029_190000.bptrace
uv run bodyctl replay data/sessions/*.bptrace --set jump.push_jerk=10
```

Good recordings (cut to the interesting part) move into `firmware/test/fixtures/` as native regression tests. Headless recording: `uv run bodyctl record --port COM5 --tag jump_drill --seconds 120`.

## 7. Measure

Same rig as every tier (`docs/measuring.md`): the node's marker LED on D0 is the "output" light. Also read the node's own per-stage numbers:

```bash
uv run bodyctl device stats --port COM5
```

## Troubleshooting

- **Marker LED blinking at boot:** no IMU on I2C. Check SDA/SCL, then try `-DBP_I2C_HZ=400000` in `platformio.ini`.
- **Nothing happens when you move:** not calibrated (stand still 2 s), or the profile's mode is `paused` (`bodyctl device mode on_foot`).
- **Jumps fire when running:** record it, replay with `--events`, and raise `jump.running_dip_min_ms` or lower `jump.freefall_g`.

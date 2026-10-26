# Measuring latency

Every tier gets the same measurement, so the scoreboard in `docs/VIDEO_PLAN.md` compares like with like. Two sources: **high-speed video** (end to end, what the video shows) and **logged timestamps** (per stage, from the firmware and the PC pipeline).

## The rig

- **B-cam:** phone at 240 fps, side angle, locked exposure and focus. One shot frames three things:
  1. **Body marker:** a strip of bright tape on the belt, same spot every tier.
  2. **Marker LED:** lights for 20 ms the instant the output stage changes an input.
     - Tier 0: the PC sends `CMD_MARKER` to a spare XIAO running `pio run -e marker -t upload` (bright LED + ~220 Ω resistor from D0 to GND). Pass its port to the webcam tier (`--marker-port`).
     - Tier 1: the belt node lights its own marker LED when its HID report changes.
     - Tier 2+: the hub's spare GPIO lights it when a relay or DAC output changes.
  3. **TV:** the part of the screen where the action shows (the character's feet for a jump).
- **Lighting:** 50 Hz mains flickers at 240 fps under cheap LEDs. Use daylight or flicker-free lights; film a test clip first.
- **Trials:** 20 per action, at least 3 s apart, standing still between trials (the analyzer needs a quiet 0.5 s baseline before each movement).

## Analyze a clip

```bash
uv run bodyctl latency-video data/clips/tier1_jump.mp4
```

The first run opens the first frame and asks you to draw three boxes: **tell** (the tape), **led**, **screen** (Enter to accept, `c` to skip one). They're saved next to the clip as `tier1_jump.mp4.rois.json`. Reuse them for the next clip filmed with the same framing:

```bash
uv run bodyctl latency-video data/clips/tier2_jump.mp4 --rois-from data/clips/tier1_jump.mp4
```

Or pass boxes directly (handy on a machine without a display): `--roi tell=x,y,w,h --roi led=... --roi screen=...`.

Output, next to the clip (or `-o folder`):

- `<clip>_latency.csv`: one row per trial with frame numbers and `tell_to_led_ms`, `led_to_screen_ms`, `tell_to_screen_ms`.
- `<clip>_latency.png`: histograms with the median marked, ready for the video.
- A summary on the terminal: median, p90, p99, range.

Copy the CSV to `data/results/<tier>_<action>.csv` for the scoreboard.

### How onsets are found

- **LED:** halfway between its dark and lit brightness; each flash anchors one trial.
- **Tell:** vertical centroid of the tape; the onset is the first frame that leaves its 0.5 s baseline by more than `--k` (default 6) standard deviations for two frames running. Searched up to `--lead-max-ms` (600) before the LED.
- **Screen:** frame-to-frame change inside the screen box, same rule, searched up to `--lag-max-ms` (500) after the LED.

If a trial comes out empty, the box probably catches other motion (a moving background on the TV, the arm crossing the tape). Make the box tighter, or raise `--k`.

### Phone slow-motion files

Some phones save 240 fps slow-motion as a 30 fps file. Check `latency-video`'s summary: if the numbers are 8× too large, pass `--fps 240`. Better: export or record at the native rate (on iPhone, AirDrop the original or use a capture app that writes 240 fps metadata).

## DualSense baseline (the number to beat)

Same rig, no LED: put a small tape marker on your thumbnail, press the jump button 20 times, and analyze with only **tell** (the thumb) and **screen** boxes. Without an LED box the tell onsets anchor the trials, and the CSV's `tell_to_screen_ms` is the baseline.

## Logged timestamps

Every packet carries `seq` and the sensor timestamp (hard rule 6). The firmware keeps per-stage histograms (`firmware/lib/latency`): sample→detect, detect→send, send→receive (needs time sync), receive→output, and tell→output. Send `CMD_STATS` to a device to get them as log lines (`bodyctl device stats --port ...`, M3). The Tier 0 PC pipeline keeps the same kind of stats (capture→pose→gesture→output) and shows them on the dashboard.

## Accuracy check (M1 acceptance)

Film 20 trials, hand-count the tell, LED and screen frames for each in a video editor with frame stepping, and compare with the CSV: every value should be within ±1 frame. The automated test `pc/tests/test_latency_video.py` does the same on a generated clip with known frames.

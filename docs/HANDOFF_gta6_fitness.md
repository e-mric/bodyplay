# Handoff: GTA 6 fitness mode (Tier 0, webcam), state on 2026-11-21

A brief for the next agent picking up this work. Read `CLAUDE.md` first: it covers the
project, the hard rules and the commands. This note covers only the GTA 6 fitness work in
progress.

## Goal right now

The user is focusing on **GTA 6 fitness mode only**. Do not work on the play moves (run, jump, aim…).

**Current exercise priority (2026-11-26):** work on kettlebell, squats, and pull-ups first.
Deadlifts, bench press, punching bag, and meditation are later additions.

The user exercises in front of a webcam. Bodyplay turns each rep into the
controller input that GTA 6 expects in the gym, and sends it to a virtual Xbox 360 pad.
Veyra (a PS5 Remote Play client on the Windows PC) forwards it to the PS5.

The current exercise is the **kettlebell**. After it, the other gym exercises:

- pull-ups
- squats
- deadlifts
- bench press
- punching bag
- meditation, which isn't built yet and still needs a video of it

## The chain

webcam → RTMPose-m (rtmlib, CUDA) → `WebcamGestures` (`pc/bodyctl/gestures/webcam.py`)
→ `BindingEngine` (`pc/bodyctl/mapping.py`) → `Gamepad` (`pc/bodyctl/output/gamepad.py`,
vgamepad + ViGEmBus, virtual Xbox 360 pad) → Veyra Remote Play → PS5 GTA 6.

- The real Xbox controller stays connected alongside. Veyra reads both controllers, so
  run with `--no-passthrough`.
- The virtual pad takes XInput slot 1.

## How GTA 6 gym controls work

From the user's gameplay videos and `config/profiles/gta6.yaml`, mode `fitness`:

- **Reps:** L2 + R2 are held and pressed harder on the way up, held at the top, released
  to go down, and pressed again from the bottom.
- **Struggling:** X is mashed through the whole set (2026-11-27), so it's there when the
  character tires near the end; the game ignores it before.
- **Ending a set:** circle ends the set (puts the weight down). The punching bag is the
  exception: it has no circle.
- **Punching bag:** square is mashed while hitting.
- **Kettlebell start pose:** the character is hinged over the kettlebell, with both hands
  on the handle at the floor or shins.

## What works (verified)

- **Kettlebell detection on recorded drills**, front and 45° right:
  - one set, starting about 0.5 s after the grab
  - every swing counted: 12 from the front and 9 at 45°, checked against the top of each
    swing
  - circle once at the end
  - Test: `pc/tests/test_workout.py::test_gta6_kettlebell_on_real_recordings`.
  - Fixtures: `pc/tests/fixtures/kettlebell_front.npz` and `kettlebell_right45.npz` (trimmed
    drill recordings).
- **Kettlebell detection live**, checked with `data/live/kettlebell_live_1.npz`:
  - the set started at 27 s and 6 swings were counted
  - L2/R2 went 0 → 1.0 on each swing up, as recorded in the recording's pad track (what
    was actually sent to the virtual pad)
- **Fitness mode ignores play moves.** In fitness mode, `WebcamGestures.update()` returns
  right after the workout check. No jump, crouch, run, aim or punch is detected or sent.
- **The tuner and drill summary replay gym drills in fitness mode.** In
  `webcam_tune.replay`, `GYM_MOVES` is read from the recording header's `moves`.
- **Tests:** all Python tests pass except 6 in `test_device.py` and `test_replay.py`.
  Those need `pio run -e nodesim/replay` host builds, which don't compile on this Windows
  PC; this failure predates this work.
  - Run: `uv run pytest -q pc/tests --deselect pc/tests/test_device.py --deselect pc/tests/test_replay.py`

## OPEN PROBLEM: the game does not react (start here)

The body detection works and the triggers are sent, but GTA 6 does nothing.

- **The user's observation:** earlier in the day, play moves (lean, crouch) did reach the
  game through Veyra.
- **What was tried:** a standalone trigger test, with no camera, on a freshly created
  virtual pad. The game had no reaction.
  - The test script is `trigger_test.py` in the old session's temp folder, which may be
    gone; it's easy to rewrite.
  - It holds L2 + R2 for 5 s, does 5 ramped presses, then taps B (circle).

**Leading hypothesis, not confirmed:** Veyra only picks up controllers when its stream
connects.

- Every Bodyplay restart unplugs the old virtual pad and plugs in a new one, and Bodyplay
  was restarted many times after lean/crouch last worked.
- So the game may simply never see the new pad.
- The user went on a break before the test with a reconnect after Bodyplay starts.

**Next steps, in order:**

1. Start Bodyplay (command below). Then have the user **disconnect and reconnect the
   Veyra stream**. Then do a kettlebell set at the in-game kettlebell.
2. If it still does nothing, test a lean:
   - press key `m` in the window for on-foot mode, then `m` again to come back to fitness
   - lean and crouch use sticks and buttons
   - if those reach the game but L2/R2 don't, the triggers specifically don't get through
     Veyra
   - then try sending L2/R2 as full digital presses (1.0) instead of the progressive
     pressure, or check how Veyra maps Xbox triggers
3. If the reconnect fixes it, avoid it next time:
   - either keep one virtual pad alive across restarts, for example a small long-running
     pad process
   - or print a clear "reconnect Veyra now" message at startup
   - record the answer in the Remote Play open question in `CLAUDE.md`
4. If the game needs something else to start the set, ask the user to try the real
   controller at the kettlebell. Possibilities:
   - L2 + R2 held while in the grab pose. Bodyplay currently sends 0 until the first
     swing up.
   - an interaction button
   - If L2 + R2 held in the grab pose starts the set, press them as soon as the set
     starts, the way `TOP_START` does for squat and bench in `webcam.py`.

## Commands

```powershell
# live play, fitness mode, kettlebell picked, saves the session for replay
uv run bodyctl webcam --game gta6 --mode fitness --exercise kettlebell --window 400,320,1500,844 --on-top --pad dualsense --record data/live/kettlebell_live_3.npz
```

- In the window:
  - `q` quits; the recording is saved when it closes
  - `e` cycles through the exercises
  - `m` cycles through the modes
- Guided drill recording (one set: grab, 8 swings, put down; written to `data/drills/`):
  `uv run bodyctl webcam-drill kettlebell --reps 8 --angles front,right45 --video --pad dualsense`
- `--record` and `--mode` on `bodyctl webcam` are new (uncommitted).
- Thresholds come from the git-ignored tuned profile
  `data/profiles/gta5_tuned_rtmpose.yaml`, plus the GTA 6 controls (`--game gta6`).

To inspect a recording:

- load it with `bodyctl.tools.drill.Recording.load(path)`
- feed `rec.poses()` frame by frame to `WebcamGestures(prof.gestures, fitness=True, exercise="kettlebell")`
- print `g.state.workout`, `workout_reps`, `workout_progress` and the events
- `rec.pad` columns are: buttons bitmask, lx, ly, rx, ry, l2, r2 (what was sent live)

## Kettlebell detection, as implemented

Where it lives in `webcam.py`: `_kettlebell`, `_knee_y` and the kettlebell branch of
`_workout`.

**Body scale (`_body_px`):**

- It's the recent tallest shoulder-to-hip distance, decaying at `body_scale_decay` per
  second.
- Bent over toward the camera, the torso looks short. Using the current frame's torso
  made the hands look far apart, so sets never started or kept ending. That was the first
  "doesn't start" bug.

**Start condition:**

- Hands together: wrists within `swing_hands_apart` (0.6) body lengths. If one wrist is
  hidden (side-on), that's accepted.
- Hands down: hands more than `swing_grab` (0.45) body lengths below the hips.
- Measured standing, hanging hands are level with the hips (≈0). In the grab pose they
  are 0.5–0.9 below.

**During a set:**

- The set continues while the hands stay together; the swing may take them overhead.
- It ends after `workout_release_ms` with the hands apart, and circle is then tapped.

**Rep progress:**

- Progress is (knee_y − wrist_y) / (knee_y − shoulder_y), so 0 = hands at the knees.
- It arms below `swing_low` (0.25) and counts a rep above `swing_high` (0.85).
- Real swings reach about 1.4–1.5.
- When hinged, the arms hide the knees. The knees are then placed at the last seen
  knee-to-hip offset (`_knee_y`).

**Mapping (`mapping.py`):**

- L2/R2 pressure = progress while rising, 0 otherwise.
- X mashes when struggling.
- `exit_tap` circle fires on `workout_end`.

## Uncommitted changes (commit when the user asks; exclude `AGENTS.md`)

- `pc/bodyctl/gestures/webcam.py`:
  - kettlebell body scale
  - `_knee_y`
  - start below the hips
  - continue while the hands are together
  - fitness mode skips play gestures
- `pc/bodyctl/config/profile.py`:
  - `swing_start` replaced by `swing_grab`
  - new `body_scale_decay`
- `pc/bodyctl/tier0.py`, `pc/bodyctl/cli.py`: `--mode`, `--record`.
- `pc/bodyctl/tools/webcam_tune.py`: replays gym drills in fitness mode.
- The current pass adds a squat drill, exercise switching for combined workout drills,
  and tuning groups for kettlebell and squat. These paths still need camera recordings
  and live GTA checks.
- `pc/tests/test_workout.py`:
  - test for the real recordings
  - the fitness play-moves test now asserts that play moves are *not* detected
- `pc/tests/fixtures/kettlebell_*.npz`: new, about 155 KB each.
- `CLAUDE.md`, `docs/IMPLEMENTATION_PLAN.md`: notes that Remote Play works via Veyra
  (2026-11-21).
- `AGENTS.md` is the user's own untracked file. Leave it out of commits unless they say
  otherwise.

## After the kettlebell works in the game

- First do the same loop for squats and pull-ups:
  - record a drill (`webcam-drill` supports `kettlebell`, `squat`, and `pullup`)
  - replay it
  - set the thresholds from the data
  - add the recording as a fixture test
- Squat thresholds have only been set on synthetic poses; squat and pull-up still need
  real recordings. Then add deadlift, bench, bag, and meditation as separate work.
- Meditation: L2 + R2 held, and "thoughts" are cleared by moving both sticks in a circle.
  Wait for the user's video of it.

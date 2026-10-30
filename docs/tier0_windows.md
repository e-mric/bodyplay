# First play test: GTA V on Windows with a phone webcam

This is an initial Tier 0 test with GTA V installed directly on the Windows PC.
Bodyplay sends a virtual Xbox 360 controller to the game. No PS5 or chiaki-ng is
needed for this test. The project still targets PS5 for the later hardware tiers;
PC measurements must be labelled separately from PS5/Remote Play results.

## 1. Connect the phone camera

Connect the phone using its Windows webcam integration. Confirm its picture in
the Windows Camera app, then close that app so Bodyplay can open the camera.
Use a stable mount, landscape orientation, and frame your whole body, including
feet and raised hands. Start at 640×480 and 30 fps; the requested rate may differ
from the actual phone stream rate printed by Bodyplay.

## 2. Test the camera and gestures without a controller driver

Install Git and uv as described in [setup.md](setup.md). In a terminal:

```text
git clone https://github.com/e-mric/bodyplay.git
cd bodyplay
uv sync --no-group gamepad
uv run --no-group gamepad bodyctl webcam --backend dry-run --fps 30
```

If you already cloned the repository, use `git pull` in that checkout instead.
The first webcam run downloads the MediaPipe pose model. Allow camera access
if Windows asks. If the phone isn't camera 0, retry with `--camera 1` (then 2).

Stand still briefly, then try running in place, leaning, and a small jump. Check
that the dashboard shows a skeleton and the corresponding events/stick changes.
This dry run does not send input to GTA V. Press **q** with the dashboard focused
to quit; **Ctrl+C** in the terminal also stops it.

## 3. Enable the virtual controller and test in GTA V

```text
uv sync
uv run bodyctl webcam --backend vgamepad --fps 30
```

`uv sync` installs vgamepad on Windows. Its build can open the ViGEmBus driver
installer: complete that dialog; restart Windows if the installer requests it.
If the controller cannot be created, follow the ViGEmBus step in
[setup.md](setup.md).

While Bodyplay is running, open **Win+R → joy.cpl**. Verify that a virtual Xbox
controller appears and that its sticks/buttons respond when you move. Then
launch GTA V, use the default controller layout, and enter **story mode**.
Keep the keyboard/mouse or your usual controller available for menus and actions
that Bodyplay doesn't cover yet.

Start on foot and check one action at a time: run, stop, lean, jump, punch, aim.
Then the Tier 0 extras (profile `tier0`, virtual pad only):

- **Shoot:** while aiming, flick the aiming hand up like recoil (fires RT / R2).
- **Weapon wheel:** reach over your right shoulder and keep the hand there (holds LB /
  L1), point your left hand at a slot (right stick; a hanging arm selects nothing),
  drop the right hand to equip.
- **Cover:** crouch down to take cover, stand up to leave it (taps RB / R1).
- **Fly (plane or helicopter):** switch the dashboard to `heli` mode (key **m**; plane
  is the same mode), then spread your arms like wings: tilt them to roll (your left hand
  down = roll left), raise both hands for nose up, lower them for nose down. Bring your
  arms in to let go of the stick.
The YAML uses PlayStation button names; the Windows backend translates these to
Xbox buttons (Cross → A, Square → X, Circle → B, Triangle → Y).

Dashboard keys only work while its window has focus: **p** pauses body input,
**m** changes mode, **q** quits. Alt-tab to the dashboard before using those keys.

## 4. Tune the gestures to your body and camera

Record each move several times with on-screen cues, tune the thresholds on the
recordings, then repeat the drill to test them:

```text
uv run bodyctl webcam-drill jump --reps 8
uv run bodyctl webcam-tune
uv run bodyctl webcam-drill jump --reps 5
```

Moves: `run`, `walk`, `jump`, `crouch`, `lean`, `punch`, `aim`, `shoot`, `wheel`, `fly`, or `all`. `walk` cues
walk, jog, then sprint in place; `webcam-tune` sets the profile's `cadence_curve` (steps/s
for half stick, full stick and sprint taps) from your own pace. Before each
recording, a setup screen guides the camera: whole body in view, distance, camera
level, and the camera angle. Always face the screen and move the camera. To make
gestures work wherever the camera ends up, record from several angles:
`--angles front,right30,left30` (also `right45`, `left45`).

During a drill, a beep and a gold title mark each cue; the side panel shows what was
detected (check when expected, cross when not). The drill scores itself at the end.
Recordings go to `data/drills/`; add `--video` to also keep the camera footage.

**Pose model.** With an NVIDIA GPU, `bodyctl` uses RTMPose-m (`--pose auto`, the
default): on a recorded drill it caught 30 of 36 moves against 20 for MediaPipe and
kept the knees tracked in 98 % of frames, at about 4.5 ms per frame. `--pose mediapipe`
switches back. `uv sync` installs it, with CUDA and cuDNN as Python wheels (large: about
1 GB); the first run downloads the models into `~/.cache/rtmlib`.

`webcam-tune` uses the drills recorded with the current pose model, tunes landmark
smoothing (One Euro filter) and then each gesture's thresholds, and writes
`data/profiles/gta5_tuned_rtmpose.yaml` (or `_mediapipe`). `webcam` and `webcam-drill`
pick that file up automatically; `--profile` overrides it.

## 5. What to report from the first test

- Did the phone image and skeleton appear? What camera index and fps were shown?
- Did `joy.cpl` show the pad and respond to movement?
- Did GTA V respond? Which actions worked, missed, or fired unexpectedly?
- If startup failed, copy the terminal error.

Once these work, record the 20 jump/run-start trials described in
[measuring.md](measuring.md), marking the results **Windows native + phone webcam**.
A phone's video transport adds its own delay; these numbers do not establish
PS5 or wearable latency.

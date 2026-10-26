# Bodyplay

Control GTA on a PS5 with your body, from a €0 webcam prototype to a sub-10 ms wearable rig.

| Tier | Cost | What you need | What it does |
|---|---|---|---|
| 0 | €0 | Webcam, Windows PC, [chiaki-ng](https://github.com/streetpea/chiaki-ng) | Pose estimation → virtual Xbox pad → Remote Play |
| 1 | ~€30 | XIAO ESP32S3 + LSM6DSOX on a belt | Running, jumping, lean and twist detected on the sensor, which plugs into the PC as a USB gamepad |
| 2 | ~€180 | + PlayStation Access controller, ESP32-S3 hub | The PC leaves the loop; target ≤ 10 ms from movement to PS5 |
| 3 | ~€520 | + wrist nodes, EMG, foot mat, 2nd Access controller | Full body, every vehicle, quick-time events |

## Docs

- [Implementation plan](docs/IMPLEMENTATION_PLAN.md): milestones, tasks, acceptance criteria
- [Video plan](docs/VIDEO_PLAN.md): chapters, script beats, shots, measurement protocol
- [CLAUDE.md](CLAUDE.md): project rules for Claude Code
- Design doc (latency budget, Access controller pinout, shopping list): https://claude.ai/artifact/1o7SSLSnKpTSY1CWsXXjVU

## Safety

The Access controller's expansion ports run at **1.8 V**. Never put more than 1.8 V on Tip or Ring 1, and never draw power from a port. Check every output with a multimeter before plugging in. Clear about 2 × 2 m of floor and secure the TV before playing.

## Status

See the checklist in `docs/IMPLEMENTATION_PLAN.md`.

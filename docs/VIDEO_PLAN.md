# Video plan

One main video (about 18 minutes) that climbs from a €0 webcam hack to a sub-10 ms full-body rig, with a scoreboard that updates every chapter: **cost, latency, and how much you moved.** Tiers 0–2 are filmed on GTA V while you build; the finale is filmed on GTA 6 in launch week.

**Working titles**

- "I made GTA 6 a workout (from €0 to 10 milliseconds)"
- "Playing GTA 6 with my whole body: €0 vs €30 vs €500"
- "I refused to sit still for GTA 6"

**Thumbnail idea:** you mid-jump in front of the TV, the character mid-jump on screen, a big "10 ms" and a small "€0 → €520".

**Publish target:** launch week, 2026-11-22 to 11-26. A teaser short goes out before launch (Nov 12–18), built from Tier 0–2 footage.

## The scoreboard (recurring graphic)

Fill it in as each tier is measured (M1 analyzer + a smartwatch). Same 20-minute GTA V on-foot session per tier for the exercise column.

| Tier | Cost | Jump latency, tell → screen (median) | vs DualSense | Steps / 20 min | Avg heart rate |
|---|---|---|---|---|---|
| DualSense baseline | €0 | _measure_ | — | _measure_ | _measure_ |
| 0 Webcam | €0 | | | | |
| 1 Belt sensor | ~€30 | | | | |
| 2 Access controller | ~€180 | | | | |
| 3 Full rig | ~€520 | | | | |

Present heart rate and steps as "what happened to me", not health claims.

## Chapter outline

Runtimes are targets. "Built in" points to the milestone in `IMPLEMENTATION_PLAN.md` that must be done before filming.

### 0. Cold open (0:00–0:45)

- **Show the payoff first:** full rig, GTA 6, you sprinting in place, a jump landing perfectly. Freeze frame.
- **Line:** "GTA 6 is the game I'll play for a hundred hours. I'm not spending them on a couch. So I made my body the controller — and then I made it fast."
- **Then rewind:** "It started with a webcam and zero euros."
- **Graphic:** empty scoreboard slides in.

### 1. The idea and the rules (0:45–2:30)

- **Beats:** the problem (GTA 6 + sitting); prior art in one breath (Kinect + FAAST playing World of Warcraft in 2010, Ring Fit); why "instant" is the hard part.
- **Explain the measuring rig:** 240 fps camera, LED that lights when the input fires, tape marker on the belt, TV in frame. "Every chapter, we count frames."
- **Baseline:** DualSense jump measured on camera. That's the number to beat.
- **Shots:** rig setup timelapse; slow-mo of a thumb pressing a button, frame counter overlay.
- **Built in:** M1.

### 2. Tier 0 — €0, webcam (2:30–5:00)

- **Beats:** MediaPipe skeleton on screen; mapping run-in-place and jump; Remote Play into the PS5 with chiaki-ng.
- **Honest moment:** it works, it's fun, and it's late. Show the slow-mo where your feet leave the ground and the character jumps after.
- **Line:** "The camera has to see the jump happen before it knows it's a jump. That's the problem we're solving for the rest of this video."
- **Measurement:** latency histogram + scoreboard row.
- **Shots:** dashboard overlay side by side with gameplay; one fail clip (it's good content).
- **Built in:** M2.

### 3. Tier 1 — €30, the boxer's trick (5:00–8:00)

- **Beats:** the boxer analogy — a good boxer reads the shoulder before the punch. A jump starts with a dip. Fire on the dip, not the jump.
- **Graphic:** IMU trace of a real jump: dip → reversal → take-off, with the moment the event fires marked. This is the video's key explainer.
- **Hardware close-up:** XIAO + IMU on a belt, €30 on screen.
- **Measurement:** latency + scoreboard; first time the body beats the camera clearly.
- **Line:** "It's not reading my jump. It's reading my intention to jump."
- **Built in:** M3.

### 4. Tier 2 — €180, take the PC out (8:00–11:30)

- **Beats:** why the PC is slow (OS, USB, Remote Play) → the diagram: body → hub → Access controller → PS5. Explain the Access controller is Sony's own accessibility hardware with 3.5 mm ports, and that its spec is public for makers.
- **Safety moment:** multimeter on the hub outputs, "1.8 volts, never more". Clear and short; it protects viewers who copy you.
- **Reveal:** slow-mo jump through the Access controller next to Tier 0, split screen.
- **Measurement:** latency vs DualSense baseline — the headline number. If it beats the thumb, say so; if it ties, say that.
- **Wireless vs tethered:** one line and the link-latency histogram (M5).
- **Built in:** M4, M5.

### 5. Tier 3 — the full rig (11:30–15:30)

- **Shoot before you move:** EMG graph rising before the finger visibly moves, slow-mo. "The shot leaves before my hand does."
- **Vehicles:** legs as throttle, DualSense steering. Car chase segment; helicopter with throttle from your hands or feet.
- **Quick-time event:** a mashing prompt cleared with a 10-second all-out sprint in place.
- **Measurement:** scoreboard row; false-trigger count from the 10-minute log.
- **Built in:** M6, M7.

### 6. GTA 6 finale (15:30–17:30)

- **Launch-day setup:** remapping to GTA 6's controls is a config change (show the YAML diff for two seconds).
- **Play:** first mission or a free-roam run with the full rig. Let gameplay breathe, minimal talking.
- **Built in:** M8.

### 7. Verdict (17:30–18:30)

- **Final scoreboard.** Which tier is worth it for whom: €0 to try, €30 for most people, €180 if latency bothers you, €520 if you're me.
- **Line:** "A hundred hours of GTA 6, and I didn't sit for any of them."
- **Call to action:** repo link (open source), parts list, subscribe for the follow-up (the AI that learns your moves — M9).

## Filming setup

- **A-cam:** front wide, you + TV, for the main narrative.
- **B-cam (measurement):** phone at 240 fps, side angle, framing the belt marker, the hub LED and part of the TV in one shot. Lock exposure and focus.
- **Screen capture:** PS5 share-button recordings or a capture card for clean gameplay.
- **Lighting at 240 fps:** mains power in Belgium runs at 50 Hz, so cheap LED and fluorescent lights can flicker in slow motion. Use flicker-free lights or daylight; test a clip before each measurement session.
- **Markers:** bright tape on the belt, same spot every tier; the LED visible but not blinding.
- **Smartwatch:** steps and heart rate for every scoreboard session.

## Measurement protocol (same every tier)

1. 20 trials per action (jump, run start; shoot and punch from Tier 3), at least 3 s apart.
2. Run `bodyctl latency-video` on the B-cam clip; keep the CSV in `data/results/`.
3. Report median and the spread (p90 or p99) on screen, plus the DualSense baseline.
4. Same 20-minute GTA V on-foot session for the exercise columns.

## Disclaimers to include

- Not affiliated with Sony or Rockstar; the Access controller is used through its published expansion-port spec.
- Electrical safety: 1.8 V on the ports, never power anything from them, check with a multimeter. Copy at your own risk.
- Physical safety: clear the floor, secure the TV.
- Check Rockstar/Take-Two's policy on gameplay videos before monetizing; use licensed music only.

## Shorts (from the same footage)

| Short | Hook | Source chapter |
|---|---|---|
| "The jump before the jump" | IMU trace of the dip, then the in-game jump | 3 |
| "Shooting before my hand moves" | EMG graph + slow-mo | 5 |
| "QTE = sprint" | 10-second all-out mash | 5 |
| "I tried to fool my own controller" | Feint drills: fake jumps and punches vs the detector | 3 or follow-up |
| "GTA V yoga, for real" | The yoga minigame played with the rig | 5 |
| "€0 vs €500 GTA controller" | Split-screen latency race | 2 + 4 |
| Teaser (pre-launch) | "GTA 6 comes out in a week. I'm not sitting down for it." | 0–4 |

## Production schedule

| Dates (2026) | Film | Edit / publish |
|---|---|---|
| Oct 25–27 | Rig setup, DualSense baseline (ch. 1) | — |
| Oct 28–Nov 1 | Tier 0 (ch. 2) | Rough cut ch. 1–2 |
| Nov 2–4 | Tier 1 (ch. 3) | IMU trace graphic |
| Nov 5–10 | Tier 2 (ch. 4) | Architecture diagram, split-screen reveal |
| Nov 11–18 | Tier 3 (ch. 5); teaser short | Publish teaser |
| Nov 19–21 | GTA 6 finale (ch. 6), verdict (ch. 7) | Final scoreboard |
| Nov 22–26 | Pick-ups | Publish main video + first shorts |

## Shot checklist

- [ ] Cold open: full rig GTA 6 jump, freeze frame
- [ ] Rig setup timelapse; DualSense baseline slow-mo
- [ ] Tier 0: dashboard + gameplay split, late-jump fail clip, histogram
- [ ] Tier 1: belt close-up, IMU jump trace, measurement
- [ ] Tier 2: wiring close-ups, multimeter safety, slow-mo split screen vs Tier 0, measurement
- [ ] Tier 2: wireless vs tethered histogram
- [ ] Tier 3: EMG graph + slow-mo, car chase, helicopter, QTE sprint, false-trigger count
- [ ] GTA 6: config diff, first mission with the rig
- [ ] Verdict: final scoreboard, to-camera wrap
- [ ] B-roll: soldering, parts flat-lay with prices, sweat, smartwatch close-ups

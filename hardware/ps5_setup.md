# PS5 setup for Tier 2+

Menu names below are from memory of the PS5 UI. Check them on the console and correct this file if they differ.

## 1. Connect the Access controller over USB

- Plug the Access controller into the PS5 with a **USB data cable** and leave it plugged in. Over Bluetooth the latency budget doesn't hold.
- Set controllers to communicate over the cable: **Settings → Accessories → Controllers → Communication method → Use USB cable**.

## 2. Map the expansion ports (one fixed PS5 button per port)

Under **Settings → Accessibility → Controllers → Access controller**, create a profile (e.g. "Bodyplay GTA") and assign each expansion port as listed below. Source of truth: the `ports:` table in `config/profiles/gta5.yaml` (hard rule 4: modes never change what a port sends).

| Port | Device type | Assign to |
|---|---|---|
| A-E1 (Expansion 1) | Stick | Left stick |
| A-E2 (Expansion 2) | Button | Cross |
| A-E3 (Expansion 3) | Button | Square |
| A-E4 (Expansion 4) | Button | Triangle |
| B-E1 | Stick | Right stick |
| B-E2 | Trigger | R2 |
| B-E3 | Trigger | L2 |
| B-E4 | Button | Circle |

Leave the Access controller's own buttons unassigned, or on things the body doesn't need. Bumping them by accident shouldn't jump or shoot.

## 3. Pair with the DualSense as one virtual controller

The Access controller can be used **with another controller** as a single player. Turn that on and pick the DualSense. The DualSense keeps everything the body doesn't own: steering in vehicles, cover, reload, weapon wheel, phone, menus.

## 4. Check with the bench test

```bash
uv run bodyctl hil --port <hub port>
```

Confirm each port on a screen that shows the inputs live. Record in `CLAUDE.md`:

- **Stick axis order** on Tip vs Ring 1 (open question).
- **Stick merging:** move the Access controller stick (`bodyctl hil`, A-E1 up) and the DualSense left stick at the same time. Does the PS5 add them, pick one, or jitter? This decides how vehicle modes behave. Body sticks are already held at neutral in vehicles (rule 4; only Tier 0 flight, on the virtual pad, is exempt).

## 5. Game

GTA V, **Performance** mode, story mode, default controls. TV in Game Mode.

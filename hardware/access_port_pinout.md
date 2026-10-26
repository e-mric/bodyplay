# Access controller expansion ports

Source: Sony, [Access Controller for PlayStation 5 — Expansion Port Specifications](https://www.playstation.com/content/dam/global_pdc/en/corporate/support/manuals/accessories/ps5-accessories/access-controller/access-docs/Access%20Controller%20for%20PlayStation%205%20Expansion%20Port%20Specifications.pdf) (PDF), as summarized in the design doc. Re-check against the PDF figures before soldering.

## Safety first

- **Vdd = 1.8 V.** Never put more than 1.8 V on Tip or Ring 1 (hard rule 1).
- **Never power anything from a port** (hard rule 2). The spec says the ports don't supply peripherals. The hub shares **ground only** (Ring 2 on stick and trigger devices).
- Firmware clamps every DAC code to **3499** (1.75 V at Vref 2.048 V, gain 1); a unit test keeps it there.
- Hardware backs that up: **1 kΩ series resistor + Schottky clamp to the port's Vdd** (Sleeve) on every analog line.
- Before plugging into the controller: multimeter on every output at code 0, neutral and max; confirm no voltage on any port pin from the hub side when the hub is off.

## Pinout (3.5 mm, 4-pole TRRS)

| Contact | Button device | Trigger device | Stick device |
|---|---|---|---|
| Tip | Button input | Trigger input (analog) | Stick axis (analog) |
| Ring 1 | not used | not connected | Stick axis (analog) |
| Ring 2 | not used | GND | GND |
| Sleeve | Vdd/2 | Vdd (1.8 V) | Vdd (1.8 V) |

> **Axis-order warning.** The spec's *jack* table and *plug* table name the stick axes on Tip and Ring 1 in **opposite order**. Check the figures, then confirm on the PS5 with `uv run bodyctl hil --port <hub>`, watching a screen that shows the inputs (the Access controller's settings under Settings → Accessibility → Controllers, or a game). A swapped axis is fixed in firmware (`swap_xy` per port), not by rewiring. Record the answer in `CLAUDE.md` (open question).

## How the hub drives each port type

| Port type | Hub output | Circuit |
|---|---|---|
| Button | photoMOS relay (Panasonic AQY212EH) | ESP32 GPIO → 330 Ω → relay LED; relay output across the button contacts given by the spec's button-device figure (Tip and Sleeve per the table above; verify on the figure). Isolated, polarity-free, operate time < 1 ms. |
| Trigger | MCP4728 DAC channel | DAC out → 1 kΩ → Tip; Schottky from Tip to Sleeve (Vdd); DAC GND → Ring 2. Code 0 = released, clamp 3499 = full pull (calibrate against what the PS5 shows). |
| Stick | 2 MCP4728 channels | DAC out X/Y → 1 kΩ each → Tip / Ring 1; Schottky clamps to Sleeve; DAC GND → Ring 2. Neutral expected near Vdd/2 ≈ 0.9 V (code ≈ 1800); confirm against the spec's stick table and calibrate. |

Both MCP4728 boards default to I2C address 0x60, so each gets its own bus: bus 0 → DAC A (controller A), bus 1 → DAC B (controller B).

## Port allocation (GTA V profile)

Each port emits **one fixed PS5 button**, set once in the PS5's Access controller profile; modes only change which gesture drives it (hard rule 4). Source of truth: `config/profiles/gta5.yaml`.

| Port | Device | PS5 input | On foot | In a vehicle |
|---|---|---|---|---|
| A-E1 | Stick | Left stick | Run cadence (Y), lean (X) | Neutral (DualSense steers) |
| A-E2 | Button | Cross (X) | Sprint taps at step rhythm | — |
| A-E3 | Button | Square | Jump | — |
| A-E4 | Button | Triangle | Enter vehicle | Exit (gated: stopped) |
| B-E1 | Stick | Right stick | Camera (torso twist) | Neutral |
| B-E2 | Trigger | R2 | Shoot (gated: aiming) | Throttle (run cadence) |
| B-E3 | Trigger | L2 | Aim | Brake / reverse (step back) |
| B-E4 | Button | Circle | Melee (punch) | — |

Controller A alone (locomotion) is enough for Tier 2: move, sprint, jump, enter/exit.

## Connection to the PS5

Both Access controllers stay on **USB cables** to the console (not Bluetooth), paired with the DualSense as one virtual controller. Setup steps go in `hardware/ps5_setup.md` (M4).

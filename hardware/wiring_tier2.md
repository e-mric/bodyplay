# Tier 2 wiring: hub → Access controller

ESP32-S3-DevKitC-1 (N8R8), two Adafruit MCP4728 DACs, four Panasonic AQY212EH photoMOS relays, TRRS pigtails into the Access controller expansion ports. Port rules and pinout: `hardware/access_port_pinout.md`. Read its safety section first.

## Hub pins (firmware: `firmware/src/hub/main.cpp`)

| Function | GPIO | Goes to |
|---|---|---|
| I2C bus 0 SDA / SCL | 8 / 9 | DAC A (controller A) |
| I2C bus 1 SDA / SCL | 10 / 11 | DAC B (controller B) |
| Relay A-E2 (Cross) | 4 | 330 Ω → AQY212EH LED anode; cathode → GND |
| Relay A-E3 (Square) | 5 | same |
| Relay A-E4 (Triangle) | 6 | same |
| Relay B-E4 (Circle) | 7 | same |
| Marker LED | 15 | LED + 220 Ω → GND (latency rig) |
| Belt tether RX / TX | 18 / 17 | belt XIAO D6 (TX) / D7 (RX), plus GND (2 Mbaud UART, until ESP-NOW in M5) |
| PC | USB port labeled **UART** | config, dashboard, `bodyctl hil` (921600 baud) |

Power the DACs from the hub's 3V3. **Nothing is powered from an expansion port** (hard rule 2).

## DAC channels

| DAC | Channel | Port | Contact |
|---|---|---|---|
| A | 0 | A-E1 left stick | Tip |
| A | 1 | A-E1 left stick | Ring 1 |
| A | 2, 3 | unused (held at 0 V) | — |
| B | 0 | B-E1 right stick | Tip |
| B | 1 | B-E1 right stick | Ring 1 |
| B | 2 | B-E2 R2 trigger | Tip |
| B | 3 | B-E3 L2 trigger | Tip |

Every analog line: **DAC out → 1 kΩ → port pin**, with a **Schottky diode from the port pin to the port's Vdd (Sleeve)**, anode on the pin. DAC GND → **Ring 2** of the stick/trigger ports.

Relay outputs go across the button contacts shown in Sony's button-device figure (Tip and Sleeve per the pinout table; check the figure before soldering).

The DACs run on their internal 2.048 V reference, and the firmware never writes a code above **3499 (1.75 V)** (hard rule 1, `firmware/lib/hubout/dac.h`, tested exhaustively in `test_hubout`). The hub never uses the MCP4728's VDD-referenced "fast write" command, where the same code would mean 2.8 V.

## Bring-up checklist (before anything touches the Access controller)

Do these in order, with the pigtails **unplugged** from the controller.

1. **Flash** the hub (`pio run -e hub -t upload`). It boots with every relay open, sticks at neutral and triggers released.
2. **Idle voltages** with a multimeter, black probe on DAC GND:
   - Every stick channel (A0, A1, B0, B1): about **0.90 V**.
   - Trigger channels (B2, B3) and unused A2, A3: **0 V**.
   - Nothing anywhere above **1.75 V**.
3. **Full scale:** run the bench test against nothing (`uv run bodyctl hil --port <hub>`, answer "n" to every question) and measure while each output is held:
   - Stick up/right: about 1.75 V. Stick down/left: about 0.05 V.
   - Trigger full: **1.75 V maximum**.
   - Each relay: continuity across its output pins while held, open after.
4. **Off-state check:** power the hub off and confirm **0 V** on every pigtail contact.
5. **Plug in controller A only**, set up the PS5 (`hardware/ps5_setup.md`), and run `bodyctl hil` for real. Note which way each stick axis goes. If Tip and Ring 1 are swapped (open question), that's fixed with `swap_xy` in firmware, not by rewiring.
6. Film step 2 for the video (the "1.8 V, never more" safety moment).

## Tethered belt (M4)

Flash the belt with `pio run -e node_belt_tether -t upload`. It's the same node plus a UART link to the hub: it syncs its clock to the hub's SYNC broadcast and sends events (stamped on the hub clock) and 250 Hz body state. Use a thin 3-wire cable (TX, RX, GND) along the belt. The node can stay on USB power from a power bank, or run on its LiPo.

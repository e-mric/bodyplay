# Tier 1 wiring: belt node

Seeed XIAO ESP32S3 + Adafruit LSM6DSOX breakout (STEMMA QT). A STEMMA QT to male-pin cable works, or solder four wires plus the interrupt.

| LSM6DSOX breakout | XIAO ESP32S3 | Notes |
|---|---|---|
| VIN | 3V3 | The breakout's regulator accepts 3.3 V |
| GND | GND | |
| SDA | D4 (GPIO5) | I2C at 1 MHz (`BP_I2C_HZ`); the breakout has 10 kΩ pull-ups |
| SCL | D5 (GPIO6) | |
| INT1 | D1 (GPIO2) | FIFO watermark interrupt: the node reads samples when it fires |
| DO / SA0 | leave open | Address 0x6A (the firmware also tries 0x6B) |

Marker LED for the latency rig: bright LED + 220 Ω from **D0** (GPIO1) to GND. It lights for 20 ms whenever the node's HID report changes in a time-critical way (button down, stick leaves neutral).

If no IMU answers at boot, the marker LED blinks at 2.5 Hz.

## Mounting

- Board **flat against the lower back, components facing out** (away from you). The firmware assumes forward = the board's −Z (`BP_MOUNT_FORWARD` in `firmware/platformio.ini`); the 2 s standing calibration levels the rest, so the board's rotation within the belt doesn't matter.
- Strap it tight: a node that wobbles on the belt adds fake jerk to every step.
- Tier 1 is wired by default: a 3 m USB-C data cable from the XIAO to the Windows PC, routed along the belt.

## Battery (M5, wireless)

LiPo 3.7 V ~400 mAh on the XIAO's battery pads (BAT+ / BAT−). Not needed while the node is on USB.

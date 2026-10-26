# Setup

Two machines: the **Mac** (development: write, build, test, flash) and the **Windows PC** (play: chiaki-ng Remote Play and the Tier 0–1 PC side). From Tier 2 on, no PC is in the hot path.

## macOS (development)

1. **Command-line tools:** `xcode-select --install` (clang for the native firmware tests).
2. **uv:** `brew install uv`.
3. **PlatformIO:** install the VS Code extension *PlatformIO IDE*, or the CLI with `uv tool install platformio`. The extension's CLI lives at `~/.platformio/penv/bin/pio`; add that to your `PATH` in `~/.zshrc` if you use it from the terminal:

   ```bash
   export PATH="$HOME/.platformio/penv/bin:$PATH"
   ```

   The first build downloads the pioarduino platform (Arduino-ESP32 core 3.x) pinned in `firmware/platformio.ini`.
4. **Python tools:** from the repo root, `uv sync` (creates `.venv`, installs Python 3.12 if needed). Check with `uv run pytest` and `uv run bodyctl --help`.
5. **Firmware:**

   ```bash
   cd firmware
   pio test -e native        # unit tests, no hardware
   pio run                   # build node_belt, node_wrist, dongle, hub
   ```

### Flashing a XIAO ESP32S3

- Plug it in with a **data** USB-C cable. It shows up as `/dev/cu.usbmodem*` (`ls /dev/cu.usbmodem*`).
- `pio run -e node_belt -t upload` (PlatformIO finds the port; add `--upload-port /dev/cu.usbmodemXXXX` if several boards are connected).
- If upload fails or the port is missing (a crashed sketch can take USB down): hold **BOOT**, tap **RESET**, release BOOT, then upload again. Tap RESET afterwards to run the new firmware.
- The node firmware runs USB in TinyUSB mode (HID gamepad + CDC serial), so after flashing the port name can change; re-run `ls /dev/cu.usbmodem*`.

### Flashing the hub (ESP32-S3-DevKitC-1)

- The board has two USB-C ports. Use the one labeled **UART** (USB-to-serial bridge) for flashing and logs; it shows up as `/dev/cu.usbserial-*` or `/dev/cu.SLAB_USBtoUART`/`/dev/cu.wchusbserial*` depending on the bridge chip.
- `pio run -e hub -t upload`.

## Windows PC (play)

For the first test with GTA V installed directly on Windows and a phone webcam,
follow [the Tier 0 walkthrough](tier0_windows.md). That test does not use chiaki-ng.

1. **Git and uv:** install [Git for Windows](https://git-scm.com/download/win), then uv in PowerShell: `winget install astral-sh.uv` (or the installer from [docs.astral.sh/uv](https://docs.astral.sh/uv/)).
2. **Repo:** `git clone https://github.com/e-mric/bodyplay.git`, `cd bodyplay`, `uv sync`. Run tools from the repo root: `uv run bodyctl --help`. Update with `git pull` then `uv sync`.
3. **chiaki-ng:** install from [github.com/streetpea/chiaki-ng](https://github.com/streetpea/chiaki-ng) (Windows installer). On the PS5: Settings → System → Remote Play → enable, then *Pair Device* and enter the code in chiaki-ng. Use a wired Ethernet connection on both ends if you can.
4. **ViGEmBus (Tier 0 only):** `uv sync` installs `vgamepad`, whose setup runs the bundled ViGEmBus installer (accept the driver prompt). If no prompt appears or the driver is missing, install the signed driver from the [ViGEmBus releases](https://github.com/nefarius/ViGEmBus/releases) (archived upstream since 2023, still installable). Confirm the virtual pad shows up in chiaki-ng's controller list (open question in `CLAUDE.md`).
5. **Serial ports:** nodes, dongle and hub appear as `COMx` (Device Manager → Ports). Pass them with `--port COM5`. The XIAO's TinyUSB CDC needs no driver on Windows 10/11; the hub's UART bridge may need the CP210x or CH343 driver from the chip vendor.

## Game settings (both tiers)

- GTA V on PS5, **Performance** graphics mode, story mode, default controls.
- TV in **Game Mode**.

## Automated checks

CI runs Python tests and firmware builds on macOS and Windows. It uses
`uv sync --locked --no-group gamepad` and `uv run --no-group gamepad ...` to
exclude the Windows driver installer. Gameplay output is tested through the
dry-run backend; ViGEmBus and chiaki-ng still need the play-PC check above.
PlatformIO is installed in the Python jobs too, so recorder and replay tests
exercise the native firmware simulator.

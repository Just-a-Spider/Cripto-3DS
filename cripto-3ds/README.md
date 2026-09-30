# Cripto-3DS Client (`cripto-3ds`)

A native Nintendo 3DS homebrew application built in C using `devkitARM`, `libctru`, `citro2d`, and `citro3d`. Connects over local WiFi TCP sockets to the `cripto-bot-engine` to display live market telemetry, technical indicators, portfolio valuation, and provide physical hardware-button trade authorization.

---

## Hardware & Software Requirements

- **Nintendo 3DS / 2DS family console** (Old 3DS, 2DS, New 3DS, New 2DS XL).
- Custom Firmware (Luma3DS) with Homebrew Launcher or FBI installer.
- Active WiFi connection on the same local network as the `cripto-bot-engine` server.

---

## Installation & Deployment

Pre-compiled binaries are provided in this repository directory:

### Option A: Homebrew Launcher (`.3dsx`)
1. Copy `cripto-3ds.3dsx` and `cripto-3ds.smdh` to your 3DS SD card under `/3ds/cripto-3ds/`.
2. Launch Homebrew Launcher from the 3DS Home Menu.
3. Select **Cripto-3DS** from the homebrew list.

### Option B: Home Menu CIA (`.cia`)
1. Copy `cripto-3ds.cia` to your 3DS SD card (e.g. `/cias/`).
2. Open **FBI** on your 3DS, navigate to `SD` -> `cias` -> `cripto-3ds.cia`.
3. Select **Install CIA** (or **Install and delete CIA**).
4. Launch directly from your 3DS Home Menu.

---

## Configuration (`sdmc:/cripto_cfg.txt`)

On first launch, the application prompts via the software keyboard for:
1. **Server IP**: Local IP address of your engine (e.g. `192.168.1.50`).
2. **Server Port**: Port configured for 3DS telemetry (default: `7343`).
3. **Auth PIN**: Security PIN matching `AUTH_PIN` in the engine `.env`.

These settings are automatically saved to `sdmc:/cripto_cfg.txt` on the root of your SD card.

### Manual Configuration
You can edit or create `sdmc:/cripto_cfg.txt` directly:
```text
192.168.1.50 7343 1234 0
```
Format: `<IP> <PORT> <AUTH_PIN> <THEME_INDEX>`

To re-configure from the console at any time, press **`SELECT`** to re-open the setup prompt.

---

## Hardware Controls & Navigation

| Control | Action |
| :--- | :--- |
| **D-Pad Left / Right** | Cycle through watchlist trading pairs |
| **L / R Shoulders** | Switch bottom screen tabs (Portfolio, Trade Logs, System Status) |
| **A Button** | Approve pending trade signal / Confirm modal |
| **B Button** | Reject pending trade signal / Cancel modal |
| **X Button** | Toggle bot state (Pause / Resume trading) |
| **Y Button** | Emergency Stop (Instant trading kill-switch) |
| **SELECT** | Open connection configuration wizard |
| **START** | Clean exit to 3DS Home Menu |
| **Touch Screen** | Direct touch interaction with on-screen buttons and modals |

---

## Building from Source

### Prerequisites
Install [devkitPro](https://devkitpro.org/wiki/Getting_Started) with the 3DS development payload:

```bash
# Arch Linux / Manjaro / Pacman
sudo pacman -S 3ds-dev 3ds-citro2d 3ds-citro3d 3ds-libctru

# Verify environment variables
export DEVKITPRO=/opt/devkitpro
export DEVKITARM=${DEVKITPRO}/devkitARM
```

### Build Binaries
```bash
cd cripto-3ds
make clean
make
```

Outputs generated:
- `cripto-3ds.3dsx`: Homebrew executable.
- `cripto-3ds.smdh`: Title metadata and icon.
- `cripto-3ds.cia`: Installable CTR Importable Archive (requires `makerom` and `bannertool` in `tools/` or system PATH).

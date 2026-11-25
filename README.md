# M5CardputerZeroPCBs

KiCad tooling for preparing PCB orders, driven from Claude Code through MCP servers.

## What's here

| Path | What it is |
| --- | --- |
| [`LightSensingPCB`](LightSensingPCB/README.md) | Cap carrying an Adafruit AS7343 14-channel spectral sensor breakout |
| [`BreadPortZero`](BreadPortZero/README.md) | Breakout cap: I2C (4 STEMMA QT / Qwiic, 2 sockets) on its own bus, buffered SPI, UART, 4 analog inputs (ADS1115) and USB-C supplemental power, with current limiting, a FAULT LED, a software power switch, I2C isolation and ESD protection for the host |
| [`tools/gerber_check`](tools/gerber_check/README.md) | `gerber-check`: checks Gerber and drill output against PCBWay and JLCPCB rules, lints the board and assembly files, and writes a ready-to-upload order package with an order sheet |
| `.mcp.json` | Registers three MCP servers for Claude Code: `kicad` (kicad-mcp-pro), `kicad-mixelpixx` (KiCAD-MCP-Server) and `gerber-check` |

## Setup (Windows)

- KiCad 10 installed in the default location (`kicad-cli` is used for exports and DRC). `gerber-check` and BreadPortZero's
  generators also find the per-user install that `winget install KiCad.KiCad` makes (`%LOCALAPPDATA%\Programs\KiCad\10.0`).
- [uv](https://docs.astral.sh/uv/) on `PATH`. `gerber-check` starts with `uv run`, which installs its dependencies on first use.
- Optional: `kicad-mcp-pro` installed with `uv tool install` (expected in `%USERPROFILE%\.local\bin`), and
  [KiCAD-MCP-Server](https://github.com/mixelpixx/KiCAD-MCP-Server) built in `C:\SourceCode\KiCAD-MCP-Server`.
- The paths in `.mcp.json` assume this repository is cloned to `C:\SourceCode\M5CardputerZeroPCBs`. Edit them if yours differs.

kicad-mcp-pro's first start can take about a minute, which is longer than Claude Code's default 30 s MCP startup
timeout. Set `MCP_TIMEOUT` (milliseconds) higher if it fails to connect.

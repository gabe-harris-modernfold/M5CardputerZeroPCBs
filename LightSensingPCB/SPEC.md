# LightSensingPCB: spec

Status: v0.1 designed 2026-11-24. KiCad DRC, ERC and schematic parity are clean, and gerber-check passes for
JLCPCB and PCBWay. Not yet fabricated.

## Purpose

A cap board that plugs into the M5Stack CardputerZero's top expansion port (EXT 2.54-14P) and carries an Adafruit
AS7343 14-channel light / colour sensor breakout. It gives the CardputerZero spectral light measurement (11 visible
bands, near-IR, clear and flicker) with no cables.

## Sensor module

Adafruit AS7343 14-Channel Light / Color Sensor Breakout, STEMMA QT / Qwiic, product ID 6477
([product page](https://www.adafruit.com/product/6477), [guide](https://learn.adafruit.com/adafruit-as7343-14-channel-light-color-sensor-breakout)).

| Item | Value | Source |
| --- | --- | --- |
| Sensor | ams OSRAM AS7343: 11 visible bands + NIR + clear + flicker = 14 channels, 16-bit 6-channel ADC with SMUX | Product page |
| Interface | I2C, fixed address **0x39** | Guide, Pinouts |
| Supply (VIN) | 3-5 V; the breakout regulates down for the sensor | Guide, Pinouts |
| I2C levels | MOSFET level shifting on the breakout; host-side pull-ups go to VIN | Product page, schematic |
| INT, GPIO | Open-drain | Guide, Pinouts |
| On-board extras | White illumination LED (AS7343 LED driver); green power LED (cut the jumper on the back to disable) | Guide, Pinouts |
| Board size | 1.00 x 0.70 in (25.4 x 17.8 mm) | Fab print |
| Header | 6-pin 0.1 in row: VIN, GND, SCL, SDA, GPIO, INT | Fab print |
| Price | $19.95 (1-9), $17.96 (10-99) | Product page, 2026-11-24 |

## Host interface: CardputerZero top port

From M5Stack's [CardputerZero docs](https://docs.m5stack.com/en/CardputerZero), its schematic (V0.6.1), the port
photo on that page, and the [Cap LoRa-1262](https://docs.m5stack.com/en/cap/Cap_LoRa-1262) drawing:

| Item | Value |
| --- | --- |
| Connector | 2x7, 2.54 mm, **female on the host**; a cap uses male pins |
| Orientation | Odd row (G26, G23, G22, G11, G10, G9, G7) on the keyboard side; pin 1 (G26) on the left looking down at the top edge |
| Position | Centred along the 84 mm top edge; rows 3.6 and 6.2 mm from the keyboard-side face (Cap drawing) |
| Power | 5VOUT (pin 6), switched by the host, **off by default** (turn on in Settings > ExtPort); under 500 mA shared with the Grove port. **No 3.3 V pin.** |
| Logic | 3.3 V, through level shifters and 33 ohm series resistors |
| I2C | I2C1: SDA G2 (pin 8), SCL G3 (pin 10). Already used: 0x10, 0x32, 0x34, 0x4F, 0x55, 0x68, so 0x39 is free |
| UART | Pin 12 = G14 TX, pin 14 = G15 RX (port photo and schematic; the docs table swaps them). Unused here |
| Hot-plug | Not supported: power the CardputerZero off before fitting the cap |

## Electrical design

| Net | Host pin | Breakout pin | Notes |
| --- | --- | --- | --- |
| +5V | 6 (5VOUT) | - | Feeds U1 only |
| +3V3 | - | VIN | From U1. VIN must be 3.3 V: the breakout pulls I2C up to VIN, and 5 V would back-drive the host's 3.3 V bus |
| GND | 4 | GND | GND pours on both layers |
| SDA | 8 (G2) | SDA | |
| SCL | 10 (G3) | SCL | |
| SENSOR_INT | 5 (G22) | INT | Open-drain; the breakout has its own pull-up |
| SENSOR_GPIO | 1 (G26) | GPIO | G26 is GPIO by default (HAT_SW low) |

Host pins 2 (5VIN), 3, 7, 9, 11, 12, 13 and 14 are not connected. 5VIN is left open so the cap never back-feeds the host.

U1 is an AP2112K-3.3 LDO (600 mA, enable tied to 5 V) with 10 uF in and out. That covers the breakout's
regulator, its power LED and the white LED at the AS7343's higher drive settings.

## Board

| Item | Value |
| --- | --- |
| Outline | 84 x 24 mm, same as M5Stack's Cap modules |
| Stack-up | 2 layers, 1.6 mm FR-4, 1 oz copper |
| Rules | 0.25 mm tracks (0.5 mm power), 0.3 mm clearance to pours, 0.8/0.3 mm vias, 0.3 mm copper to edge |
| J1 | 2x7 2.54 mm male header on the **bottom**, pins down into the host |
| J2 | 1x6 2.54 mm female socket on the top; the breakout plugs in, sensor facing up |
| U1, C1, C2 | Top side, right of the breakout |
| Retention | Header friction only. M5Stack's Caps also screw into the case with tabs; this board has none. The breakout's own holes aren't used, because standoffs there would hit J1's pins and J2's socket |

The breakout sits on the socket about 8.5 mm above the board and overhangs J1. J1's pins protrude about 3 mm on the top
side, well clear of it. The board covers the microSD slot on the top edge, as M5Stack's Caps do.

## Bill of materials

| Ref | Part | LCSC | Price each (LCSC, 2026-11-24) |
| --- | --- | --- | --- |
| J1 | BOOMELE 2.54-2*7P male header | C71397 | $0.108 (5+) |
| J2 | BOOMELE 2.54-1*6P female header | C40877 | $0.093 (5+) |
| U1 | Diodes AP2112K-3.3TRG1 | C51118 | $0.168 (5+), JLCPCB extended part |
| C1, C2 | Samsung CL21A106KAYNNNE 10 uF 25 V X5R 0805 | C15850 | $0.031 (20+), JLCPCB basic part |
| - | Adafruit AS7343 breakout | Adafruit 6477 | $19.95 |

## Cost (live quotes, 2026-11-24, 5 boards)

| Item | JLCPCB | PCBWay |
| --- | --- | --- |
| 5 bare boards, 2 layers, 1.6 mm, green | $5.20 lead-free HASL ($4.10 leaded); $3.20 via JLCPCB's desktop app | $5.00 (leaded HASL default) |
| Shipping to the US | $25.99 UPS Express Saver, duties paid (DDP) | $26.55 DHL; duties and VAT extra |
| LCSC parts for 5 boards (minimum quantities) | $2.47 + LCSC shipping | same |
| 5 Adafruit breakouts | $99.75 + Adafruit shipping | same |

## Fabrication files

Run `export_for_fab` (gerber-check) on `LightSensingPCB.kicad_pcb` with profile `jlcpcb_standard` or
`pcbway_standard`. It writes the fab zip, stencil zip, assembly files and an order sheet to `fab/<profile>/`
(ignored by git). Regenerate the design from `tools/` (see [README](README.md)).

## Verify before ordering

- The pin-1 orientation comes from M5Stack's port photo; check it against your unit's port silkscreen.
- Hand-assemble one and check 3.3 V at J2 pin 1 with 5VOUT enabled, before fitting the breakout.
- The 3D fit (header seating height, case lip at the top edge) has not been checked against the STL model.

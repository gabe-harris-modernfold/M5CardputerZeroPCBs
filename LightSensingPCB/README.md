# LightSensingPCB

A cap for the M5Stack CardputerZero's top expansion port that carries an Adafruit AS7343 14-channel spectral sensor
breakout. See [SPEC.md](SPEC.md) for the design, pinout, BOM and cost.

## Files

| File | What it is |
| --- | --- |
| `LightSensingPCB.kicad_pro`, `.kicad_sch`, `.kicad_pcb` | KiCad 10 project (generated; open and edit as usual) |
| `tools/design.py` | Board size, pin maps, parts and nets: the single source for both generators |
| `tools/gen_schematic.py` | Writes the schematic from `design.py` |
| `tools/gen_pcb.py` | Writes the PCB: placement, routing and GND pours |

## Rebuild

```powershell
python tools\gen_schematic.py
```

```powershell
& "C:\Program Files\KiCad\10.0\bin\python.exe" tools\gen_pcb.py
```

Then check it:

```powershell
& "C:\Program Files\KiCad\10.0\bin\kicad-cli.exe" pcb drc --schematic-parity --severity-all LightSensingPCB.kicad_pcb
```

Regenerating overwrites hand edits to the schematic and PCB. Once you start editing in KiCad, stop using the generators.

Fab output comes from the `gerber-check` MCP server's `export_for_fab` tool (profile `jlcpcb_standard` or
`pcbway_standard`), which writes to `fab/<profile>/`.

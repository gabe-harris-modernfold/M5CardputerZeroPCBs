# gerber-check

Prepares and checks a PCBWay or JLCPCB order from a KiCad board. It checks the files that actually go to the fab
(Gerbers, drills, paste, BOM, centroid) as well as the board itself, and writes the values to type into the fab's order
form so the form and the files can't disagree.

## Use it

As an MCP server (registered in the project `.mcp.json` as `gerber-check`):

| Tool | What it does |
| --- | --- |
| `export_for_fab(board_file, profile)` | The whole order package in `<board>/fab/<profile>/`: refills zones, exports every layer and PTH/NPTH drills, runs every check, and writes `<board>-<profile>-fab.zip` (bare board, no paste, `layer-order.txt` for multilayer), `<board>-<profile>-stencil.zip`, `assembly/` and `order_sheet.md` |
| `check_gerbers(path, profile, board_file)` | Checks an existing Gerber folder or `.zip`; with `board_file` it also compares copper against a fresh refill |
| `check_board_and_assembly(board_file, fab)` | Board lint, placement and PCBA files (`fab` = `pcbway` or `jlcpcb`) |
| `list_fab_profiles()` | Every rule per profile, with assumed and engineering-query rules marked |

Profiles: `pcbway_standard` (default), `pcbway_advanced`, `jlcpcb_standard`, `jlcpcb_advanced`. The profile picks the
rules, the order-sheet fields and the assembly file formats.

From a terminal:

```powershell
uv run --directory C:\SourceCode\M5CardputerZeroPCBs\tools\gerber_check python gerber_check.py <gerbers-folder-or-zip> --board <board.kicad_pcb> --profile jlcpcb_standard
```

```powershell
uv run --directory C:\SourceCode\M5CardputerZeroPCBs\tools\gerber_check python assembly_check.py <board.kicad_pcb> --fab jlcpcb
```

Exit code 0 = PASS, 1 = WARN, 2 = FAIL.

## PCBWay vs JLCPCB

| | PCBWay | JLCPCB |
| --- | --- | --- |
| Rules | PCBWay capabilities and help-centre pages | JLCPCB capabilities page; multilayer boards switch to 0.09 mm trace/space, 0.15 mm ring, 0.35 mm plated slot |
| Assembly files | `bom.csv` (MPN, manufacturer), `cpl.csv` | `bom_jlcpcb.csv` (Comment, Designator, Footprint, JLCPCB Part #), `cpl_jlcpcb.csv` (Designator, Mid X, Mid Y, Layer, Rotation) |
| Part numbers | MPN or distributor part number | LCSC number checked (JLCPCB places from its library) |
| Order sheet | PCBWay form fields; track/space, hole, thickness and via process mapped to the form's options | JLCPCB fields: thickness, copper weight from stackup, via covering, min via hole/diameter option, Mark on PCB |
| Fab-specific | Engineering-query rules (hole to edge 0.5 mm, PTH to trace 0.35 mm) warn; `WayWayWay` text places PCBWay's product number | No mark by default since May 2026: a leftover `JLCJLCJLCJLC` placeholder warns; silkscreen 1.0 mm text, 0.15 mm from pads |

Rotations in `cpl_jlcpcb.csv` are KiCad's. Check each part in JLCPCB's placement preview.

## Problems covered

PCBWay's common order problems:

| # | Problem | Covered by |
| --- | --- | --- |
| 1 | Order form vs Gerbers | `order_sheet.md` built from the files |
| 2 | Cutouts on the wrong layer | Closed shapes on drawing/user/fab/silk layers; empty closed silkscreen shapes |
| 3 | NPTH made as plated | Separate NPTH file; FAIL for NPTH sitting in copper |
| 4 | Missing layers | Required-files check; export ignores the board's own plot settings |
| 5 | Multilayer layer order | `layer-order.txt` and the order sheet |
| 6 | Via tenting | Tented / open / partly exposed / via-in-pad counts and the matching option |
| 7 | Edge and hole spacing | Copper to edge, hole to edge, hole to hole, slot widths |
| 8 | Silkscreen | Silk on or near pads, text size and stroke, mirrored text |
| 9 | Rings at the minimum | WARN within the ring margin |
| 10 | Panels | WARN for several boards in one outline |
| 11-15 | BOM, footprints, PCBA files, polarity, sensitive parts | Assembly check and `assembly_notes.md` |
| 16 | Solder bridges | Paste bridges, stencil, mask dams, fine-pitch note |
| 17-20 | Duties, customs, PCBA pricing, design service | Checklist in the order sheet |

General DFM/DFA problems:

| # | Problem | Covered by |
| --- | --- | --- |
| 1 | Annular ring | Outer and inner rings, margin warning |
| 2 | Trace width / spacing | Trace width and net clearance; order sheet maps the real minimum |
| 3 | Acid traps | Track segments of one net meeting under 90 degrees |
| 4 | Drill-to-copper | Plated and non-plated hole wall to other-net copper, inner isolation, wrong-net planes |
| 5 | Via aspect ratio | Board thickness over smallest plated drill: PCBWay standard warns over 8:1 and fails over 12:1; PCBWay advanced 16:1; JLCPCB 10:1 |
| 6 | Slivers and islands | Pour slivers under the minimum trace width; copper with no pad or via |
| 7 | Mask slivers | Mask dams |
| 8 | Missing mask openings | Pads covered by mask (FAIL), partly exposed (WARN); tented stitching/thermal vias and net ties recognised |
| 9 | Solder bridging | Clearance, paste bridges, mask dams |
| 10 | Open via-in-pad | Via-in-pad count and the filling/capping option |
| 11 | Tombstoning | Small two-pad parts with one pad on a solid pour and the other on a thin track (X2 pin data) |
| 12 | Paste apertures | Paste checks, windowpane hint, missing paste |
| 13 | Edge clearance for parts | Courtyards within 0.5 mm of the edge; ceramic capacitors within 3 mm (V-score / tab warning); edge connectors listed |
| 14 | Component spacing | KiCad DRC courtyard overlaps |
| 15 | Unverified footprints | KiCad DRC: footprints that differ from their library or whose library is missing |
| 16 | Silk over pads | Silk on pads and silk-to-pad clearance |
| 17 | Fiducials and test points | Fiducial count (WARN with fine-pitch/BGA), test point count |
| 18 | Copper balance | Coverage per layer; mirrored layers more than 30 points apart |
| 19 | Stack-up and finish | Odd layer counts; HASL with fine-pitch/BGA |
| 20 | Inconsistent files | Duplicate hits, BOM vs centroid, stale fills, drill offset, rotation note |

## Rules and sources

Every rule has a `_sources` note in `profiles.json`. `ASSUMED` (no published value), `ENGINEERING QUERY` (the fab
asks rather than rejects) and `HEURISTIC` rules can only raise a WARN. Override any rule for one run with
`--set rule=value` (CLI) or `overrides={...}` (MCP).

Sources: [PCBWay capabilities](https://www.pcbway.com/capabilities.html),
[PCBWay hole to edge](https://www.pcbway.com/helpcenter/Engineering_Questions/the_spacing_requirement_from_hole_to_the_edge_of_board.html),
[PCBWay hole to trace](https://www.pcbway.com/helpcenter/Engineering_Questions/the_spacing_from_hole_to_trace.html),
[PCBWay NPTH](https://www.pcbway.com/helpcenter/drill_hole_or_via_issues/NTPH_design_rule.html),
[PCBWay slots](https://www.pcbway.com/helpcenter/drill_hole_or_via_issues/Slots_standard_size___Limited_to_Quick-turn_PCB.html),
[PCBWay production issues](https://www.pcbway.com/helpcenter/production_issues/),
[JLCPCB capabilities](https://jlcpcb.com/capabilities/pcb-capabilities),
[JLCPCB BOM](https://jlcpcb.com/help/article/bill-of-materials-for-pcb-assembly),
[JLCPCB KiCad BOM/CPL](https://jlcpcb.com/help/article/how-to-generate-the-bom-and-centroid-file-from-kicad),
[JLCPCB mark on PCB](https://jlcpcb.com/help/article/how-to-mark-on-pcb),
[JLCPCB stencils](https://jlcpcb.com/capabilities/pcb-stencil-manufacturing).
Order-form options come from [PCBWay's form](https://www.pcbway.com/orderonline.aspx), its
[stencil form](https://www.pcbway.com/stencil.aspx) (default 0.12 mm) and [JLCPCB's form](https://cart.jlcpcb.com/quote),
all checked 2026-11-24. The stencil area ratio 0.66 and aspect ratio 1.5 are IPC-7525 values as cited by TI (SLUA271C)
and JLCPCB; IPC's own text (current revision IPC-7525C, 2021) is paywalled.

## Limits

- Silkscreen text height, sensitive parts, polarity, connectors and tombstoning use geometry or name heuristics:
  treat them as prompts to review.
- Clearance and several pad checks need X2 attributes (KiCad's default).
- Placement checks run KiCad's DRC, which adds 5-50 s per board.
- gerbonara drops X2 attributes on filled regions; `_region_nets` restores the net and pad function from the file.

## Validation (2026-11-24, KiCad 10.0.8 demos)

| Board | Notable results | KiCad DRC agrees |
| --- | --- | --- |
| pic_programmer | Stale-fill shorts; 4.3 mm hole 0.041 mm from R1; 7% vs 76% copper top/bottom; no part numbers; C7/C12 courtyards 0.3 mm from the edge | `hole_to_hole`, courtyard overlap P106/R1 |
| tiny_tapeout (4 layers) | Two shorts; 294 rings at the limit; 8 tombstone risks; 35 acute junctions; 112 of 120 parts without LCSC numbers | `shorting_items` |
| RoyalBlue54L Feather (8 layers) | 183 zero-size drills; 28 castellated holes; 93 via-in-pad; no fiducials with fine-pitch parts; stencil aperture at area ratio 0.60 | `drill_out_of_range`, `copper_edge_clearance`, `holes_co_located`, 11 courtyard overlaps |
| Synthetic | Paste bridges, tiny apertures, mirrored text, cutouts on Dwgs.User/F.SilkS, inner shorts, hole overlaps | n/a |

Built on [gerbonara](https://pypi.org/project/gerbonara/) and shapely.

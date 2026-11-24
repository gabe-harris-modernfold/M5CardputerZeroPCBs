"""MCP server for checking fab output (Gerber + Excellon) before ordering.

Tools:
  check_gerbers     - check a folder or .zip of Gerbers/drills against a fab profile
  export_for_fab    - export a complete Gerber + drill set from a .kicad_pcb (fresh zone refill), check it, zip it
  list_fab_profiles - show the bundled fab rule profiles
"""
from __future__ import annotations

import re
import subprocess
import zipfile
from pathlib import Path

import functools

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError

import assembly_check as ac
import gerber_check as gc
import order_sheet as osh

mcp = _Server("gerber-check")


def _clear_errors(fn):
    """Show the caller why a tool failed (bad path, unknown profile, kicad-cli error) instead of a generic error."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            raise ToolError(str(e)) from e
    return wrapper


def _summary(rep: dict) -> str:
    md = gc.to_markdown(rep)
    files = rep.get("report_files", []) + rep.get("previews", [])
    return md + "\nFiles written:\n" + "\n".join(f"- {f}" for f in files)


@mcp.tool()
def list_fab_profiles() -> str:
    """List every fab rule per profile (PCBWay and JLCPCB, mm). Rules marked (assumed) or (query) can only raise a WARN;
    by_layers shows rules that tighten on multilayer boards. Each value's source is in profiles.json (_sources)."""
    keys = [k for k in next(iter(gc.PROFILES.values())) if not k.startswith("_") and k not in ("manufacturer", "tier")]
    rows = ["| Rule | " + " | ".join(gc.PROFILES) + " |", "| --- |" + " --- |" * len(gc.PROFILES)]
    for k in keys:
        cells = []
        for r in gc.PROFILES.values():
            s = str(r.get("_sources", {}).get(k, ""))
            tag = " (assumed)" if s.startswith("ASSUMED") else " (query)" if s.startswith("ENGINEERING QUERY") else ""
            cells.append(f"{r.get(k, '')}{tag}")
        rows.append(f"| {k} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


@mcp.tool()
@_clear_errors
def check_gerbers(path: str, profile: str = "pcbway_standard", board_file: str = "", out_dir: str = "",
                  overrides: dict[str, float] | None = None) -> str:
    """Check exported Gerber + drill files before sending them to a fab.

    path: folder of Gerber/drill files, or a .zip of them.
    profile: fab rules to apply (see list_fab_profiles), default pcbway_standard.
    board_file: optional .kicad_pcb; when given, copper is compared against a fresh zone refill to catch stale fills.
    out_dir: where to write report.md, report.json and SVG previews (default: <path>/_gerber_check).
    overrides: optional rule overrides in mm, e.g. {"min_mask_dam_mm": 0.08} (see list_fab_profiles for rule names).
    Checks: required files, closed outline + size, stale zone fills, drill sizes (incl. 0 mm tools), drill-to-copper
    alignment, trace width, net-to-net clearance and shorts (X2 net attributes; bridged jumpers reported as WARN),
    annular ring, copper to edge (castellated holes recognised), inner-layer rings, isolation and wrong-net plane
    shorts, plated-hole wall to other-net copper, NPTH-to-copper, hole-to-hole spacing (overlapping holes, duplicate hits), solder mask dams, silk on pads, silkscreen text height and stroke.
    """
    rep = gc.run(path, profile, board_file or None, out_dir or None, overrides)
    return _summary(rep)


def _inner_layers(board: Path) -> list[str]:
    text = board.read_text(encoding="utf-8", errors="replace")
    head = text[: text.find("(setup")] if "(setup" in text else text[:20000]
    return sorted(set(re.findall(r'"(In\d+\.Cu)"', head)), key=lambda s: int(s[2:-3]))


@mcp.tool()
@_clear_errors
def export_for_fab(board_file: str, profile: str = "pcbway_standard", out_dir: str = "", make_zip: bool = True,
                   overrides: dict[str, float] | None = None) -> str:
    """Export a complete PCBWay or JLCPCB order package (pick with profile) from a .kicad_pcb, check it, and write the order sheet.

    Exports every copper layer, both masks, both silkscreens, both paste layers and Edge.Cuts with zones refilled
    (kicad-cli --check-zones; the board file itself is not saved), plus separate PTH/NPTH Excellon drill files
    with the same origin. Runs check_gerbers and check_board_and_assembly, then writes to <board folder>/fab/<profile>:
    <board>-<profile>-fab.zip (bare board, no paste layers, layer-order.txt for multilayer), <board>-<profile>-stencil.zip
    (paste + outline, only when there is paste), assembly/ (bom.csv, cpl.csv, assembly_notes.md, drawings), and
    order_sheet.md (values for PCBWay's order form and a pre-payment checklist). Earlier zips for this board are replaced.
    """
    board = Path(board_file).resolve()
    if not board.exists():
        raise FileNotFoundError(f"No such board file: {board}")
    if profile not in gc.PROFILES:
        raise ValueError(f"Unknown profile '{profile}'. Known: {', '.join(gc.PROFILES)}")
    cli = gc.find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli not found; set KICAD_CLI")
    out = Path(out_dir).resolve() if out_dir else board.parent / "fab" / profile
    gerb = out / "gerbers"
    gerb.mkdir(parents=True, exist_ok=True)
    for old in gerb.iterdir():  # never mix files from an earlier export
        if old.is_file():
            old.unlink()
    layers = ["F.Cu", *_inner_layers(board), "B.Cu", "F.Mask", "B.Mask", "F.Silkscreen", "B.Silkscreen",
              "F.Paste", "B.Paste", "Edge.Cuts"]
    steps = [
        [cli, "pcb", "export", "gerbers", "--check-zones", "-l", ",".join(layers), "-o", str(gerb) + "\\", str(board)],
        [cli, "pcb", "export", "drill", "--format", "excellon", "--excellon-separate-th", "--excellon-units", "mm",
         "-o", str(gerb) + "\\", str(board)],
    ]
    for cmd in steps:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd[1:4])} failed: {r.stderr.strip() or r.stdout.strip()}")
    rep = gc.run(str(gerb), profile, str(board), str(out / "check"), overrides)
    text = _summary(rep)
    arep = ac.run_board(str(board), str(out / "assembly"), gc.PROFILES[profile].get("manufacturer_key", "pcbway"))
    text += "\n\n" + arep["markdown"]
    fab_zip = stencil_zip = None
    for old in out.glob(f"{board.stem}-{profile}*.zip"):  # zips from an earlier export of this board: never leave stale ones
        old.unlink()
    if make_zip:
        paste_files = {l["file"] for l in rep["layers"] if l["kind"] == "paste"}
        has_paste = any(l["objects"] for l in rep["layers"] if l["kind"] == "paste")
        copper = [l for l in rep["layers"] if l["kind"] == "copper"]
        fab_zip = out / f"{board.stem}-{profile}-fab.zip"
        with zipfile.ZipFile(fab_zip, "w", zipfile.ZIP_DEFLATED) as z:  # bare board: no paste layers (PCBWay advice)
            for f in sorted(gerb.iterdir()):
                if f.is_file() and f.name not in paste_files:
                    z.write(f, f.name)
            if len(copper) > 2:
                order = sorted(copper, key=osh.layer_number)
                z.writestr("layer-order.txt", "Layer order, top to bottom:\n" +
                           "".join(f"L{i}: {l['file']}\n" for i, l in enumerate(order, 1)))
        if has_paste:
            stencil_zip = out / f"{board.stem}-{profile}-stencil.zip"
            with zipfile.ZipFile(stencil_zip, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(gerb.iterdir()):
                    if f.is_file() and (f.name in paste_files or "Edge_Cuts" in f.name):
                        z.write(f, f.name)
        note = "" if rep["verdict"] != "FAIL" else " (verdict is FAIL: fix the findings before uploading)"
        text += f"\n- {fab_zip}{note}" + (f"\n- {stencil_zip}" if stencil_zip else "")
    sheet = out / "order_sheet.md"
    sheet.write_text(osh.build(rep, arep, gerb, fab_zip, stencil_zip), encoding="utf-8")
    text += f"\n- {sheet} (values for PCBWay's order form, plus the pre-payment checklist)"
    return text


@mcp.tool()
@_clear_errors
def check_board_and_assembly(board_file: str, out_dir: str = "", fab: str = "pcbway") -> str:
    """Lint a .kicad_pcb and prepare PCBA files for PCBWay.

    Checks mirrored silkscreen text, closed shapes on drawing/silk/fab layers that may be cutouts on the wrong layer,
    footprint SMD/THT type vs pads, BOM part numbers and consistency, BOM vs centroid designators, polarity marking,
    and parts needing special handling (wash, temperature). Writes bom.csv, cpl.csv, assembly_notes.md and assembly
    drawings to out_dir (default: <board folder>/assembly). fab='jlcpcb' also writes bom_jlcpcb.csv (Comment, Designator,
    Footprint, JLCPCB Part #) and cpl_jlcpcb.csv (Designator, Mid X, Mid Y, Layer, Rotation), checks LCSC part numbers,
    and flags a leftover 'JLCJLCJLCJLC' order-number placeholder (JLCPCB retired it in May 2026). fab='pcbway' checks
    for PCBWay's 'WayWayWay' product-number location text.
    """
    if fab not in ("pcbway", "jlcpcb"):
        raise ValueError("fab must be 'pcbway' or 'jlcpcb'")
    return ac.run_board(board_file, out_dir or None, fab)["markdown"]


if __name__ == "__main__":
    mcp.run()

"""Board-file lint and PCBA (assembly) file preparation for PCBWay or JLCPCB.

Reads the .kicad_pcb directly (footprints carry the schematic fields in KiCad 8+), so no schematic is needed.

Board lint:
  - silkscreen text mirrored the wrong way (front text mirrored, back text not mirrored)
  - closed shapes on drawing/user/fab/silk layers inside the board: cutouts drawn on the wrong layer
  - footprint type (SMD / through-hole) that disagrees with its pads
Assembly:
  - BOM with part numbers; missing / inconsistent part numbers; BOM vs centroid designator match
  - centroid (pick-and-place) file from kicad-cli
  - polarity / pin-1 risk: polarized parts with no silkscreen marking
  - sensitive parts that need wash / temperature notes; THT and double-sided notes
  - assembly drawings (PDF) for orientation

Usage: uv run assembly_check.py <board.kicad_pcb> [--out DIR]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from gerber_check import Check, STATUS_RANK, find_kicad_cli

TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')
PN_FIELD = re.compile(r"^(mpn|mfr.?p/?n|mfr.?part.*|manufacturer.?part.*|part.?number|mf.?pn|lcsc.*|digi.?key.*|mouser.*|jlc.*|"
                      r"supplier.?part.*|pcbway.*)$", re.I)
LCSC_FIELD = re.compile(r"^(lcsc.*|jlc.*)$", re.I)
JLC_PLACEHOLDER = "JLCJLCJLCJLC"  # retired by JLCPCB in May 2026; now flagged if left on a board
PCBWAY_PLACEHOLDER = "WayWayWay"  # marks where PCBWay prints its product number ('Specify a location')
MFR_FIELD = re.compile(r"^(manufacturer|mfr|mfg|manufacturer.?name)$", re.I)
CUTOUT_LAYERS = {"Dwgs.User", "Cmts.User", "Eco1.User", "Eco2.User", "Margin", "F.SilkS", "B.SilkS", "F.Fab", "B.Fab",
                 *(f"User.{i}" for i in range(1, 10))}
POLARIZED_REF = re.compile(r"^(D|LED|Q|U|IC|BT|CP|VR|Y|X|SW|J|P)\d", re.I)
POLARIZED_FP = re.compile(r"(CP_|Elec|Tantal|LED|Diode|SOD|SOT|QFN|DFN|SOIC|SSOP|TSSOP|MSOP|QFP|BGA|LGA|WLCSP|PLCC|DIP)", re.I)
SENSITIVE = [
    ("no ultrasonic wash", re.compile(r"MEMS|micro.?phone|\bMIC\b|SPH\d|ICS-?4|pressure|baro|\bBM[EPI]\d|\bLPS\d|humid|\bSHT\d|"
                                      r"\bHDC\d|accel|gyro|\bIMU\b|\bLSM\d|\bLIS\d|\bMPU-?\d|\bICM-?\d|crystal|xtal|oscillat|TCXO|"
                                      r"buzzer|speaker|resonator", re.I)),
    ("reflow temperature limits", re.compile(r"\bLED|\bWS28|SK68|APA10|battery|\bBT\d|super.?cap|electrolytic|\bCP_|switch|button|"
                                             r"\bSW_|connector|USB|header|jack|socket|relay|display|OLED|LCD", re.I)),
]


# ---------------------------------------------------------------- s-expression
def parse_sexpr(text: str):
    stack, cur = [], []
    for m in TOKEN.finditer(text):
        tok = m.group(0)
        if tok == "(":
            stack.append(cur)
            cur = []
        elif tok == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        else:
            cur.append(tok[1:-1].replace('\\"', '"') if tok.startswith('"') else tok)
    return cur[0] if cur else []


def kids(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def kid(node, name):
    k = kids(node, name)
    return k[0] if k else None


def _flag(node, name):
    """True for a bare token (old style) or (name yes) (new style)."""
    if name in node:
        return True
    k = kid(node, name)
    return bool(k) and (len(k) == 1 or k[1] in ("yes", "true"))


def _xy(node, name="at"):
    k = kid(node, name)
    return (float(k[1]), float(k[2])) if k and len(k) >= 3 else None


# ---------------------------------------------------------------- board model
def read_board(path: Path):
    root = parse_sexpr(path.read_text(encoding="utf-8", errors="replace"))
    fps = []
    for fp in kids(root, "footprint"):
        layer = (kid(fp, "layer") or [None, "F.Cu"])[1]
        attr = kid(fp, "attr") or ["attr"]
        props = {}
        for p in kids(fp, "property"):
            if len(p) >= 3:
                props[p[1]] = p[2]
        for t in kids(fp, "fp_text"):  # KiCad 6/7
            if len(t) >= 3 and t[1] in ("reference", "value"):
                props.setdefault(t[1].capitalize(), t[2])
        pads = [(p[1], p[2]) for p in kids(fp, "pad") if len(p) >= 3]
        silk = [g for g in fp if isinstance(g, list) and g and g[0] in ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly")
                and (kid(g, "layer") or [None, ""])[1].endswith("SilkS")]
        texts = []
        for t in kids(fp, "fp_text") + kids(fp, "property"):
            tl = (kid(t, "layer") or [None, ""])[1]
            if tl.endswith("SilkS"):
                eff = kid(t, "effects") or []
                hidden = "hide" in t or _flag(t, "hide") or "hide" in eff or _flag(eff, "hide")
                just = kid(eff, "justify") or []
                texts.append({"layer": tl, "text": t[2] if t[0] == "property" else t[2], "hidden": hidden,
                              "mirror": "mirror" in just})
        at = kid(fp, "at") or ["at", 0, 0]
        fps.append({
            "lib": fp[1] if len(fp) > 1 and isinstance(fp[1], str) else "",
            "layer": layer, "side": "bottom" if layer.startswith("B.") else "top",
            "ref": props.get("Reference", "?"), "value": props.get("Value", ""), "props": props,
            "type": "smd" if "smd" in attr else "through_hole" if "through_hole" in attr else "unspecified",
            "board_only": "board_only" in attr, "no_pos": "exclude_from_pos_files" in attr or _flag(fp, "exclude_from_pos_files"),
            "no_bom": "exclude_from_bom" in attr or _flag(fp, "exclude_from_bom"),
            "dnp": "dnp" in attr or _flag(fp, "dnp"),
            "pads": pads, "silk": silk, "texts": texts,
            "x": float(at[1]), "y": float(at[2]),
        })
    graphics = []
    for g in root:
        if not (isinstance(g, list) and g and g[0] in ("gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_poly", "gr_text")):
            continue
        graphics.append(g)
    return root, fps, graphics


def _edge_bbox(graphics):
    xs, ys = [], []
    for g in graphics:
        if (kid(g, "layer") or [None, ""])[1] != "Edge.Cuts":
            continue
        for key in ("start", "end", "mid", "center"):
            p = _xy(g, key)
            if p:
                xs.append(p[0]); ys.append(p[1])
        pts = kid(g, "pts")
        for xy in kids(pts or [], "xy"):
            xs.append(float(xy[1])); ys.append(float(xy[2]))
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


# ---------------------------------------------------------------- lint
def lint_board(board_path: Path, manufacturer: str = "pcbway"):
    _, fps, graphics = read_board(board_path)
    mir = Check("silk_mirror", "Silkscreen text orientation")
    cut = Check("cutout_layers", "Shapes that may be cutouts on the wrong layer")
    typ = Check("fp_type", "Footprint type matches its pads")
    # mirrored text
    bad = []
    for g in graphics:
        if g[0] != "gr_text":
            continue
        layer = (kid(g, "layer") or [None, ""])[1]
        eff = kid(g, "effects") or []
        if layer.endswith("SilkS") and not ("hide" in eff or _flag(eff, "hide")):
            m = "mirror" in (kid(eff, "justify") or [])
            if (layer == "B.SilkS") != m:
                bad.append((g[1], layer, m, _xy(g)))
    for f in fps:
        for t in f["texts"]:
            if t["hidden"]:
                continue
            if (t["layer"] == "B.SilkS") != t["mirror"]:
                bad.append((f"{f['ref']}: {t['text']}", t["layer"], t["mirror"], (f["x"], f["y"])))
    for text, layer, m, at in bad[:10]:
        mir.details.append(f"'{text[:30]}' on {layer} is {'mirrored' if m else 'not mirrored'}"
                           + (f" at ({at[0]:.2f}, {at[1]:.2f})" if at else ""))
    if bad:
        mir.worsen("WARN", f"{len(bad)} silkscreen text(s) mirrored the wrong way: back-side text must be mirrored and "
                           "front-side text must not, or it reads backwards on the board")
    else:
        mir.message = "Front text reads normally, back text is mirrored."
    # cutouts on the wrong layer
    bb = _edge_bbox(graphics)
    shapes = []
    for g in graphics:
        layer = (kid(g, "layer") or [None, ""])[1]
        if g[0] not in ("gr_rect", "gr_circle", "gr_poly") or layer not in CUTOUT_LAYERS:
            continue
        fill = kid(g, "fill")
        if fill and len(fill) > 1 and fill[1] in ("solid", "yes"):
            continue  # filled shapes are artwork, not cutouts
        if g[0] == "gr_rect":
            (x1, y1), (x2, y2) = _xy(g, "start"), _xy(g, "end")
        elif g[0] == "gr_circle":
            (cx, cy), (ex, ey) = _xy(g, "center"), _xy(g, "end")
            r = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
            x1, y1, x2, y2 = cx - r, cy - r, cx + r, cy + r
        else:
            pts = [(float(p[1]), float(p[2])) for p in kids(kid(g, "pts") or [], "xy")]
            if len(pts) < 3:
                continue
            x1, y1 = min(p[0] for p in pts), min(p[1] for p in pts)
            x2, y2 = max(p[0] for p in pts), max(p[1] for p in pts)
        x1, x2, y1, y2 = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)
        if bb and not (bb[0] < x1 and x2 < bb[2] and bb[1] < y1 and y2 < bb[3]):
            continue
        if bb and (x2 - x1) * (y2 - y1) > 0.8 * (bb[2] - bb[0]) * (bb[3] - bb[1]):
            continue
        if max(x2 - x1, y2 - y1) < 0.5:
            continue
        shapes.append((g[0][3:], layer, (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1))
    for kind, layer, x, y, w, h in shapes[:10]:
        cut.details.append(f"{kind} {w:.2f} x {h:.2f} mm on {layer} at ({x:.2f}, {y:.2f})")
    if shapes:
        cut.worsen("WARN", f"{len(shapes)} closed shape(s) inside the board on drawing/silk/fab layers: if any is a cutout or "
                           "slot, move it to Edge.Cuts (fabs only cut the outline and drill layers)")
    else:
        cut.message = "No closed shapes on drawing, silk or fab layers inside the board."
    # footprint type vs pads
    smd_tht, tht_smd, unspec, hybrid = [], [], [], []
    for f in fps:
        kinds_ = {t for _, t in f["pads"]}
        if not f["pads"] or f["board_only"]:
            continue
        if f["type"] == "smd" and kinds_ and kinds_ <= {"thru_hole", "np_thru_hole"}:
            smd_tht.append(f["ref"])
        elif f["type"] == "smd" and "thru_hole" in kinds_:
            hybrid.append(f["ref"])
        elif f["type"] == "through_hole" and kinds_ and kinds_ <= {"smd", "connect"}:
            tht_smd.append(f["ref"])
        elif f["type"] == "unspecified" and kinds_ & {"smd", "thru_hole"} and not f["ref"].startswith(("#", "TP", "H", "FID", "MH")):
            unspec.append(f["ref"])
    if smd_tht:
        typ.worsen("WARN", f"Marked SMD but every pad is through-hole: {', '.join(smd_tht[:15])}: an SMT line can't place "
                           "these, and the BOM package will look wrong to the assembler")
    if hybrid:
        typ.worsen("INFO", f"SMD with some through-hole pads (shield pegs, thermal vias): {', '.join(hybrid[:15])}: "
                           "fine, but confirm the assembler solders the pegs")
    if tht_smd:
        typ.worsen("WARN", f"Marked through-hole but only SMD pads: {', '.join(tht_smd[:15])}: the assembler will quote it wrongly")
    if unspec:
        typ.worsen("INFO", f"Type unspecified (left out of the position file): {', '.join(unspec[:15])}")
    if typ.status == "PASS":
        typ.message = "Footprint types match their pads."
    checks = [mir, cut, typ]
    if manufacturer == "jlcpcb":
        on = Check("order_number", "JLCPCB board mark")
        texts = [str(g[1]) for g in graphics if g[0] == "gr_text"] + [t["text"] for f in fps for t in f["texts"]]
        found = any(JLC_PLACEHOLDER in str(x).upper() for x in texts)
        if found:  # jlcpcb.com/help/article/how-to-mark-on-pcb, checked 2026-11-24
            on.worsen("WARN", f"'{JLC_PLACEHOLDER}' text found: JLCPCB stopped printing order numbers in May 2026 and asks "
                              "you not to add this text. Delete it, or it may be printed as ordinary silkscreen")
            on.message = f"Leftover '{JLC_PLACEHOLDER}' order-number placeholder: delete it."
        else:
            on.message = ("No order-number placeholder. JLCPCB adds no mark by default; for a 2D barcode or serial number "
                          "in a chosen spot, draw a solid silkscreen square (5x5, 8x8 or 10x10 mm; 2x10 mm for a number "
                          "only) and pick 'Specify Position'.")
        on.data = {"placeholder": found}
        checks.append(on)
    else:  # pcbway.com/orderonline.aspx 'Remove product No.' help text, checked 2026-11-24
        pn = Check("product_number", "PCBWay product number")
        texts = [str(g[1]) for g in graphics if g[0] == "gr_text"] + [t["text"] for f in fps for t in f["texts"]]
        found = any(PCBWAY_PLACEHOLDER.upper() in str(x).upper() for x in texts)
        pn.message = (f"'{PCBWAY_PLACEHOLDER}' text found: choose 'Specify a location' under Remove product No." if found else
                      f"PCBWay prints its product number where it chooses. To place it, add '{PCBWAY_PLACEHOLDER}' on "
                      "silkscreen and choose 'Specify a location' (free); removing it costs extra.")
        if not found:
            pn.worsen("INFO")
        pn.data = {"placeholder": found}
        checks.append(pn)
    return checks, fps


# ---------------------------------------------------------------- assembly
def _pn(props):
    for k, v in props.items():
        if PN_FIELD.match(k) and v.strip() and v.strip() not in ("~", "-", "DNP", "NA", "N/A"):
            return k, v.strip()
    return None, ""


def _mfr(props):
    for k, v in props.items():
        if MFR_FIELD.match(k) and v.strip() not in ("", "~"):
            return v.strip()
    return ""


def _lcsc(props):
    for k, v in props.items():
        if LCSC_FIELD.match(k) and v.strip() not in ("", "~", "-"):
            return v.strip()
    return ""


def check_assembly(board_path: Path, out_dir: Path, fps=None, manufacturer: str = "pcbway"):
    out_dir.mkdir(parents=True, exist_ok=True)
    if fps is None:
        _, fps, _ = read_board(board_path)
    bom_c = Check("bom", "BOM part numbers and designators")
    cpl_c = Check("cpl", "Centroid file matches the BOM")
    pol_c = Check("polarity", "Polarity and pin-1 marking")
    sens_c = Check("sensitive", "Parts needing special handling")
    parts = [f for f in fps if f["type"] in ("smd", "through_hole") and not f["board_only"] and not f["no_bom"]
             and not f["ref"].startswith("#")]
    fitted = [f for f in parts if not f["dnp"]]
    dnp = [f for f in parts if f["dnp"]]
    # BOM rows grouped like PCBWay asks: same part on one line
    groups = defaultdict(list)
    for f in fitted:
        key, pn = _pn(f["props"])
        groups[(f["value"], f["lib"].split(":")[-1], pn, _mfr(f["props"]))].append(f)
    missing = sorted((f["ref"] for f in fitted if not _pn(f["props"])[1]), key=_natural)
    rows = []
    for i, ((val, pkg, pn, mfr), fs) in enumerate(sorted(groups.items(), key=lambda kv: _natural(kv[1][0]["ref"])), 1):
        refs = sorted((f["ref"] for f in fs), key=_natural)
        rows.append({"Item": i, "Designator": ",".join(refs), "Qty": len(refs), "Manufacturer": mfr,
                     "Manufacturer Part Number": pn, "Description/Value": val, "Package/Footprint": pkg,
                     "Type": "SMD" if fs[0]["type"] == "smd" else "THT",
                     "Side": "/".join(sorted({f["side"] for f in fs}))})
    bom_path = out_dir / "bom.csv"
    with open(bom_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["Item"])
        w.writeheader()
        w.writerows(rows)
    if dnp:
        bom_c.details.append(f"Do not populate (left out of the BOM): {', '.join(sorted((f['ref'] for f in dnp), key=_natural))}")
    if missing:
        bom_c.worsen("WARN", f"{len(missing)} of {len(fitted)} fitted parts have no manufacturer or distributor part number: "
                             f"{', '.join(missing[:20])}{' ...' if len(missing) > 20 else ''} (the assembler needs one per line)")
    # consistency: same value+footprint with different part numbers, or one part number on different values/footprints
    by_vf, by_pn = defaultdict(set), defaultdict(set)
    for (val, pkg, pn, _), fs in groups.items():
        if pn:
            by_vf[(val, pkg)].add(pn)
            by_pn[pn].add((val, pkg))
    for (val, pkg), pns in by_vf.items():
        if len(pns) > 1:
            bom_c.worsen("WARN", f"{val} / {pkg} has different part numbers: {', '.join(sorted(pns))} (check it's intended)")
    for pn, vfs in by_pn.items():
        if len(vfs) > 1:
            bom_c.worsen("WARN", f"Part number {pn} is used for different values/footprints: "
                                 + "; ".join(f"{v} / {p}" for v, p in sorted(vfs)))
    if manufacturer == "jlcpcb":
        no_lcsc = sorted((f["ref"] for f in fitted if not _lcsc(f["props"])), key=_natural)
        if no_lcsc:
            bom_c.worsen("WARN", f"{len(no_lcsc)} of {len(fitted)} fitted parts have no LCSC part number: "
                                 f"{', '.join(no_lcsc[:20])}{' ...' if len(no_lcsc) > 20 else ''} (JLCPCB places parts from "
                                 "its LCSC library; others need global sourcing or consigned parts)")
        jrows = []
        for (val, pkg, pn, mfr), fs in sorted(groups.items(), key=lambda kv: _natural(kv[1][0]["ref"])):
            refs = sorted((f["ref"] for f in fs), key=_natural)
            jrows.append({"Comment": val, "Designator": ",".join(refs), "Footprint": pkg,
                          "JLCPCB Part #": _lcsc(fs[0]["props"])})
        with open(out_dir / "bom_jlcpcb.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["Comment", "Designator", "Footprint", "JLCPCB Part #"])
            w.writeheader()
            w.writerows(jrows)
        bom_c.details.append("bom_jlcpcb.csv: Comment, Designator, Footprint, JLCPCB Part # (LCSC)")
    bom_c.data = {"lines": len(rows), "fitted": len(fitted), "dnp": len(dnp), "missing_pn": len(missing),
                  "smd": sum(f["type"] == "smd" for f in fitted), "tht": sum(f["type"] == "through_hole" for f in fitted),
                  "sides": sorted({f["side"] for f in fitted})}
    if bom_c.status == "PASS":
        bom_c.message = f"{len(rows)} BOM lines, every fitted part has a part number."
    # centroid file from kicad-cli, compared with the BOM
    cli = find_kicad_cli()
    cpl_path = out_dir / "cpl.csv"
    if cli:
        r = subprocess.run([cli, "pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both",
                            "--exclude-dnp", "-o", str(cpl_path), str(board_path)], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            cpl_c.worsen("FAIL", f"kicad-cli position export failed: {r.stderr.strip()[:200]}")
    else:
        cpl_c.worsen("WARN", "kicad-cli not found: no centroid file")
    if cpl_path.exists():
        with open(cpl_path, encoding="utf-8") as fh:
            cpl_refs = {row["Ref"] for row in csv.DictReader(fh)}
        bom_refs = {f["ref"] for f in fitted}
        smd_refs = {f["ref"] for f in fitted if f["type"] == "smd"}
        not_in_cpl = sorted(smd_refs - cpl_refs, key=_natural)
        not_in_bom = sorted(cpl_refs - bom_refs, key=_natural)
        if not_in_cpl:
            cpl_c.worsen("FAIL", f"SMD parts in the BOM but missing from the centroid file: {', '.join(not_in_cpl[:20])} "
                                 "(check 'exclude from position files')")
        excluded = {f["ref"] for f in fps if f["no_bom"] or f["dnp"] or f["board_only"] or f["type"] == "unspecified"}
        stray = [r for r in not_in_bom if r not in excluded]
        if stray:
            cpl_c.worsen("WARN", f"In the centroid file but not the BOM: {', '.join(stray[:20])}")
        if len(stray) < len(not_in_bom):
            cpl_c.worsen("INFO", "In the centroid file but deliberately left out of the BOM (fiducials, mounting holes, "
                                 f"jumpers): {', '.join([r for r in not_in_bom if r in excluded][:20])}")
        cpl_c.details.append(f"{len(cpl_refs)} placements written to cpl.csv (mm, KiCad rotation, both sides)")
        if manufacturer == "jlcpcb":
            with open(cpl_path, encoding="utf-8") as fh, open(out_dir / "cpl_jlcpcb.csv", "w", newline="", encoding="utf-8") as fo:
                w = csv.DictWriter(fo, fieldnames=["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
                w.writeheader()
                for row in csv.DictReader(fh):
                    w.writerow({"Designator": row["Ref"], "Mid X": f"{row['PosX']}mm", "Mid Y": f"{row['PosY']}mm",
                                "Layer": "Bottom" if row["Side"].lower().startswith("b") else "Top", "Rotation": row["Rot"]})
            cpl_c.details.append("cpl_jlcpcb.csv: Designator, Mid X, Mid Y, Layer, Rotation (mm). Rotations are KiCad's: "
                                 "check every part in JLCPCB's placement preview and fix any that are turned")
        if cpl_c.status == "PASS":
            cpl_c.message = "Centroid file and BOM list the same SMD designators."
    # polarity marking
    unmarked = [f["ref"] for f in fitted if (POLARIZED_REF.match(f["ref"]) or POLARIZED_FP.search(f["lib"]))
                and len(f["pads"]) >= 2 and not f["silk"]]
    if unmarked:
        pol_c.worsen("WARN", f"{len(unmarked)} polarized or multi-pin part(s) with no silkscreen marking: "
                             f"{', '.join(sorted(unmarked, key=_natural)[:20])}: add a pin-1 / polarity mark")
    pol_c.details.append("Assembly drawings (assembly_top.pdf / assembly_bottom.pdf) show fab-layer outlines and pin-1 marks")
    if pol_c.status == "PASS":
        pol_c.message = "Every polarized part has silkscreen marking (check the drawings for pin 1)."
    if cli:
        for side, layers, extra in (("top", "F.Fab,F.SilkS,Edge.Cuts", []), ("bottom", "B.Fab,B.SilkS,Edge.Cuts", ["--mirror"])):
            if any(f["side"] == side for f in fitted):
                subprocess.run([cli, "pcb", "export", "pdf", "--mode-single", "-l", layers, *extra,
                                "-o", str(out_dir / f"assembly_{side}.pdf"), str(board_path)],
                               capture_output=True, text=True, timeout=300)
    # sensitive parts, THT, double-sided
    notes = defaultdict(list)
    for f in fitted:
        blob = " ".join([f["ref"], f["value"], f["lib"], *f["props"].values()])
        for label, rx in SENSITIVE:
            if rx.search(blob):
                notes[label].append(f["ref"])
    tht = sorted((f["ref"] for f in fitted if f["type"] == "through_hole"), key=_natural)
    maker = "JLCPCB" if manufacturer == "jlcpcb" else "PCBWay"
    lines = [f"# Assembly notes for {maker}", "", "Paste these into the order's remarks, edited to suit.", ""]
    for label, refs in notes.items():
        refs = sorted(set(refs), key=_natural)
        sens_c.worsen("INFO", f"{label}: {', '.join(refs[:25])}{' ...' if len(refs) > 25 else ''}")
        lines.append(f"- **{label.capitalize()}:** {', '.join(refs)}")
    if tht:
        sens_c.worsen("INFO", f"{len(tht)} through-hole part(s): hand or wave soldered, quoted separately")
        lines.append(f"- **Through-hole (hand/wave solder):** {', '.join(tht)}")
    if len(bom_c.data["sides"]) > 1:
        sens_c.worsen("INFO", "Parts on both sides: double-sided assembly (second reflow, higher cost)")
        lines.append("- **Double-sided assembly:** parts on top and bottom")
    if dnp:
        lines.append(f"- **Do not populate:** {', '.join(sorted((f['ref'] for f in dnp), key=_natural))}")
    lines += ["", f"Rotation is KiCad's convention (counter-clockwise, top view). Ask {maker} to confirm part orientation "
              "against assembly_top.pdf / assembly_bottom.pdf before production."]
    (out_dir / "assembly_notes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sens_c.data = {k: sorted(set(v), key=_natural) for k, v in notes.items()}
    if sens_c.status == "PASS":
        sens_c.message = "No parts flagged for special handling."
    return [bom_c, cpl_c, pol_c, sens_c]


MLCC_FP = re.compile(r"^C_(0201|0402|0603|0805|1206|1210|1812|2220)", re.I)


def check_placement(board_path: Path, fps, edge_keepout: float = 0.5):
    """Component-to-edge (courtyards), component spacing and footprint/library checks via KiCad DRC, test points."""
    import tempfile
    import warnings
    from gerbonara import GerberFile
    import gerber_check as gc

    edge_c = Check("comp_edge", "Components clear of the board edge")
    drc_c = Check("comp_drc", "Component spacing and footprint verification")
    tp_c = Check("test_points", "Test points and fiducials")
    cli = find_kicad_cli()
    if not cli:
        for c in (edge_c, drc_c):
            c.worsen("WARN", "kicad-cli not found: skipped")
        return [edge_c, drc_c, tp_c]
    by_ref = {f["ref"]: f for f in fps}
    with tempfile.TemporaryDirectory(prefix="crtyd_") as tmp:
        tmp = Path(tmp)
        subprocess.run([cli, "pcb", "export", "gerbers", "-l", "F.Courtyard,B.Courtyard,Edge.Cuts", "-o", str(tmp) + "\\",
                        str(board_path)], capture_output=True, text=True, timeout=300)
        warnings.filterwarnings("ignore")
        edge_files = list(tmp.glob("*Edge_Cuts*"))
        board = None
        if edge_files:
            el = gc.Layer(edge_files[0], "Profile,NP", "profile", "none", 0, GerberFile.open(edge_files[0]))
            _, board = gc.build_outline([el], None)
        comps = {}
        for f in sorted(tmp.glob("*Courtyard*")):
            side = "bottom" if "B_Courtyard" in f.name else "top"
            lay = gc.Layer(f, "Other,User", "other", side, 0, GerberFile.open(f))
            lay.load_geometry()
            for feat in lay.features:
                if feat.ref:
                    comps.setdefault(feat.ref, []).append(feat.geom)
        if board is None:
            edge_c.worsen("INFO", "No closed outline: skipped")
        else:
            edge = board.boundary
            near, mlcc, conns = [], [], []
            for ref, gs in comps.items():
                g = gc.unary_union(gs).convex_hull
                d = 0.0 if not board.contains(g) else g.distance(edge)
                fp = by_ref.get(ref, {})
                lib = fp.get("lib", "").lower()
                if re.match(r"^(H|MH|MT|FID|P1\d\d)\d*", ref, re.I) and ("mount" in lib or "fiducial" in lib or not fp.get("pads")
                                                                         or all(k == "np_thru_hole" for _, k in fp.get("pads", []))
                                                                         or ref.upper().startswith(("H", "MH", "MT", "FID"))):
                    continue  # mounting holes and fiducials sit where they must
                if d < edge_keepout - 1e-6:
                    if re.match(r"^(J|P|CN|USB)\d", ref, re.I) or any(k in lib for k in ("conn", "usb", "jack", "header", "socket")):
                        conns.append(ref)
                    else:
                        near.append((ref, d))
                if MLCC_FP.match(fp.get("lib", "").split(":")[-1]) and d < 3.0:
                    mlcc.append(ref)
            if near:
                near.sort(key=lambda x: x[1])
                edge_c.worsen("WARN", f"{len(near)} component(s) with courtyards within {edge_keepout} mm of (or over) the "
                                      f"edge: {', '.join(f'{r} {d:.2f} mm' for r, d in near[:12])}")
            if conns:
                edge_c.worsen("INFO", f"{len(conns)} connector(s) at the edge (expected for edge-mounted connectors): "
                                      f"{', '.join(sorted(conns, key=_natural)[:15])}")
            if mlcc:
                edge_c.worsen("INFO", f"{len(mlcc)} ceramic capacitor(s) within 3 mm of the edge ({', '.join(sorted(mlcc, key=_natural)[:15])}): "
                                      "if that edge is a V-score or breakaway tab, move them back or turn them parallel to it "
                                      "(depaneling cracks MLCCs)")
            edge_c.data = {"near": len(near), "mlcc_near": len(mlcc), "edge_connectors": len(conns)}
            if edge_c.status == "PASS":
                edge_c.message = f"Every courtyard keeps {edge_keepout} mm from the edge."
    # KiCad DRC: courtyard overlaps (nozzle / AOI / rework space) and footprints that differ from their library
    r = subprocess.run([cli, "pcb", "drc", "--format", "json", "--severity-all", "-o", str(board_path.parent / ".drc_tmp.json"),
                        str(board_path)], capture_output=True, text=True, timeout=600)
    drc_path = board_path.parent / ".drc_tmp.json"
    if drc_path.exists():
        try:
            d = json.loads(drc_path.read_text(encoding="utf-8"))
        finally:
            drc_path.unlink()
        def refs(kind):
            out = []
            for v in d.get("violations", []):
                if v.get("type") == kind:
                    out.append(" / ".join(i["description"].replace("Footprint ", "") for i in v.get("items", [])))
            return out
        ov, mm, li, mc = refs("courtyards_overlap"), refs("lib_footprint_mismatch"), refs("lib_footprint_issues"), refs("missing_courtyard")
        if ov:
            drc_c.worsen("WARN", f"{len(ov)} courtyard overlap(s): {'; '.join(ov[:10])} (pick-and-place nozzles, AOI and rework need the space)")
        if mc:
            drc_c.worsen("INFO", f"{len(mc)} footprint(s) without a courtyard: {', '.join(mc[:10])}")
        if mm:
            drc_c.worsen("WARN", f"{len(mm)} footprint(s) differ from their library copy: {', '.join(mm[:15])} "
                                 "(check pads, pitch and pin 1 against the datasheet)")
        if li:
            drc_c.worsen("INFO", f"{len(li)} footprint(s) whose library can't be found, so they can't be verified: {', '.join(li[:10])}")
        drc_c.data = {"overlaps": len(ov), "lib_mismatch": len(mm), "lib_missing": len(li)}
        if drc_c.status == "PASS":
            drc_c.message = "No courtyard overlaps; footprints match their libraries."
    else:
        drc_c.worsen("WARN", f"KiCad DRC did not run: {r.stderr.strip()[:200]}")
    # test points and fiducials (design for test / assembly)
    tps = [f["ref"] for f in fps if re.match(r"^TP\d", f["ref"], re.I) or "testpoint" in f["lib"].lower()]
    fids = [f["ref"] for f in fps if re.match(r"^FID", f["ref"], re.I) or "fiducial" in f["lib"].lower()]
    tp_c.details.append(f"{len(tps)} test point(s), {len(fids)} fiducial footprint(s)")
    if not tps:
        tp_c.worsen("INFO", "No test points: add them on power rails and key signals for bring-up and production test")
    if len(fids) < 2 and any(f["type"] == "smd" for f in fps):
        tp_c.worsen("INFO", f"{len(fids)} fiducial(s): add 2-3 global fiducials (plus local ones by fine-pitch parts) for "
                            "pick-and-place and AOI")
    tp_c.data = {"test_points": len(tps), "fiducials": len(fids)}
    if tp_c.status == "PASS":
        tp_c.message = f"{len(tps)} test points, {len(fids)} fiducials."
    return [edge_c, drc_c, tp_c]


def _natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def run_board(board: str, out: str | None = None, manufacturer: str = "pcbway") -> dict:
    bp = Path(board).resolve()
    if not bp.exists():
        raise FileNotFoundError(f"No such board file: {bp}")
    out_dir = Path(out).resolve() if out else bp.parent / "assembly"
    lint, fps = lint_board(bp, manufacturer)
    checks = lint + check_assembly(bp, out_dir, fps, manufacturer) + check_placement(bp, fps)
    for c in checks:
        if c.status in ("WARN", "FAIL") and not c.message:
            first = next((d for d in c.details if d.startswith(f"[{c.status}]")), None)
            c.message = first.split("] ", 1)[1] if first else c.title
        elif not c.message:
            first = next((d for d in c.details if d.startswith("[INFO]")), None)
            c.message = first.split("] ", 1)[1] if first else "OK"
    verdict = max((c.status for c in checks), key=lambda s: STATUS_RANK[s], default="PASS")
    verdict = "PASS" if verdict == "INFO" else verdict
    files = sorted(str(p) for p in out_dir.iterdir() if p.is_file())
    rep = {"verdict": verdict, "board": str(bp), "checks": [c.__dict__ for c in checks], "files": files}
    (out_dir / "assembly_report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    md = [f"# Board and assembly check: {verdict}", "", f"Board: `{bp}`", "", "| Check | Status | Result |", "| --- | --- | --- |"]
    md += [f"| {c.title} | {c.status} | {c.message} |" for c in checks]
    for c in checks:
        if c.details:
            md += ["", f"## {c.title} ({c.status})"] + [f"- {d}" for d in c.details]
    md += ["", "Files: " + ", ".join(f"`{Path(f).name}`" for f in files)]
    (out_dir / "assembly_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    rep["markdown"] = "\n".join(md)
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("board")
    ap.add_argument("--out")
    ap.add_argument("--fab", choices=["pcbway", "jlcpcb"], default="pcbway")
    a = ap.parse_args(argv)
    rep = run_board(a.board, a.out, a.fab)
    sys.stdout.reconfigure(encoding="utf-8")
    print(rep["markdown"])
    return {"PASS": 0, "INFO": 0, "WARN": 1, "FAIL": 2}[rep["verdict"]]


if __name__ == "__main__":
    sys.exit(main())

"""Build order_sheet.md: the values to enter on PCBWay's order form, taken from the files being uploaded.

Most PCBWay holds come from the order form disagreeing with the Gerbers (layer count, thickness, via process, layer
order). This sheet is generated from the same files, so copying it into the form keeps them in step.
"""
from __future__ import annotations

import json
from pathlib import Path

# Option lists as shown on the live order forms (pcbway.com/orderonline.aspx, HighQualityOrderOnline.aspx and
# cart.jlcpcb.com/quote, checked 2026-11-24). Re-check them if a fab changes its form.
TRACE_OPTIONS_MM = [(0.2032, "8/8 mil"), (0.1524, "6/6 mil"), (0.127, "5/5 mil"), (0.1016, "4/4 mil"), (0.0762, "3/3 mil")]
HOLE_OPTIONS_MM = [1.0, 0.8, 0.3, 0.25, 0.2, 0.15]
THICKNESS_MM = {
    "pcbway_standard": [0.2, 0.3, 0.4, 0.6, 0.8, 1.0, 1.2, 1.6, 2.0, 2.4, 2.6, 2.8, 3.0, 3.2],
    "pcbway_advanced": [0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.6, 2.0, 2.4, 2.8, 3.2, 3.6, 4.0, 4.4, 4.8, 5.2, 5.6, 6.0, 7.0, 8.0],
    "jlcpcb": [0.4, 0.6, 0.8, 1.0, 1.2, 1.6, 2.0],  # 1-2 layer FR-4; the list changes with layer count
}
PCBWAY_VIA = {"Tented vias": "Tenting vias", "Untented (open) vias": "Vias not covered"}
JLC_VIA = {"Tented vias": "Tented", "Untented (open) vias": "Untented"}
JLC_VIA_HOLE = [(0.3, "0.3mm/(0.4/0.45mm)"), (0.25, "0.25mm/(0.35/0.4mm)"), (0.2, "0.2mm/(0.3/0.35mm)"),
                (0.15, "0.15mm/(0.25/0.3mm)"), (0.1, "0.1mm/(0.2/0.25mm)")]
PCBWAY_MARK = "WayWayWay"


def _check(rep, cid):
    return next((c for c in (rep or {}).get("checks", []) if c["id"] == cid), {"data": {}, "status": "PASS", "details": []})


def _pick_trace(v):
    for mm, label in TRACE_OPTIONS_MM:
        if v + 1e-4 >= mm:
            return label
    return "below 3/3 mil: not manufacturable as standard"


def _pick_hole(v):
    for mm in HOLE_OPTIONS_MM:
        if v + 1e-4 >= mm:
            return f"{mm} mm"
    return "below 0.15 mm: not manufacturable as standard"


def _thickness(v, options):
    v = float(v)
    near = min(options, key=lambda c: abs(c - v))
    return f"{near} mm" + ("" if abs(near - v) < 0.01 else f" (stackup adds up to {v:g} mm; pick the nearest option)")


def _pick_jlc_via(v):
    for mm, label in JLC_VIA_HOLE:
        if v + 1e-4 >= mm:
            return label
    return "below 0.1 mm: not offered"


def layer_number(l):
    """Copper position from X2 FileFunction (Copper,L1,Top ... Copper,Ln,Bot), top = 1."""
    try:
        return int(l["function"].split(",")[1][1:])
    except (IndexError, ValueError):
        return {"top": 0, "bottom": 999}.get(l["side"], 500)


def _copper_oz(job):
    for layer in job.get("MaterialStackup", []):
        if str(layer.get("Type", "")).lower() == "copper" and layer.get("Thickness"):
            th = float(layer["Thickness"])
            return f"{max(1, round(th / 0.035))} oz (stackup copper {th} mm)"
    return "1 oz (default; not in the job file)"


def build(rep: dict, arep: dict | None, gerb_dir: Path, fab_zip: Path | None, stencil_zip: Path | None) -> str:
    job = {}
    jobs = list(gerb_dir.glob("*.gbrjob"))
    if jobs:
        try:
            job = json.loads(jobs[0].read_text(encoding="utf-8", errors="replace"))
        except Exception:
            job = {}
    rules = rep.get("rules") or {}
    jlc = rules.get("manufacturer_key") == "jlcpcb"
    thick_opts = THICKNESS_MM["jlcpcb" if jlc else "pcbway_advanced" if rules.get("tier") == "advanced" else "pcbway_standard"]
    maker = "JLCPCB" if jlc else "PCBWay"
    spec = job.get("GeneralSpecs", {})
    layers = rep.get("layers", [])
    copper = sorted([l for l in layers if l["kind"] == "copper"], key=layer_number)
    outline = _check(rep, "outline")["data"]
    drills = _check(rep, "drills")["data"]
    tw = _check(rep, "trace_width")["data"]
    cl = _check(rep, "clearance")["data"]
    edge = _check(rep, "copper_edge")["data"]
    vias = _check(rep, "vias")["data"]
    paste = _check(rep, "paste")["data"]
    stencil = _check(rep, "stencil")

    def has(kind, side):
        return any(l["kind"] == kind and l["side"] == side and l["objects"] for l in layers)

    def sides(kind):
        if has(kind, "top") and has(kind, "bottom"):
            return "both sides"
        return "top only" if has(kind, "top") else "bottom only" if has(kind, "bottom") else "none"

    min_feature = min(tw.get("min_trace_mm", 9), cl.get("min_gap_mm", 9))
    size = outline.get("size_mm") or [spec.get("Size", {}).get("X"), spec.get("Size", {}).get("Y")]
    finish = spec.get("Finish") or "None"
    fine = any(s.get("fine_pitch_pads") or s.get("bga_pads") for s in paste.values() if isinstance(s, dict))
    finish_row = finish if finish != "None" else ("ENIG (fine-pitch/BGA pads need a flat finish)" if fine else
                                                  "your choice: lead-free HASL or ENIG")
    designs = "1" if outline.get("boards", 1) <= 1 else f"{outline['boards']} (order as panel / multiple designs)"
    dims = f"{size[0]} x {size[1]} mm" if size and size[0] else "see outline"
    castell = f"Yes ({edge['castellated']} holes)" if edge.get("castellated") else "No"
    slots = f"{drills.get('slots', 0)} slot(s), {outline.get('cutouts', 0)} internal cutout(s)"
    if jlc:
        an = _check(arep or {}, "order_number")
        if vias.get("in_pad"):
            via_cover = "Epoxy Filled & Capped (via-in-pad)"
        else:
            via_cover = JLC_VIA.get(vias.get("order_option"), "per the Gerber (add a note in PCB Remark)")
        mark = ("Remove Mark (the default); delete the leftover 'JLCJLCJLCJLC' text first: JLCPCB dropped order numbers "
                "in May 2026" if an["data"].get("placeholder") else
                "Remove Mark (the default), or 2D barcode (Serial Number) with a solid silkscreen square for 'Specify Position'")
        via_min = drills.get("min_via_hole_mm") or drills.get("min_hole_mm")
        rows = [
            ("Base Material", "FR-4"),
            ("Layers", f"{len(copper)}"),
            ("Dimensions", dims),
            ("Different Design", designs),
            ("PCB Thickness", _thickness(spec.get("BoardThickness", 1.6), thick_opts)),
            ("Surface Finish", finish_row),
            ("Outer Copper Weight", _copper_oz(job)),
            ("Via Covering", via_cover),
            ("Min via hole size/diameter", (f"{_pick_jlc_via(float(via_min))} (measured hole {via_min} mm, "
                                            f"pad {vias.get('min_via_pad_mm', '?')} mm)") if via_min else "default"),
            ("Castellated Holes", castell),
            ("Mark on PCB", mark),
            ("Silkscreen / mask", f"silkscreen {sides('legend')}, mask {sides('mask')}"),
            ("Slots / cutouts", slots),
        ]
        intro = ("Enter these on JLCPCB's order page (field names and options as on cart.jlcpcb.com/quote on 2026-11-24). "
                 "The values come from the uploaded files.")
    else:
        rows = [
            ("Layers", f"{len(copper)}"),
            ("Size (X x Y)", dims),
            ("Different designs in panel", designs),
            ("Thickness", _thickness(spec.get("BoardThickness", 1.6), thick_opts)),
            ("Min track/spacing", f"{_pick_trace(min_feature)} (measured {min_feature:.3f} mm)" if min_feature < 9 else "6/6 mil"),
            ("Min hole size", f"{_pick_hole(drills.get('min_hole_mm', 0.3))} (measured {drills.get('min_hole_mm', '?')} mm)"),
            ("Solder mask", sides("mask")),
            ("Silkscreen", sides("legend")),
            ("Surface finish", finish_row),
            ("Via process", PCBWAY_VIA.get(vias.get("order_option"), vias.get("order_option", "n/a")) +
             (f"; plus via-in-pad filling and capping for {vias['in_pad']} via(s) (Advanced PCB: 'Via in pad')"
              if vias.get("in_pad") else "")),
            ("Castellated holes", castell),
            ("Slots / cutouts", slots + " (in the drill and outline files)"),
        ]
        if rules.get("tier") != "advanced":  # the Advanced PCB form has no product-number field
            pn = _check(arep or {}, "product_number")["data"]
            rows.append(("Remove product No.", f"Specify a location (the '{PCBWAY_MARK}' text is on the board)"
                         if pn.get("placeholder") else f"No (PCBWay places it), Yes (extra charge), or add '{PCBWAY_MARK}' "
                         "on silkscreen and pick Specify a location (free)"))
        intro = ("Enter these on PCBWay's order form (options as on pcbway.com on 2026-11-24). They come from the files "
                 "in the zip, so the form and the Gerbers agree.")
    out = [f"# {maker} order sheet", "", intro, "", "## Bare board", "", "| Form field | Value |", "| --- | --- |"]
    out += [f"| {k} | {v} |" for k, v in rows]
    note = "; PCBWay advises leaving GTP/GBP out, or pads can come back uncovered)." if not jlc else ")."
    out += ["", f"Upload: `{fab_zip.name if fab_zip else 'gerbers/'}` (bare board without paste layers{note}"]
    if len(copper) > 2:
        out += ["", "## Layer order (top to bottom, for the multilayer note)", ""]
        out += [f"{i}. L{i}: `{l['file']}`" for i, l in enumerate(copper, 1)]
    if stencil_zip:
        t = rules.get("stencil_thickness_mm", 0.12)
        thin = fine or stencil.get("status") == "WARN"
        out += ["", "## Stencil", "", f"- Upload `{stencil_zip.name}` (paste layers + outline).",
                f"- Thickness: {0.10 if thin else t} mm" + (" (fine-pitch parts or area-ratio warnings)" if thin else ""),
                "- Sides: " + ", ".join(s for s, d in paste.items() if isinstance(d, dict) and d.get("apertures"))]
    if arep:
        bom = _check(arep, "bom")["data"]
        sens = _check(arep, "sensitive")["data"]
        files = "`assembly/bom_jlcpcb.csv`, `assembly/cpl_jlcpcb.csv`" if jlc else "`assembly/bom.csv`, `assembly/cpl.csv`"
        out += ["", "## Assembly (PCBA)", "",
                f"- Unique parts (BOM lines): {bom.get('lines', '?')}; SMD placements: {bom.get('smd', '?')}; "
                f"through-hole: {bom.get('tht', '?')}; sides: {', '.join(bom.get('sides', [])) or '?'}",
                f"- Do not populate: {bom.get('dnp', 0)} part(s) (left out of the BOM and centroid file)",
                f"- Upload {files} and the assembly drawings `assembly/assembly_*.pdf`.",
                "- Paste the special-handling lines from `assembly/assembly_notes.md` into the order remarks."]
        if jlc:
            out += ["- Check every part's rotation in JLCPCB's placement preview: the centroid file uses KiCad's rotation.",
                    "- Economic PCBA lists an Extended Components Fee: prefer Basic library parts where you can."]
        for label, refs in sens.items():
            out.append(f"  - {label}: {', '.join(refs[:15])}{' ...' if len(refs) > 15 else ''}")
        if bom.get("missing_pn"):
            out.append(f"- {bom['missing_pn']} part(s) have no part number yet: {maker} can't quote them until they do.")
    open_items = [(c["title"], c["status"]) for r in (rep, arep) if r for c in r.get("checks", [])
                  if c["status"] in ("FAIL", "WARN")]
    out += ["", "## Open findings", ""]
    out += [f"- [{s}] {t}" for t, s in open_items] or ["- None"]
    out += ["", "## Before you pay", "",
            "- [ ] Every form field above matches the order form (a mismatch puts the order on an engineering hold).",
            "- [ ] Shipping: pick a duty-paid option (DDP/DTP) if offered, or budget for import VAT, duty and the courier's "
            "brokerage fee; they aren't in the board price.",
            "- [ ] Schedule: leave slack for customs inspection on international express shipments.",
            f"- [ ] PCBA: parts price and stock are confirmed after {maker}'s review; submit early.",
            "- [ ] Watch your email: engineering questions pause the order until you answer."]
    if not jlc:
        out.append("- [ ] If you use PCBWay's design service, give a written spec and agree revision turnaround up front.")
    return "\n".join(out) + "\n"

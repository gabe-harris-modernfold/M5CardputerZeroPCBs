"""Check exported Gerber + Excellon files against a fab profile (PCBWay standard by default; JLCPCB profiles too).

Checks the files that go to the fab, not the KiCad design:
  1. completeness   - every required layer and drill file present and non-empty
  2. outline        - closed board outline, size matches the job file
  3. fills          - copper matches a fresh zone refill of the board (needs --board)
  4. drills         - min drill size, every plated hole lands on copper (alignment)
  5. dfm            - min trace width, net-to-net clearance, annular ring, copper to edge
  6. silk           - silkscreen over exposed pads
  7. render         - top/bottom SVG previews with problem markers

Usage:  uv run gerber_check.py <gerber dir or .zip> [--profile pcbway_standard] [--board x.kicad_pcb] [--out DIR]
Exit code: 0 PASS, 1 WARN, 2 FAIL.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from shapely import STRtree
from shapely.geometry import LineString, Point, Polygon, box
from shapely.affinity import rotate as shp_rotate, translate as shp_translate
from shapely.ops import nearest_points, polygonize_full, unary_union

warnings.filterwarnings("ignore")  # gerbonara warns on harmless KiCad header quirks
from gerbonara import ExcellonFile, GerberFile  # noqa: E402
from gerbonara.utils import MM  # noqa: E402
import gerbonara.graphic_primitives as gp  # noqa: E402

HERE = Path(__file__).resolve().parent
PROFILES = {k: v for k, v in json.loads((HERE / "profiles.json").read_text()).items() if not k.startswith("_")}
ARC_SEG_MM = 0.05
MAX_LOCATIONS = 10
STATUS_RANK = {"PASS": 0, "INFO": 0, "WARN": 1, "FAIL": 2}

EXT_FUNCTIONS = {  # fallback when a file has no X2 FileFunction and no job file entry
    ".gtl": "Copper,L1,Top", ".gbl": "Copper,Ln,Bot", ".gts": "Soldermask,Top", ".gbs": "Soldermask,Bot",
    ".gto": "Legend,Top", ".gbo": "Legend,Bot", ".gtp": "Paste,Top", ".gbp": "Paste,Bot",
    ".gm1": "Profile,NP", ".gko": "Profile,NP", ".gml": "Profile,NP", ".gm": "Profile,NP",
}


# ---------------------------------------------------------------- report model
@dataclass
class Check:
    id: str
    title: str
    status: str = "PASS"
    message: str = ""
    details: list = field(default_factory=list)
    locations: list = field(default_factory=list)  # [{"x","y","side","note"}]
    data: dict = field(default_factory=dict)  # structured facts for the order sheet

    def worsen(self, status: str, message: str | None = None):
        if STATUS_RANK[status] > STATUS_RANK[self.status] or (self.status == "PASS" and status == "INFO"):
            self.status = status
        if message:
            self.details.append(f"[{status}] {message}")

    def add_loc(self, x, y, side, note):
        if len(self.locations) < MAX_LOCATIONS:
            self.locations.append({"x": round(x, 3), "y": round(-y, 3), "side": side, "note": note})


# ---------------------------------------------------------------- geometry
def _arc_points(p1, p2, center, clockwise):
    cx, cy = center
    r = math.hypot(p1[0] - cx, p1[1] - cy)
    a1 = math.atan2(p1[1] - cy, p1[0] - cx)
    a2 = math.atan2(p2[1] - cy, p2[0] - cx)
    sweep = (a1 - a2) if clockwise else (a2 - a1)
    while sweep <= 1e-9:
        sweep += 2 * math.pi
    n = max(2, min(720, math.ceil(r * sweep / ARC_SEG_MM)))
    sign = -1 if clockwise else 1
    return [(cx + r * math.cos(a1 + sign * sweep * i / n), cy + r * math.sin(a1 + sign * sweep * i / n)) for i in range(n + 1)]


def _prim_geom(p):
    if isinstance(p, gp.Line):
        if p.width <= 0:
            return None
        if math.hypot(p.x2 - p.x1, p.y2 - p.y1) < 1e-9:
            return Point(p.x1, p.y1).buffer(p.width / 2)
        return LineString([(p.x1, p.y1), (p.x2, p.y2)]).buffer(p.width / 2)
    if isinstance(p, gp.Arc):
        if p.width <= 0:
            return None
        return LineString(_arc_points((p.x1, p.y1), (p.x2, p.y2), (p.cx, p.cy), p.clockwise)).buffer(p.width / 2)
    if isinstance(p, gp.Circle):
        return Point(p.x, p.y).buffer(p.r)
    if isinstance(p, gp.Rectangle):
        r = box(-p.w / 2, -p.h / 2, p.w / 2, p.h / 2)
        if p.rotation:
            r = shp_rotate(r, p.rotation, origin=(0, 0), use_radians=True)
        return shp_translate(r, p.x, p.y)
    if isinstance(p, gp.ArcPoly):
        pts = []
        ring = list(p.outline)
        if ring and tuple(ring[0]) != tuple(ring[-1]):
            ring.append(ring[0])  # closing segment is implied
        for seg, arc in itertools.zip_longest(itertools.pairwise(ring), p.arc_centers or []):
            if seg is None:
                break
            a, b = seg
            if arc is None:
                pts.append(a)
            else:
                cw, c = arc
                pts.extend(_arc_points(a, b, c, cw)[:-1])
        if len(pts) < 3:
            return None
        poly = Polygon(pts)
        return poly if poly.is_valid else poly.buffer(0)
    return None


def _object_geom(obj):
    acc = None
    for prim in obj.to_primitives(MM):
        g = _prim_geom(prim)
        if g is None or g.is_empty:
            continue
        if getattr(prim, "polarity_dark", True):
            acc = g if acc is None else acc.union(g)
        elif acc is not None:
            acc = acc.difference(g)
    return acc


def _aper_function(obj):
    v = _aper_attrs(obj)
    return v[0] if v else None


def _aper_attrs(obj):
    ap = getattr(obj, "aperture", None)
    attrs = getattr(ap, "attrs", None) or ()
    for k, v in attrs:
        if k == ".AperFunction":
            return tuple(v)
    return ()


@dataclass
class Feature:
    geom: object
    net: str | None
    func: str | None
    kind: str  # Line / Arc / Flash / Region
    width: float | None = None
    ref: str | None = None  # X2 component reference (.C or .P)
    pin: str | None = None  # X2 pad pin number (.P)
    smdef: bool = False  # solder-mask-defined pad (opening smaller than copper on purpose)
    ends: tuple | None = None  # (x1, y1, x2, y2) for straight tracks


@dataclass
class Layer:
    path: Path
    function: str  # e.g. "Copper,L1,Top"
    kind: str  # copper / mask / legend / paste / profile / drill_pth / drill_npth / other
    side: str  # top / bottom / inner / none
    index: int = 0
    file: object = None
    features: list = field(default_factory=list)
    union: object = None
    has_nets: bool = False
    _parts: list = field(default=None, repr=False)
    _tree: object = field(default=None, repr=False)

    def tree(self):
        """Separate copper/opening shapes of this layer plus a spatial index over them (cached)."""
        if self._parts is None:
            self.load_geometry()
            u = self.union if self.union is not None else Polygon()
            self._parts = [p for p in getattr(u, "geoms", [u]) if not p.is_empty]
            self._tree = STRtree(self._parts) if self._parts else None
        return self._parts, self._tree

    def load_geometry(self):
        if self.features or self.file is None:
            return
        clear = []
        region_attrs = _region_nets(self.path) if self.kind in ("copper", "paste") else []
        region_i = 0
        for obj in self.file.objects:
            rnet = rfunc = None
            if type(obj).__name__ == "Region":  # gerbonara drops X2 attrs on regions
                rnet, rfunc = region_attrs[region_i] if region_i < len(region_attrs) else (None, None)
                region_i += 1
            g = _object_geom(obj)
            if g is None or g.is_empty:
                continue
            attrs = getattr(obj, "attrs", None) or {}
            nv = attrs.get(".N") if isinstance(attrs, dict) else None
            net = (nv[0] if nv else None) or rnet
            if net is not None:
                self.has_nets = True
            if not getattr(obj, "polarity_dark", True):
                clear.append(g)
                continue
            width = None
            kind = type(obj).__name__
            if kind in ("Line", "Arc"):
                ap = getattr(obj, "aperture", None)
                try:
                    width = ap.equivalent_width(MM)
                except Exception:
                    width = None
            pv = attrs.get(".P") if isinstance(attrs, dict) else None
            cv = attrs.get(".C") if isinstance(attrs, dict) else None
            ends = None
            if kind == "Line":
                prim = next(iter(obj.to_primitives(MM)), None)
                if isinstance(prim, gp.Line):
                    ends = (prim.x1, prim.y1, prim.x2, prim.y2)
            self.features.append(Feature(g, net or None, _aper_function(obj) or rfunc, kind, width,
                                         ref=(pv[0] if pv else cv[0] if cv else None), pin=(pv[1] if pv and len(pv) > 1 else None),
                                         smdef="SMDef" in _aper_attrs(obj), ends=ends))
        u = unary_union([f.geom for f in self.features]) if self.features else Polygon()
        if clear:
            cu = unary_union(clear)
            u = u.difference(cu)
            for f in self.features:
                f.geom = f.geom.difference(cu)
        self.union = u


def _region_nets(path: Path):
    """(net, aperture function) in force at each G36 region, in file order.

    X2 %TO.N and %TA.AperFunction stay set until deleted with %TD (all), %TD.N or %TD.AperFunction.
    """
    out, net, func = [], None, None
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return out
    for m in re.finditer(r"%TO\.N,([^*]*)\*%|%TA\.AperFunction,([^*]*)\*%|%TD(\.N|\.AperFunction)?\*%|G36\*", text):
        tok = m.group(0)
        if tok.startswith("%TO.N"):
            net = m.group(1).split(",")[0] or None
        elif tok.startswith("%TA.AperFunction"):
            func = m.group(2).split(",")[0] or None
        elif tok.startswith("%TD"):
            which = m.group(3)
            if which in (None, ".N"):
                net = None
            if which in (None, ".AperFunction"):
                func = None
        else:
            out.append((net, func))
    return out


def _classify(function: str):
    f = [x.strip() for x in function.split(",")]
    head = f[0].lower()
    side = "none"
    if "Top" in f:
        side = "top"
    elif "Bot" in f:
        side = "bottom"
    if head == "copper":
        idx = int(f[1][1:]) if len(f) > 1 and f[1][1:].isdigit() else 0
        if "Inr" in f:
            side = "inner"
        return "copper", side, idx
    if head == "soldermask":
        return "mask", side, 0
    if head == "legend":
        return "legend", side, 0
    if head in ("paste", "solderpaste"):
        return "paste", side, 0
    if head == "profile":
        return "profile", "none", 0
    if head == "plated":
        return "drill_pth", "none", 0
    if head == "nonplated":
        return "drill_npth", "none", 0
    return "other", side, 0


def _x2_function(path: Path):
    try:
        with open(path, "r", errors="replace") as fh:
            head = fh.read(4000)
    except OSError:
        return None
    m = re.search(r"%TF\.FileFunction,([^*]+)\*%", head) or re.search(r"TF\.FileFunction,([^\r\n]+)", head)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------- loading
def _unpack(path: Path, workdir: Path) -> Path:
    if path.is_file() and path.suffix.lower() == ".zip":
        dest = workdir / "unzipped"
        with zipfile.ZipFile(path) as z:
            for m in z.infolist():  # no path traversal
                target = (dest / m.filename).resolve()
                if dest.resolve() not in target.parents and target != dest.resolve():
                    continue
                z.extract(m, dest)
        subdirs = [p for p in dest.iterdir() if p.is_dir()]
        files = [p for p in dest.iterdir() if p.is_file()]
        return subdirs[0] if (not files and len(subdirs) == 1) else dest
    return path


def load_set(gdir: Path):
    job = None
    jobs = list(gdir.glob("*.gbrjob"))
    job_funcs = {}
    if jobs:
        try:
            job = json.loads(jobs[0].read_text(encoding="utf-8", errors="replace"))
            for fa in job.get("FilesAttributes", []):
                job_funcs[fa["Path"].lower()] = fa.get("FileFunction", "")
        except Exception:
            job = None
    layers, unknown, errors = [], [], []
    for p in sorted(gdir.iterdir()):
        if not p.is_file() or p.suffix.lower() in (".gbrjob", ".zip", ".md", ".json", ".svg", ".txt", ".pdf", ".csv", ".pos"):
            continue
        func = _x2_function(p) or job_funcs.get(p.name.lower())
        is_drill = p.suffix.lower() in (".drl", ".xln", ".exc", ".txt")
        if func is None and not is_drill:
            func = EXT_FUNCTIONS.get(p.suffix.lower())
        if func is None and is_drill:
            func = "NonPlated,1,2,NPTH" if "npth" in p.name.lower() else "Plated,1,2,PTH"
        if func is None:
            unknown.append(p.name)
            continue
        kind, side, idx = _classify(func)
        try:
            f = ExcellonFile.open(p) if kind.startswith("drill") else GerberFile.open(p)
        except Exception as e:  # unparseable file is itself a finding
            errors.append(f"{p.name}: {type(e).__name__}: {e}")
            continue
        layers.append(Layer(p, func, kind, side, idx, f))
    return layers, unknown, errors, job


# ---------------------------------------------------------------- checks
def check_completeness(layers, unknown, errors, job, profile):
    c = Check("completeness", "Required files present")
    by = lambda k, s=None: [l for l in layers if l.kind == k and (s is None or l.side == s)]  # noqa: E731
    copper = by("copper")
    n_layers = (job or {}).get("GeneralSpecs", {}).get("LayerNumber") or len(copper)
    c.details.append(f"Files read: {len(layers)}; copper layers expected: {n_layers}, found: {len(copper)}")
    for e in errors:
        c.worsen("FAIL", f"Could not parse {e}")
    for kind, side, label, missing_status in (
        ("copper", "top", "Top copper", "FAIL"), ("copper", "bottom", "Bottom copper", "FAIL"),
        ("mask", "top", "Top solder mask", "FAIL"), ("mask", "bottom", "Bottom solder mask", "FAIL"),
        ("legend", "top", "Top silkscreen", "WARN"), ("legend", "bottom", "Bottom silkscreen", "INFO"),
        ("paste", "top", "Top paste (stencil only)", "INFO"), ("paste", "bottom", "Bottom paste (stencil only)", "INFO"),
        ("profile", None, "Board outline", "FAIL"), ("drill_pth", None, "Plated drill file", "FAIL"),
        ("drill_npth", None, "Non-plated drill file", "INFO"),
    ):
        found = by(kind, side)
        if not found:
            c.worsen(missing_status, f"Missing: {label}")
            continue
        for l in found:
            if not l.file.objects:
                empty_status = "FAIL" if kind in ("copper", "profile") else ("INFO" if kind in ("paste", "drill_npth", "legend") else "WARN")
                if not (kind == "drill_pth" and by("drill_npth") and any(x.file.objects for x in by("drill_npth"))):
                    c.worsen(empty_status, f"Empty file: {l.path.name} ({label})")
    if len(copper) < n_layers:
        c.worsen("FAIL", f"Job file says {n_layers} copper layers but only {len(copper)} copper files found")
    if n_layers > profile["max_layers"]:
        c.worsen("FAIL", f"{n_layers} layers exceeds {profile['manufacturer']} {profile['tier']} max of {profile['max_layers']}")
    if unknown:
        c.worsen("INFO", "Not recognised (ignored): " + ", ".join(unknown))
    c.message = "All required files present." if c.status in ("PASS", "INFO") else "Required files are missing or unreadable."
    return c


def build_outline(layers, job):
    c = Check("outline", "Board outline")
    prof = [l for l in layers if l.kind == "profile" and l.file.objects]
    if not prof:
        c.worsen("FAIL", "No board outline file, so edge and size checks were skipped")
        c.message = "No outline."
        return c, None
    lines, widths = [], []
    for obj in prof[0].file.objects:
        for p in obj.to_primitives(MM):
            if isinstance(p, gp.Line):
                lines.append(LineString([(p.x1, p.y1), (p.x2, p.y2)]))
                widths.append(p.width)
            elif isinstance(p, gp.Arc):
                lines.append(LineString(_arc_points((p.x1, p.y1), (p.x2, p.y2), (p.cx, p.cy), p.clockwise)))
                widths.append(p.width)
            elif isinstance(p, gp.ArcPoly):
                g = _prim_geom(p)
                if g is not None:
                    lines.append(g.exterior)
    # snap tiny gaps so endpoints that are 1 µm apart still close
    merged = unary_union([shp_snap(l) for l in lines])
    polys, dangles, cuts, invalids = polygonize_full(merged)
    polys = [g for g in getattr(polys, "geoms", [])]
    if not polys:
        c.worsen("FAIL", "Outline is not closed: no enclosed board shape could be formed")
        if not dangles.is_empty:
            for g in getattr(dangles, "geoms", [dangles]):
                x, y = g.coords[0]
                c.add_loc(x, y, "outline", "open end")
        c.message = "Outline is open."
        return c, None
    if not dangles.is_empty:
        c.worsen("WARN", f"{len(getattr(dangles, 'geoms', [dangles]))} outline segment(s) don't connect to the closed shape")
        for g in getattr(dangles, "geoms", [dangles]):
            x, y = g.coords[0]
            c.add_loc(x, y, "outline", "dangling segment")
    polys.sort(key=lambda g: g.area, reverse=True)
    holes = [p for i, p in enumerate(polys) if any(q.contains(p.representative_point()) for q in polys[:i])]
    outers = [p for p in polys if not any(p is h for h in holes)]
    outer = unary_union(outers)
    board = outer.difference(unary_union(holes)) if holes else outer
    if len(outers) > 1:
        c.worsen("WARN", f"{len(outers)} separate board outlines in one file: the fab treats this as a panel or multiple "
                         "designs (extra fee or rejection). Order it as a panel with their panel option, or upload one board")
        for p in outers[:5]:
            pt = p.representative_point()
            c.add_loc(pt.x, pt.y, "outline", "separate board")
    w = (max(widths) if widths else 0.0)
    minx, miny, maxx, maxy = outer.bounds
    sx, sy = maxx - minx + w, maxy - miny + w
    c.details.append(f"Board size {sx:.2f} x {sy:.2f} mm, area {board.area / 100:.2f} cm², {len(holes)} internal cutout(s)")
    c.data = {"size_mm": [round(sx, 2), round(sy, 2)], "boards": len(outers), "cutouts": len(holes)}
    js = (job or {}).get("GeneralSpecs", {}).get("Size")
    if js:
        if abs(js["X"] - sx) > 0.05 or abs(js["Y"] - sy) > 0.05:
            c.worsen("WARN", f"Outline size {sx:.2f} x {sy:.2f} mm differs from job file {js['X']} x {js['Y']} mm")
        else:
            c.details.append(f"Matches job file size {js['X']} x {js['Y']} mm")
    c.message = f"Closed outline, {sx:.2f} x {sy:.2f} mm." if c.status == "PASS" else c.message or "Outline has problems."
    return c, board


def shp_snap(line):
    return LineString([(round(x, 3), round(y, 3)) for x, y in line.coords])


def check_fills(layers, board_file: Path | None, kicad_cli: str | None, workdir: Path):
    c = Check("fills", "Copper matches a fresh zone refill")
    if board_file is None:
        c.worsen("INFO", "Skipped: pass --board <file.kicad_pcb> to compare against a fresh zone refill")
        c.message = "Not checked (no board file)."
        return c
    if not kicad_cli:
        c.worsen("WARN", "Skipped: kicad-cli not found")
        c.message = "Not checked (no kicad-cli)."
        return c
    copper = [l for l in layers if l.kind == "copper"]
    if not copper:
        c.message = "No copper to compare."
        return c
    before = hashlib.sha256(board_file.read_bytes()).hexdigest()
    ref_dir = workdir / "fresh_refill"
    ref_dir.mkdir(parents=True, exist_ok=True)
    cmd = [kicad_cli, "pcb", "export", "gerbers", "--check-zones", "-o", str(ref_dir) + os.sep, str(board_file)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if hashlib.sha256(board_file.read_bytes()).hexdigest() != before:
        c.worsen("WARN", "kicad-cli changed the board file while refilling")
    if r.returncode != 0:
        c.worsen("WARN", f"kicad-cli export failed: {r.stderr.strip()[:300]}")
        c.message = "Not checked (export failed)."
        return c
    ref_layers, _, _, _ = load_set(ref_dir)
    ref_by_func = {}
    for l in ref_layers:
        if l.kind == "copper":
            ref_by_func[(l.side, l.index if l.side == "inner" else 0)] = l
    worst = 0.0
    for l in copper:
        ref = ref_by_func.get((l.side, l.index if l.side == "inner" else 0))
        if ref is None:
            continue
        l.load_geometry()
        ref.load_geometry()
        diff = l.union.symmetric_difference(ref.union)
        area = diff.area
        worst = max(worst, area)
        if area > 1.0:
            c.worsen("FAIL", f"{l.path.name}: {area:.1f} mm² of copper differs from a fresh refill")
            parts = sorted(getattr(diff, "geoms", [diff]), key=lambda g: g.area, reverse=True)
            for g in parts[:3]:
                pt = g.representative_point()
                c.add_loc(pt.x, pt.y, l.side, f"{g.area:.1f} mm² differs")
        elif area > 0.05:
            c.worsen("WARN", f"{l.path.name}: {area:.2f} mm² differs from a fresh refill (small; check zone edges)")
        else:
            c.details.append(f"{l.path.name}: matches fresh refill ({area:.3f} mm² difference)")
    c.message = ("Copper matches a fresh zone refill." if c.status == "PASS" else
                 "Gerbers were plotted from stale zone fills: refill zones (B), save, and export again.")
    return c


def check_drills(layers, profile):
    c = Check("drills", "Drill sizes and alignment")
    holes = []  # (x, y, r, plated)
    slots = []  # (x, y, width, length, plated)
    for l in layers:
        if not l.kind.startswith("drill"):
            continue
        plated = l.kind == "drill_pth"
        for obj in l.file.objects:
            for p in obj.to_primitives(MM):
                if isinstance(p, gp.Circle):
                    holes.append((p.x, p.y, p.r, plated))
                elif isinstance(p, gp.Line):  # slot
                    holes.append(((p.x1 + p.x2) / 2, (p.y1 + p.y2) / 2, p.width / 2, plated))
                    slots.append(((p.x1 + p.x2) / 2, (p.y1 + p.y2) / 2, p.width,
                                  math.hypot(p.x2 - p.x1, p.y2 - p.y1) + p.width, plated))
    for (x, y, w, ln, plated) in slots:
        lim = profile["min_plated_slot_mm"] if plated else profile["min_npth_slot_mm"]
        if 0 < w < lim - 1e-6:
            c.worsen("WARN" if _assumed(profile, "min_plated_slot_mm") else "FAIL",
                     f"{'Plated' if plated else 'Non-plated'} slot {w:.2f} mm wide at ({x:.2f}, {-y:.2f}) is under the fab's "
                     f"{lim} mm minimum")
            c.add_loc(x, y, "drill", f"{w:.2f} mm slot")
    if slots:
        c.details.append(f"{len(slots)} slot(s): {sum(s[4] for s in slots)} plated, {sum(not s[4] for s in slots)} non-plated")
    zero = [h for h in holes if h[2] <= 1e-6]
    if zero:
        c.worsen("FAIL", f"{len(zero)} drill hit(s) use a 0 mm tool: pads or vias with no drill size set")
        for h in zero:
            c.add_loc(h[0], h[1], "drill", "0 mm drill")
        holes = [h for h in holes if h[2] > 1e-6]
    if not holes:
        c.worsen("WARN", "No drill holes found")
        c.message = c.message or "No holes."
        return c, holes
    dmin = min(h[2] * 2 for h in holes)
    sizes = sorted({round(h[2] * 2, 3) for h in holes})
    c.details.append(f"{sum(h[3] for h in holes)} plated, {sum(not h[3] for h in holes)} non-plated holes; sizes {sizes} mm")
    npth_min = profile.get("min_npth_mm", 0.0) or 0.0
    small_np = [h for h in holes if not h[3] and h[2] * 2 < npth_min - 1e-6]
    if small_np:
        c.worsen("FAIL", f"{len(small_np)} non-plated hole(s) under the {npth_min} mm minimum for non-plated holes "
                         f"(smallest {min(h[2] for h in small_np) * 2:.3f} mm)")
        for h in small_np:
            c.add_loc(h[0], h[1], "drill", f"{h[2] * 2:.3f} mm NPTH")
    small = [h for h in holes if h[2] * 2 < profile["min_drill_mm"] - 1e-6]
    if small:
        c.worsen("FAIL", f"{len(small)} hole(s) below min drill {profile['min_drill_mm']} mm (smallest {dmin:.3f} mm)")
        for h in small:
            c.add_loc(h[0], h[1], "drill", f"{h[2] * 2:.3f} mm hole")
    c.data = {"min_hole_mm": round(dmin, 3), "plated": sum(h[3] for h in holes), "non_plated": sum(not h[3] for h in holes),
              "slots": len(slots), "npth_file": any(l.kind == "drill_npth" for l in layers)}
    c.message = f"Smallest hole {dmin:.3f} mm." if c.status == "PASS" else c.message
    return c, holes


def _groups(layer: Layer):
    """Split a copper layer into conductor groups.

    One group per net. Netless copper (footprint copper shapes, logos) that touches one net becomes part of that net;
    netless copper that touches two or more nets joins them into one group and is reported as a bridge (bridged
    solder jumpers and net ties do this on purpose). Netless copper touching nothing stays its own island.
    Returns (groups [(name, geometry)], bridges [(names, point)]).
    """
    by_net, nonet = {}, []
    for f in layer.features:
        if f.net:
            by_net.setdefault(f.net, []).append(f.geom)
        else:
            nonet.append(f.geom)
    names = list(by_net)
    geoms = [unary_union(by_net[n]) for n in names]
    parent = list(range(len(names)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    extras = {i: [] for i in range(len(names))}
    islands, bridges = [], []
    if nonet:
        u = unary_union(nonet)
        tree = STRtree(geoms) if geoms else None
        for isl in getattr(u, "geoms", [u]):
            touch = sorted({int(k) for k in tree.query(isl, predicate="intersects")}) if tree is not None else []
            if not touch:
                islands.append(isl)
                continue
            extras[touch[0]].append(isl)
            for k in touch[1:]:
                parent[find(k)] = find(touch[0])
            if len(touch) > 1:
                bridges.append(([names[k] for k in touch], isl.representative_point()))
    merged = {}
    for i in range(len(names)):
        merged.setdefault(find(i), []).append(i)
    groups = []
    for root, idx in merged.items():
        label = " + ".join(sorted(names[i] for i in idx))
        groups.append((label, unary_union([geoms[i] for i in idx] + [g for i in idx for g in extras[i]])))
    for i, g in enumerate(islands):
        groups.append((f"<no net #{i}>", g))
    return groups, bridges


TOL = 0.005  # mm; circles are approximated by polygons, so exact-limit features measure a hair small


def _assumed(profile, key):
    """True when a rule may only raise a WARN: no published value (ASSUMED) or PCBWay just asks (ENGINEERING QUERY)."""
    return str(profile.get("_sources", {}).get(key, "")).startswith(("ASSUMED", "ENGINEERING QUERY"))


def _why(profile, key):
    s = str(profile.get("_sources", {}).get(key, ""))
    return "the fab raises an engineering query" if s.startswith("ENGINEERING QUERY") else "assumed rule" if s.startswith("ASSUMED") else ""


def check_dfm(layers, holes, board, profile):
    tw = Check("trace_width", "Minimum trace width")
    cl = Check("clearance", "Copper clearance between nets")
    ar = Check("annular_ring", "Annular ring and drill-on-copper")
    ed = Check("copper_edge", "Copper to board edge")
    rule = profile
    outer = [l for l in layers if l.kind == "copper" and l.side in ("top", "bottom")]
    any_nets = False
    for l in [x for x in layers if x.kind == "copper"]:
        l.load_geometry()
        any_nets |= l.has_nets
        # --- trace width
        widths = [f.width for f in l.features if f.kind in ("Line", "Arc") and f.width and (f.func in (None, "Conductor"))]
        if widths:
            wmin = min(widths)
            tw.data["min_trace_mm"] = round(min(wmin, tw.data.get("min_trace_mm", math.inf)), 3)
            tw.details.append(f"{l.path.name}: narrowest track {wmin:.3f} mm")
            if wmin < rule["min_trace_width_mm"] - 1e-6:
                bad = [f for f in l.features if f.width and f.width < rule["min_trace_width_mm"] - 1e-6 and f.kind in ("Line", "Arc")]
                tw.worsen("FAIL", f"{l.path.name}: {len(bad)} track(s) narrower than {rule['min_trace_width_mm']} mm (narrowest {wmin:.3f} mm)")
                for f in bad:
                    pt = f.geom.representative_point()
                    tw.add_loc(pt.x, pt.y, l.side, f"{f.width:.3f} mm track")
        # --- clearance between different nets
        groups, bridges = _groups(l)
        for nets, pt in bridges:
            cl.worsen("WARN", f"{l.path.name}: netless copper joins {' and '.join(nets)} "
                              "(fine for a bridged solder jumper or net tie; otherwise a short)")
            cl.add_loc(pt.x, pt.y, l.side, "netless copper joins " + " / ".join(nets))
        parts, owner = [], []
        for gi, (_, g) in enumerate(groups):
            for part in getattr(g, "geoms", [g]):
                if not part.is_empty:
                    parts.append(part)
                    owner.append(gi)
        tree = STRtree(parts)
        search = max(rule["min_trace_clearance_mm"] * 3, 0.5)
        best = math.inf
        seen = set()
        for i, part in enumerate(parts):
            for j in tree.query(part, predicate="dwithin", distance=search):
                j = int(j)
                if j <= i or owner[i] == owner[j]:
                    continue
                key = (min(owner[i], owner[j]), max(owner[i], owner[j]))
                d = part.distance(parts[j])
                best = min(best, d)
                if d > 1e-6:  # touching copper is a short, reported separately; the order form wants the real gap
                    cl.data["min_gap_mm"] = round(min(d, cl.data.get("min_gap_mm", math.inf)), 3)
                if d < rule["min_trace_clearance_mm"] - TOL and key not in seen:
                    seen.add(key)
                    a, b = nearest_points(part, parts[j])
                    na, nb = groups[owner[i]][0], groups[owner[j]][0]
                    what = "touch (short)" if d < 1e-6 else f"{d:.3f} mm apart"
                    cl.worsen("FAIL", f"{l.path.name}: {na} and {nb} {what}")
                    cl.add_loc((a.x + b.x) / 2, (a.y + b.y) / 2, l.side, f"{na} / {nb}: {d:.3f} mm")
        if best < math.inf:
            cl.details.append(f"{l.path.name}: closest gap between different conductors {best:.3f} mm")
        # --- copper to edge
        if board is not None:
            edge = board.boundary
            # plated holes cut by the edge are castellated (half) holes: their pads cross the edge on purpose
            castell = [(x, y, r) for (x, y, r, p) in holes if p and Point(x, y).distance(edge) < r]
            cz = unary_union([Point(x, y).buffer(r + 1.0) for x, y, r in castell]) if castell else None
            ed.data["castellated"] = len(castell)
            if castell and l is next(x for x in layers if x.kind == "copper"):
                ed.worsen("WARN", f"{len(castell)} plated hole(s) cross the board edge (castellated holes): order the fab's "
                                  "castellated-holes option; copper next to them was not edge-checked")
                for x, y, r in castell[:3]:
                    ed.add_loc(x, y, "drill", "castellated hole")
            outside = l.union.difference(board.buffer(1e-4))
            if cz is not None:
                outside = outside.difference(cz)
            if outside.area > 0.01:
                ed.worsen("FAIL", f"{l.path.name}: {outside.area:.2f} mm² of copper lies outside the board outline")
                pt = outside.representative_point()
                ed.add_loc(pt.x, pt.y, l.side, "copper outside board")
            shapes = [p.difference(cz) if cz is not None else p for p in getattr(l.union, "geoms", [l.union])]
            shapes = [p for p in shapes if not p.is_empty]
            near = [p for p in shapes if p.distance(edge) < rule["copper_to_edge_mm"] - TOL]
            emin = min((p.distance(edge) for p in shapes), default=math.inf)
            if emin < math.inf:
                ed.details.append(f"{l.path.name}: closest copper to edge {emin:.3f} mm")
            if near:
                ed.worsen("FAIL", f"{l.path.name}: {len(near)} copper shape(s) closer than {rule['copper_to_edge_mm']} mm to the edge")
                for p in near:
                    a, _ = nearest_points(p, edge)
                    ed.add_loc(a.x, a.y, l.side, f"{p.distance(edge):.3f} mm from edge")
    if not any_nets:
        cl.worsen("INFO", "No X2 net attributes in the Gerbers: clearance measured between separate copper shapes only")
    # --- annular ring / drills on copper (outer layers, plated holes)
    misses, offsets = [], []
    plated = [(x, y) for (x, y, r, p) in holes if p]
    pad_centers = [f.geom.centroid for l in outer for f in l.features if f.kind == "Flash"]
    if plated and pad_centers:  # a consistent hole-to-pad offset means a drill origin mismatch
        ptree = STRtree(pad_centers)
        vecs = []
        for x, y in plated:
            c0 = pad_centers[int(ptree.nearest(Point(x, y)))]
            vecs.append((c0.x - x, c0.y - y))
        mdx = sorted(v[0] for v in vecs)[len(vecs) // 2]
        mdy = sorted(v[1] for v in vecs)[len(vecs) // 2]
        agree = sum(math.hypot(v[0] - mdx, v[1] - mdy) < 0.05 for v in vecs) / len(vecs)
        if math.hypot(mdx, mdy) > 0.05 and agree > 0.6:
            offsets = [(mdx, mdy, agree)]
    near_limit = set()
    margin = rule.get("ring_margin_mm", 0.0)
    for l in outer:
        parts = [p for p in getattr(l.union, "geoms", [l.union]) if not p.is_empty]
        tree = STRtree(parts) if parts else None
        rmin = math.inf
        for (x, y, r, plated) in holes:
            if not plated:
                continue
            pt = Point(x, y)
            hits = [parts[int(k)] for k in tree.query(pt, predicate="within")] if tree is not None else []
            if not hits:
                misses.append((x, y, l.side))
                continue
            ring = min(h.boundary.distance(pt) for h in hits) - r
            rmin = min(rmin, ring)
            if ring < rule["min_annular_ring_mm"] - TOL:
                ar.worsen("FAIL", None)
                ar.add_loc(x, y, l.side, "hole breaks out of its pad" if ring < 0 else f"ring {ring:.3f} mm")
            elif ring < rule["min_annular_ring_mm"] + margin - TOL:
                near_limit.add((round(x, 3), round(y, 3)))
        if rmin < math.inf:
            ar.details.append(f"{l.path.name}: smallest annular ring {rmin:.3f} mm")
            if rmin < rule["min_annular_ring_mm"] - TOL:
                ar.details.append(f"[FAIL] {l.path.name}: ring below {rule['min_annular_ring_mm']} mm (smallest {rmin:.3f} mm)")
    if near_limit:
        ar.worsen("WARN", f"{len(near_limit)} plated hole(s) have rings within {margin} mm of the {rule['min_annular_ring_mm']} mm "
                          "minimum: at the fab's limit, drill wander can break out; add margin if you can")
        for x, y in list(near_limit)[:3]:
            ar.add_loc(x, y, "drill", "ring at the limit")
    ar.data = {"near_limit": len(near_limit)}
    if misses:
        ar.worsen("FAIL", f"{len(misses)} plated hole(s) don't land on copper (if they're mounting holes meant to be "
                          "unplated, make them NPTH so they go in the NPTH drill file)")
        for x, y, side in misses:
            ar.add_loc(x, y, side, "plated hole with no copper")
    if offsets:
        dx, dy, agree = offsets[0]
        ar.worsen("FAIL", f"Drill file is offset from the copper by ({-dx:.3f}, {dy:.3f}) mm ({agree:.0%} of holes agree): "
                          "export Gerbers and drills with the same origin setting")
        ar.details.insert(0, ar.details.pop())
    for chk, ok in ((tw, "Track widths meet the rule."), (cl, "Clearance meets the rule."),
                    (ar, "Every plated hole lands on copper with enough ring."), (ed, "Copper keeps clear of the edge.")):
        if not chk.message:
            chk.message = ok if chk.status in ("PASS", "INFO") else f"{chk.title}: rule violated."
    if board is None:
        ed.worsen("WARN", "Skipped: no closed outline")
        ed.message = "Not checked (no outline)."
    return [tw, cl, ar, ed, _check_hole_edge(holes, board, rule)]


def _check_hole_edge(holes, board, profile):
    """Hole wall to board edge (outline and internal cutouts). Castellated holes are skipped."""
    c = Check("hole_edge", "Holes clear of the board edge")
    if board is None:
        c.worsen("INFO", "Skipped: no closed outline")
        c.message = "Not checked (no outline)."
        return c
    lim, soft = profile["hole_to_edge_mm"], _assumed(profile, "hole_to_edge_mm")
    edge = board.boundary
    dmin, n = math.inf, 0
    for (x, y, r, plated) in holes:
        d = Point(x, y).distance(edge) - r
        if plated and d < 0:
            continue  # castellated: reported by the copper-to-edge check
        dmin = min(dmin, d)
        if d < lim - TOL:
            n += 1
            c.add_loc(x, y, "drill", f"hole wall {max(d, 0):.3f} mm from edge")
    if n:
        c.worsen("WARN" if soft else "FAIL", f"{n} hole(s) closer than {lim} mm to the board edge"
                 + (f" ({_why(profile, 'hole_to_edge_mm')}; V-scored panels need 1.0 mm)" if soft else ""))
    if dmin < math.inf:
        c.details.append(f"Closest hole wall to an edge: {dmin:.3f} mm")
    if c.status == "PASS":
        c.message = f"Holes keep {lim} mm from the edge."
    return c


def _hole_nets(copper, holes):
    """Net of each plated hole, from the outer-layer pad it sits in (drill files carry no nets)."""
    pads = [f for l in copper if l.side in ("top", "bottom") for f in l.features if f.net and f.kind == "Flash"]
    ptree = STRtree([f.geom for f in pads]) if pads else None
    nets = {}
    for (x, y, r, plated) in holes:
        if plated and ptree is not None:
            hit = ptree.query(Point(x, y), predicate="within")
            if len(hit):
                nets[(x, y)] = pads[int(hit[0])].net
    return nets


def _inner_iso(profile, n_copper):
    """Inner hole-to-copper rule: PCBWay scales it with layer count; an explicit override wins."""
    table = profile.get("inner_hole_to_copper_by_layers")
    if isinstance(table, dict) and table and "inner_hole_to_copper_mm" not in profile.get("_overrides", {}):
        keys = sorted(int(k) for k in table)
        pick = max([k for k in keys if k <= n_copper], default=keys[0])
        return float(table[str(pick)]), False
    return profile["inner_hole_to_copper_mm"], _assumed(profile, "inner_hole_to_copper_mm")


def check_pth_clearance(layers, holes, profile):
    """Plated hole wall to copper of other nets, on every layer where the hole connects (drill wander can nick it)."""
    c = Check("pth_clearance", "Plated holes clear of other nets' copper")
    copper = [l for l in layers if l.kind == "copper"]
    plated = [h for h in holes if h[3]]
    if not copper or not plated:
        c.worsen("INFO", "No plated holes or copper")
        c.message = "Not applicable."
        return c
    outer_rule, outer_assumed = profile["pth_to_copper_mm"], _assumed(profile, "pth_to_copper_mm")
    inner_rule, inner_assumed = _inner_iso(profile, len(copper))
    hole_net = _hole_nets(copper, holes)
    worst = {}
    for l in copper:
        l.load_geometry()
        rule, assumed = (inner_rule, inner_assumed) if l.side == "inner" else (outer_rule, outer_assumed)
        groups = _groups(l)[0]
        parts, owner = [], []
        for name, g in groups:
            for p in getattr(g, "geoms", [g]):
                if not p.is_empty:
                    parts.append(p)
                    owner.append(name)
        if not parts:
            continue
        tree = STRtree(parts)
        dmin = math.inf
        for (x, y, r, _) in plated:
            pt = Point(x, y)
            inside = {int(k) for k in tree.query(pt, predicate="within")}
            if not inside and l.side == "inner":
                continue  # passes through without connecting: the inner-layer isolation check covers it
            net = hole_net.get((x, y))
            own = {owner[k] for k in inside}
            hole = pt.buffer(r)
            for k in tree.query(hole, predicate="dwithin", distance=max(rule + 0.05, 0.5)):  # wider, to report the closest
                k = int(k)
                name = owner[k]
                if k in inside or name in own or (net and net in name.split(" + ")):
                    continue
                d = parts[k].distance(hole)
                dmin = min(dmin, d)
                if d < rule - TOL:
                    status = "WARN" if assumed else "FAIL"
                    key = (l.path.name, status)
                    worst[key] = worst.get(key, 0) + 1
                    c.add_loc(x, y, l.side, f"{l.path.name}: {name} copper {d:.3f} mm from the hole wall"
                                            + (f" of a {net} hole" if net else ""))
        if dmin < math.inf:
            c.details.append(f"{l.path.name}: closest other-net copper {dmin:.3f} mm from a plated hole wall (rule {rule} mm"
                             + (f"; {_why(profile, 'pth_to_copper_mm')})" if assumed and l.side != "inner" else ")"))
    for (name, status), n in sorted(worst.items(), key=lambda kv: kv[0][1] != "FAIL"):
        side_rule = inner_rule if "In" in name and "_Cu" in name else outer_rule
        c.worsen(status, f"{name}: {n} plated hole(s) with other-net copper closer than {side_rule} mm to the hole wall"
                         + (f" ({_why(profile, 'pth_to_copper_mm')})" if status == "WARN" else ""))
    if c.status == "PASS":
        c.message = f"Other nets keep {outer_rule} mm (outer) / {inner_rule} mm (inner) from plated hole walls."
    return c


def check_hole_clearance(layers, holes, profile):
    """Inner-layer rings and hole isolation, plus non-plated holes kept clear of copper on every layer."""
    inner_c = Check("inner_layers", "Inner-layer rings and hole clearance")
    npth_c = Check("npth", "Non-plated holes clear of copper")
    ring_min, npth_min = profile["min_annular_ring_mm"], profile["npth_to_copper_mm"]
    copper = [l for l in layers if l.kind == "copper"]
    iso_min, iso_assumed = _inner_iso(profile, len(copper))
    inner = [l for l in copper if l.side == "inner"]
    if not inner:
        inner_c.worsen("INFO", "No inner copper layers (2-layer board)")
        inner_c.message = "Not applicable."
    hole_net = _hole_nets(copper, holes)
    for l in inner:
        parts, tree = l.tree()
        if tree is None:
            continue
        groups = _groups(l)[0] if l.has_nets else []
        gtree = STRtree([g for _, g in groups]) if groups else None
        rmin = cmin = math.inf
        for (x, y, r, plated) in holes:
            if not plated:
                continue
            pt = Point(x, y)
            hits = [parts[int(k)] for k in tree.query(pt, predicate="within")]
            if hits:  # hole lands in copper on this layer: same net = connection (check ring), other net = short
                net = hole_net.get((x, y))
                if net and gtree is not None:
                    gh = gtree.query(pt, predicate="within")
                    owner = groups[int(gh[0])][0] if len(gh) else None
                    if owner and not owner.startswith("<no net") and net not in owner.split(" + "):
                        inner_c.worsen("FAIL", f"{l.path.name}: hole of net {net} at ({x:.2f}, {-y:.2f}) passes through "
                                               f"{owner} copper with no clearance (short)")
                        inner_c.add_loc(x, y, l.side, f"{l.path.name}: {net} hole in {owner} copper")
                        continue
                ring = min(h.boundary.distance(pt) for h in hits) - r
                rmin = min(rmin, ring)
                if ring < ring_min - TOL:
                    inner_c.worsen("FAIL", None)
                    inner_c.add_loc(x, y, l.side, f"{l.path.name}: ring {ring:.3f} mm")
            else:  # hole passes through: copper must keep its isolation distance
                d = parts[int(tree.nearest(pt))].distance(pt) - r
                cmin = min(cmin, d)
                if d < iso_min - TOL:
                    inner_c.worsen("WARN" if iso_assumed else "FAIL", None)
                    inner_c.add_loc(x, y, l.side, f"{l.path.name}: copper {d:.3f} mm from hole wall" if d > 0 else
                                    f"{l.path.name}: hole cuts through copper")
        inner_c.details.append(f"{l.path.name}: smallest ring {rmin:.3f} mm, closest pass-through copper {cmin:.3f} mm"
                               .replace("inf mm", "n/a"))
    if inner_c.status in ("FAIL", "WARN") and not any(d.startswith("[FAIL]") for d in inner_c.details):
        n = len(inner_c.locations)
        inner_c.details.insert(0, f"[{inner_c.status}] {n}{'+' if n == MAX_LOCATIONS else ''} inner-layer problem(s): "
                                  f"ring below {ring_min} mm or copper closer than {iso_min} mm to a hole it doesn't connect to")
    elif inner:
        inner_c.message = "Inner-layer rings and isolation meet the rule."

    np_holes = [h for h in holes if not h[3]]
    if not np_holes:
        npth_c.worsen("INFO", "No non-plated holes")
        npth_c.message = "Not applicable."
        return [inner_c, npth_c]
    dmin = math.inf
    for l in copper:
        parts, tree = l.tree()
        if tree is None:
            continue
        for (x, y, r, _) in np_holes:
            pt = Point(x, y)
            if len(tree.query(pt, predicate="within")):
                npth_c.worsen("FAIL", f"{l.path.name}: non-plated hole at ({x:.2f}, {-y:.2f}) sits in copper: fabs "
                                      "plate holes that sit in copper (short risk; PCBWay says so explicitly); keep NPTH in "
                                      "a copper-free area")
                npth_c.add_loc(x, y, l.side, "NPTH through copper")
                continue
            d = parts[int(tree.nearest(pt))].distance(pt) - r
            dmin = min(dmin, d)
            if d < npth_min - TOL:
                npth_c.worsen("WARN" if _assumed(profile, "npth_to_copper_mm") else "FAIL", f"{l.path.name}: copper {max(d, 0):.3f} mm from a non-plated hole wall "
                                      f"at ({x:.2f}, {-y:.2f}) (rule {npth_min} mm" + (f"; {_why(profile, 'npth_to_copper_mm')}" if _assumed(profile, "npth_to_copper_mm") else "") + ")")
                npth_c.add_loc(x, y, l.side, f"{max(d, 0):.3f} mm to copper")
    if dmin < math.inf:
        npth_c.details.append(f"{len(np_holes)} non-plated holes; closest copper {dmin:.3f} mm from a hole wall")
    if npth_c.status in ("PASS", "INFO"):
        npth_c.message = f"Non-plated holes keep {npth_min} mm from copper."
    return [inner_c, npth_c]


def check_hole_spacing(layers, profile):
    """Edge-to-edge spacing between drill holes, plus overlapping holes and duplicate hits."""
    c = Check("hole_spacing", "Hole-to-hole spacing")
    comp_rule, via_rule = profile["min_hole_to_hole_mm"], profile["min_via_hole_to_hole_mm"]
    shapes = []  # (geometry, x, y, diameter, plated)
    for l in layers:
        if not l.kind.startswith("drill"):
            continue
        for obj in l.file.objects:
            for p in obj.to_primitives(MM):
                if isinstance(p, gp.Circle) and p.r > 1e-6:
                    shapes.append((Point(p.x, p.y).buffer(p.r), p.x, p.y, 2 * p.r, l.kind == "drill_pth"))
                elif isinstance(p, gp.Line) and p.width > 1e-6:  # routed slot
                    g = LineString([(p.x1, p.y1), (p.x2, p.y2)]).buffer(p.width / 2)
                    shapes.append((g, (p.x1 + p.x2) / 2, (p.y1 + p.y2) / 2, p.width, l.kind == "drill_pth"))
    if len(shapes) < 2:
        c.worsen("INFO", "Fewer than two holes")
        c.message = "Not applicable."
        return c
    tree = STRtree([s[0] for s in shapes])
    dmin, pair_min = math.inf, None
    dup = overlap = close = 0
    for i, (gi, xi, yi, di, _) in enumerate(shapes):
        for j in tree.query(gi, predicate="dwithin", distance=max(comp_rule, via_rule) + 0.05):
            j = int(j)
            if j <= i:
                continue
            gj, xj, yj, dj, _ = shapes[j]
            if math.hypot(xi - xj, yi - yj) < 1e-3:
                dup += 1
                c.add_loc(xi, yi, "drill", f"duplicate hit ({di:.2f} / {dj:.2f} mm)")
                continue
            d = gi.distance(gj)
            if d < dmin:
                dmin, pair_min = d, (di, dj)
            if d <= 1e-6:
                overlap += 1
                c.add_loc((xi + xj) / 2, (yi + yj) / 2, "drill", f"holes overlap ({di:.2f} / {dj:.2f} mm)")
                continue
            rule = via_rule if max(di, dj) <= 0.45 + 1e-6 else comp_rule
            if d < rule - TOL:
                close += 1
                c.add_loc((xi + xj) / 2, (yi + yj) / 2, "drill", f"{d:.3f} mm between {di:.2f} / {dj:.2f} mm holes")
    if dup:
        c.worsen("WARN", f"{dup} duplicate drill hit(s): the same hole drilled twice (fabs usually merge them, but it can break bits)")
    if overlap:
        c.worsen("FAIL", f"{overlap} pair(s) of overlapping holes: they drill into each other (use a slot if that's intended)")
    if close:
        assumed = _assumed(profile, "min_hole_to_hole_mm") or _assumed(profile, "min_via_hole_to_hole_mm")
        c.worsen("WARN" if assumed else "FAIL",
                 f"{close} hole pair(s) closer than {comp_rule} mm (component holes) or {via_rule} mm (vias up to 0.45 mm)"
                 + (f" ({_why(profile, 'min_hole_to_hole_mm') or _why(profile, 'min_via_hole_to_hole_mm')})" if assumed else ""))
    if pair_min:
        c.details.append(f"{len(shapes)} holes; closest pair {dmin:.3f} mm apart ({pair_min[0]:.2f} and {pair_min[1]:.2f} mm holes)")
    if c.status == "PASS":
        c.message = f"Holes are at least {via_rule} mm (vias) / {comp_rule} mm apart."
    return c


def check_vias(layers):
    """How the mask treats vias. PCBWay follows the Gerber mask, so the order form's via option must match it."""
    c = Check("vias", "Via tenting (match the order form)")
    summary = {}
    for side in ("top", "bottom"):
        cu = next((l for l in layers if l.kind == "copper" and l.side == side), None)
        mk = next((l for l in layers if l.kind == "mask" and l.side == side), None)
        if cu is None:
            continue
        cu.load_geometry()
        vias = [f for f in cu.features if f.func == "ViaPad"]
        if not vias:
            continue
        if mk is None:
            summary[side] = {"tented": 0, "open": len(vias), "partial": 0, "in_pad": 0}
            continue
        mk.load_geometry()
        pads = [f for f in cu.features if f.func and f.func != "ViaPad" and "Pad" in f.func]
        ptree = STRtree([f.geom for f in pads]) if pads else None
        tented = open_ = partial = in_pad = 0
        for f in vias:
            if ptree is not None and len(ptree.query(f.geom.centroid, predicate="within")):
                in_pad += 1  # via-in-pad: a filling/capping question, not a tenting one
                continue
            frac = f.geom.intersection(mk.union).area / max(f.geom.area, 1e-9)
            if frac < 0.05:
                tented += 1
            elif frac > 0.9:
                open_ += 1
            else:
                partial += 1
        summary[side] = {"tented": tented, "open": open_, "partial": partial, "in_pad": in_pad,
                         "min_pad": min(2 * math.sqrt(f.geom.area / math.pi) for f in vias)}
    c.data = summary
    pads = [s.pop("min_pad") for s in summary.values() if isinstance(s, dict) and "min_pad" in s]
    if pads:
        c.data["min_via_pad_mm"] = round(min(pads), 3)
    if not summary:
        c.worsen("INFO", "No vias marked in the Gerbers (X2 ViaPad)")
        c.message = "No vias found."
        return c
    for side, s in [(k, v) for k, v in summary.items() if isinstance(v, dict)]:
        c.details.append(f"{side}: {s['tented']} tented, {s['open']} open, {s['partial']} partly exposed, {s['in_pad']} in pads")
    sv = [s for s in summary.values() if isinstance(s, dict)]
    kinds = {k for s in sv for k in ("tented", "open") if s[k]}
    partial = sum(s["partial"] for s in sv)
    in_pad = sum(s["in_pad"] for s in sv)
    if partial:
        c.worsen("WARN", f"{partial} via(s) partly exposed by a neighbouring pad's mask opening: solder can wick into them")
        c.data["partial"] = partial
    if in_pad:
        c.worsen("INFO", f"{in_pad} via(s) inside SMD pads: order via filling and capping (resin-filled, plated over) "
                         "or solder will drain into them")
        c.data["in_pad"] = in_pad
    if len(kinds) > 1:
        c.worsen("WARN", "Vias are a mix of tented and open: the fab follows the Gerber mask, so add a note on the order "
                         "saying which vias stay open, or make them consistent")
    choice = ("Tented vias" if kinds == {"tented"} else "Untented (open) vias" if kinds == {"open"}
              else "per the Gerber (add a note)" if kinds else "n/a")
    c.data["order_option"] = choice
    if c.status in ("PASS", "INFO") and len(kinds) == 1:
        c.message = f"Free vias are all {'tented' if kinds == {'tented'} else 'open'}: choose '{choice}' on the order form."
    return c


def check_silk_cutouts(layers, board):
    """Closed silkscreen shapes with nothing inside look like cutouts drawn on the wrong layer (PCBWay misses them)."""
    c = Check("cutouts", "Cutouts drawn on silkscreen")
    if board is None:
        c.worsen("INFO", "Skipped: no closed outline")
        c.message = "Not checked."
        return c
    copper = unary_union([l.union for l in layers if l.kind == "copper" and l.union is not None] or [Polygon()])
    found = 0
    for l in [x for x in layers if x.kind == "legend"]:
        l.load_geometry()
        lines = []
        for obj in l.file.objects:
            for p in obj.to_primitives(MM):
                if isinstance(p, gp.Line):
                    lines.append(LineString([(p.x1, p.y1), (p.x2, p.y2)]))
                elif isinstance(p, gp.Arc):
                    lines.append(LineString(_arc_points((p.x1, p.y1), (p.x2, p.y2), (p.cx, p.cy), p.clockwise)))
        if not lines:
            continue
        polys = polygonize_full(unary_union([shp_snap(x) for x in lines]))[0]
        for poly in getattr(polys, "geoms", []):
            if poly.area < 1.0 or not board.contains(poly):
                continue
            inner = poly.buffer(-0.3)
            if inner.is_empty or inner.intersects(copper) or inner.intersects(l.union.difference(poly.exterior.buffer(0.3))):
                continue  # encloses pads or text: a component outline or a text frame
            found += 1
            pt = poly.representative_point()
            c.add_loc(pt.x, pt.y, l.side, f"empty closed shape {poly.area:.1f} mm²")
    if found:
        c.worsen("WARN", f"{found} empty closed shape(s) on silkscreen: if any is a cutout or slot, draw it on Edge.Cuts "
                         "(fabs read silkscreen as artwork and won't cut it; PCBWay calls these text frames)")
    else:
        c.message = "No empty closed shapes on silkscreen."
    return c


def check_acid_traps(layers):
    """Tracks of one net meeting at an acute angle trap etchant and can over-etch (item: acid traps)."""
    c = Check("acid_traps", "Acid traps (acute track junctions)")
    n = 0
    for l in [x for x in layers if x.kind == "copper"]:
        l.load_geometry()
        ends = {}
        for f in l.features:
            if f.ends and f.net and f.func in (None, "Conductor"):
                x1, y1, x2, y2 = f.ends
                if math.hypot(x2 - x1, y2 - y1) < 0.05:
                    continue
                for (ax, ay, bx, by) in ((x1, y1, x2, y2), (x2, y2, x1, y1)):
                    ends.setdefault((round(ax, 3), round(ay, 3), f.net), []).append((bx - ax, by - ay))
        for (x, y, net), vecs in ends.items():
            if len(vecs) < 2:
                continue
            for (u, v) in itertools.combinations(vecs, 2):
                cosang = (u[0] * v[0] + u[1] * v[1]) / (math.hypot(*u) * math.hypot(*v))
                if cosang > math.cos(math.radians(89)):  # angle under ~90 degrees
                    n += 1
                    c.add_loc(x, y, l.side, f"{net}: {math.degrees(math.acos(max(-1, min(1, cosang)))):.0f} deg junction")
                    break
    if n:
        c.worsen("WARN", f"{n} track junction(s) meet at an acute angle (acid traps): reroute at 45/90 degrees")
    else:
        c.message = "No acute track junctions."
    return c


def check_aspect_ratio(holes, job, profile):
    c = Check("aspect_ratio", "Plated hole aspect ratio")
    plated = [2 * r for (_, _, r, p) in holes if p and r > 0]
    if not plated:
        c.worsen("INFO", "No plated holes")
        c.message = "Not applicable."
        return c
    thick = float((job or {}).get("GeneralSpecs", {}).get("BoardThickness", 1.6) or 1.6)
    lim = profile.get("max_aspect_ratio", 10.0)
    worst = thick / min(plated)
    c.details.append(f"Board {thick} mm thick, smallest plated hole {min(plated):.3f} mm: aspect ratio {worst:.1f}:1")
    c.data = {"aspect_ratio": round(worst, 1)}
    hard = profile.get("max_aspect_ratio_hard") or 0
    if hard and worst > hard + 1e-6:
        c.worsen("FAIL", f"Aspect ratio {worst:.1f}:1 exceeds {hard:g}:1, which {profile.get('manufacturer', 'the fab')} "
                         "lists as unable to make: use a larger drill or a thinner board")
    elif worst > lim + 1e-6:
        c.worsen("WARN", f"Aspect ratio {worst:.1f}:1 exceeds {lim:g}:1: plating may not reach the middle of the barrel and "
                         f"the fab may charge more; use a larger drill or a thinner board ({_why(profile, 'max_aspect_ratio') or 'guideline'})")
    else:
        c.message = f"Aspect ratio {worst:.1f}:1 is within {lim:g}:1."
    return c


def check_slivers_islands(layers, profile):
    """Thin copper slivers in pours (can lift and short) and copper islands with no pad or via (dead copper)."""
    c = Check("slivers", "Copper slivers and floating islands")
    w = profile["min_trace_width_mm"]
    slivers = islands = 0
    for l in [x for x in layers if x.kind == "copper"]:
        l.load_geometry()
        pours = [f.geom for f in l.features if f.kind == "Region" and f.func in (None, "Conductor")]
        if pours:
            pu = unary_union(pours)
            thin = pu.difference(pu.buffer(-w / 2).buffer(w / 2 + 1e-3))
            for g in getattr(thin, "geoms", [thin]):
                if g.area > 0.02 and g.minimum_rotated_rectangle.length > 4 * w:  # skip corner nicks
                    slivers += 1
                    pt = g.representative_point()
                    c.add_loc(pt.x, pt.y, l.side, f"sliver {g.area:.2f} mm²")
        by_net = {}
        for f in l.features:
            if f.net and f.func in (None, "Conductor") and f.kind in ("Region", "Line", "Arc"):
                by_net.setdefault(f.net, []).append(f.geom)
        anchors = [f.geom for f in l.features if f.kind == "Flash" and f.net]
        atree = STRtree(anchors) if anchors else None
        for net, gs in by_net.items():
            u = unary_union(gs)
            for comp in getattr(u, "geoms", [u]):
                if comp.area < 0.5 or atree is None:
                    continue
                if not any(anchors[int(k)].intersects(comp) for k in atree.query(comp, predicate="intersects")):
                    islands += 1
                    pt = comp.representative_point()
                    c.add_loc(pt.x, pt.y, l.side, f"{net} island {comp.area:.1f} mm²")
    if slivers:
        c.worsen("WARN", f"{slivers} copper sliver(s) narrower than {w} mm in pours: set a minimum pour width or "
                         "remove them (slivers can lift in etching and short elsewhere)")
    if islands:
        c.worsen("WARN", f"{islands} copper island(s) with no pad or via (dead copper): remove islands in the zone settings")
    if c.status == "PASS":
        c.message = "No pour slivers or floating islands."
    return c


def check_pad_mask(layers):
    """Pads the solder mask covers: solder can't wet them (open joints)."""
    c = Check("pad_mask", "Pads exposed by the solder mask")
    covered = partial = tented_tht = 0
    covered_refs = set()
    for side in ("top", "bottom"):
        cu = next((l for l in layers if l.kind == "copper" and l.side == side), None)
        mk = next((l for l in layers if l.kind == "mask" and l.side == side), None)
        if cu is None or mk is None:
            continue
        cu.load_geometry()
        mk.load_geometry()
        for f in cu.features:
            if f.kind not in ("Flash", "Region") or f.func not in ("SMDPad", "BGAPad", "ComponentPad", "HeatsinkPad",
                                                                   "ConnectorPad", "CastellatedPad"):
                continue
            a = f.geom.area
            if a <= 1e-6 or f.smdef or (f.ref or "").upper().startswith("NT"):
                continue  # mask-defined pads and net ties are covered on purpose
            frac = f.geom.intersection(mk.union).area / a
            pt = f.geom.centroid
            label = f"{f.ref or ''}{'.' + f.pin if f.pin else ''}".strip(".") or f.func
            if frac < 0.5 and f.func in ("ComponentPad", "HeatsinkPad") and a < 0.8:
                tented_tht += 1  # small through-hole / thermal-via pads tented on purpose (stitching, thermal vias)
                continue
            if frac < 0.5 and f.func == "HeatsinkPad":
                partial += 1  # a heatsink pad on the far side of the board is sometimes covered on purpose
                c.add_loc(pt.x, pt.y, side, f"{label}: heatsink pad {frac:.0%} exposed")
            elif frac < 0.5:
                covered += 1
                covered_refs.add(f.ref or label)
                c.add_loc(pt.x, pt.y, side, f"{label}: {frac:.0%} exposed")
            elif frac < 0.9 and f.func != "HeatsinkPad":
                partial += 1
                c.add_loc(pt.x, pt.y, side, f"{label}: {frac:.0%} exposed")
    if covered:
        c.worsen("FAIL", f"{covered} pad(s) mostly covered by solder mask ({', '.join(sorted(covered_refs)[:12])}): "
                         "solder won't wet them")
    if tented_tht:
        c.worsen("INFO", f"{tented_tht} small through-hole or thermal-via pad(s) tented (stitching or thermal vias): fine if intended")
    if partial:
        c.worsen("WARN", f"{partial} pad(s) only partly exposed, or heatsink pads covered (mask opening misaligned, too "
                         "small, or deliberately tented on the far side)")
    if c.status == "PASS":
        c.message = "Every pad has its mask opening."
    return c


def check_tombstoning(layers):
    """Small two-pad parts with one pad on a solid pour and the other on a thin track reflow unevenly."""
    c = Check("tombstone", "Tombstoning risk (uneven pad heat)")
    n = 0
    for side in ("top", "bottom"):
        cu = next((l for l in layers if l.kind == "copper" and l.side == side), None)
        if cu is None:
            continue
        cu.load_geometry()
        pads = {}
        for f in cu.features:
            if f.kind == "Flash" and f.func == "SMDPad" and f.ref and f.net:
                pads.setdefault(f.ref, []).append(f)
        nets = {}
        for f in cu.features:
            if f.net and f.func in (None, "Conductor", "SMDPad", "ComponentPad", "ViaPad"):
                nets.setdefault(f.net, []).append(f.geom)
        net_u = {}
        for ref, ps in pads.items():
            if len(ps) != 2 or max(p.geom.area for p in ps) > 1.0:
                continue
            heat = []
            for p in ps:
                if p.net not in net_u:
                    net_u[p.net] = unary_union(nets[p.net])
                ring = p.geom.buffer(0.3).difference(p.geom)
                heat.append(ring.intersection(net_u[p.net]).area)
            lo, hi = min(heat), max(heat)
            if hi > 0.15 and hi > 4 * max(lo, 0.01):
                n += 1
                pt = unary_union([p.geom for p in ps]).centroid
                c.add_loc(pt.x, pt.y, side, f"{ref}: copper around pads {hi:.2f} vs {lo:.2f} mm²")
    if n:
        c.worsen("WARN", f"{n} small two-pad part(s) with one pad on a solid pour and the other on a thin track: "
                         "use thermal reliefs on the pour connection")
    else:
        c.message = "Small two-pad parts have balanced copper on both pads."
    return c


def check_copper_balance(layers, board):
    c = Check("copper_balance", "Copper balance (warp risk)")
    copper = sorted([l for l in layers if l.kind == "copper"],
                    key=lambda l: (0 if l.side == "top" else 2 if l.side == "bottom" else 1, l.index))
    if board is None or len(copper) < 2:
        c.worsen("INFO", "Skipped: needs an outline and two or more copper layers")
        c.message = "Not checked."
        return c
    area = board.area
    cov = []
    for l in copper:
        l.load_geometry()
        cov.append((l, l.union.intersection(board).area / area * 100))
    c.details.append("Coverage: " + ", ".join(f"{l.path.name.split('-')[-1]} {p:.0f}%" for l, p in cov))
    c.data = {"coverage_pct": [round(p, 1) for _, p in cov]}
    for i in range(len(cov) // 2):
        (la, pa), (lb, pb) = cov[i], cov[-1 - i]
        if abs(pa - pb) > 30:
            c.worsen("WARN", f"{la.path.name} has {pa:.0f}% copper but its mirror layer {lb.path.name} has {pb:.0f}%: "
                             "balance with a pour or hatch on the lighter layer to limit warp")
    if c.status == "PASS":
        c.message = "Mirrored layers carry similar copper."
    return c


def check_stackup(layers, job, paste_check):
    c = Check("stackup", "Stack-up and finish")
    n = sum(l.kind == "copper" for l in layers)
    spec = (job or {}).get("GeneralSpecs", {})
    finish = str(spec.get("Finish", "None"))
    if n > 2 and n % 2:
        c.worsen("WARN", f"{n} copper layers: fabs build even layer counts, so an odd count is quoted as {n + 1}")
    fine = any(isinstance(s, dict) and (s.get("fine_pitch_pads") or s.get("bga_pads")) for s in paste_check.data.values())
    if "HASL" in finish.upper() and fine:
        c.worsen("WARN", f"Finish is {finish} but the board has fine-pitch or BGA pads: HASL is uneven; choose ENIG")
    c.details.append(f"{n} copper layers, thickness {spec.get('BoardThickness', '?')} mm, finish {finish}")
    if c.status == "PASS":
        c.message = f"{n} layers, finish {finish}" + ("; choose ENIG for the fine-pitch parts" if fine and finish == "None" else ".")
    return c


PASTE_PADS = {"SMDPad", "BGAPad", "HeatsinkPad"}  # X2 pad functions that normally get solder paste


def check_paste(layers, holes, profile):
    """Solder paste placement against copper, mask, holes and nets; stencil aperture printability."""
    pc = Check("paste", "Solder paste placement")
    sc = Check("stencil", "Stencil apertures")
    t = profile["stencil_thickness_mm"]
    ar_min, asp_min, web = profile["min_stencil_area_ratio"], profile["min_stencil_aspect_ratio"], profile["min_stencil_web_mm"]
    t_note = f" at {t} mm stencil thickness" + (" (assumed; set stencil_thickness_mm)" if _assumed(profile, "stencil_thickness_mm") else "")
    hole_geoms = [Point(x, y).buffer(r) for (x, y, r, p) in holes if p]
    htree = STRtree(hole_geoms) if hole_geoms else None
    any_paste = False
    for side in ("top", "bottom"):
        cu = next((l for l in layers if l.kind == "copper" and l.side == side), None)
        pa = next((l for l in layers if l.kind == "paste" and l.side == side), None)
        mk = next((l for l in layers if l.kind == "mask" and l.side == side), None)
        if cu is None:
            continue
        cu.load_geometry()
        smd = [f for f in cu.features if f.func in PASTE_PADS]
        fids = [f for f in cu.features if f.func == "FiducialPad"]
        parts = []
        if pa is not None:
            parts = [p for p in pa.tree()[0] if p.area > 1e-4]
        if not parts:
            if smd and pa is None:
                pc.worsen("WARN", f"{side}: {len(smd)} SMD pad(s) but no solder paste file: export the paste layer "
                                  "if this side gets SMD parts")
            elif smd:
                pc.worsen("INFO", f"{side}: {len(smd)} SMD pad(s), paste file exported but empty: the footprints have "
                                  "no paste (solder jumpers, test pads), so no stencil for this side")
            else:
                pc.details.append(f"{side}: no SMD pads, no paste needed")
            continue
        any_paste = True
        if mk is not None:
            mk.load_geometry()
        pads = [f for f in cu.features if f.kind in ("Flash", "Region") and f.func and "Pad" in f.func]
        ptree = STRtree([f.geom for f in pads]) if pads else None
        n = {"off_cu": 0, "off_mask": 0, "bridge": 0, "hole": 0, "ar": 0, "asp": 0, "web": 0}
        ar_low, w_low = math.inf, math.inf
        for A in parts:
            a = A.area
            pt = A.representative_point()
            if A.difference(cu.union).area > max(0.01, 0.05 * a):
                n["off_cu"] += 1
                pc.add_loc(pt.x, pt.y, side, "paste beyond copper")
            if mk is not None and A.difference(mk.union).area > max(0.01, 0.05 * a):
                n["off_mask"] += 1
                pc.add_loc(pt.x, pt.y, side, "paste on solder mask")
            if ptree is not None:
                nets = sorted({pads[int(k)].net for k in ptree.query(A, predicate="intersects")
                               if pads[int(k)].net and pads[int(k)].geom.intersection(A).area > 0.001})
                if len(nets) >= 2:
                    n["bridge"] += 1
                    pc.worsen("FAIL", f"{side}: one paste aperture spans pads of {' and '.join(nets[:3])}: the paste will bridge them")
                    pc.add_loc(pt.x, pt.y, side, "paste bridges " + " / ".join(nets[:3]))
            if htree is not None and any(hole_geoms[int(k)].intersection(A).area > 0.001
                                         for k in htree.query(A, predicate="intersects")):
                n["hole"] += 1
                pc.add_loc(pt.x, pt.y, side, "paste over plated hole")
            # stencil printability (IPC-7525 style): area ratio and aspect ratio
            mrr = A.minimum_rotated_rectangle
            xs = list(mrr.exterior.coords) if mrr.geom_type == "Polygon" else []
            w = min(math.dist(xs[i], xs[i + 1]) for i in range(len(xs) - 1)) if len(xs) > 2 else 0.0
            area_ratio = a / (A.length * t) if A.length else math.inf
            ar_low, w_low = min(ar_low, area_ratio), min(w_low, w)
            if area_ratio < ar_min - 1e-3:
                n["ar"] += 1
                sc.add_loc(pt.x, pt.y, side, f"area ratio {area_ratio:.2f}")
            if w / t < asp_min - 1e-3:
                n["asp"] += 1
                sc.add_loc(pt.x, pt.y, side, f"aperture {w:.3f} mm wide")
        ptree_ap = STRtree(parts)
        gap_low = math.inf
        for i, A in enumerate(parts):
            for j in ptree_ap.query(A, predicate="dwithin", distance=max(web, 0.3)):
                j = int(j)
                if j <= i:
                    continue
                d = A.distance(parts[j])
                gap_low = min(gap_low, d)
                if 1e-6 < d < web - TOL:
                    n["web"] += 1
                    a_, b_ = nearest_points(A, parts[j])
                    sc.add_loc((a_.x + b_.x) / 2, (a_.y + b_.y) / 2, side, f"stencil web {d:.3f} mm")
        # per-pad paste coverage, measured on merged pad shapes: a thermal pad drawn with thermal-via pads inside it
        # is one pad, and its paste windows sit between the vias on purpose
        paste_u = pa.union
        su = unary_union([f.geom for f in smd]) if smd else Polygon()
        islands = [p for p in getattr(su, "geoms", [su]) if not p.is_empty]
        no_paste = starved = big_full = 0
        for pad in islands:
            pa_area = pad.area
            if pa_area <= 1e-6:
                continue
            cov = pad.intersection(paste_u).area / pa_area
            c0 = pad.centroid
            if cov == 0:
                no_paste += 1
            elif cov < 0.4:
                starved += 1
                pc.add_loc(c0.x, c0.y, side, f"{cov:.0%} paste coverage")
            elif pa_area > 6.0 and cov > 0.9:
                big_full += 1
                pc.add_loc(c0.x, c0.y, side, f"{pa_area:.1f} mm² pad at {cov:.0%} paste")
        msgs = [
            ("off_cu", "WARN", "paste aperture(s) extend beyond their copper pad (solder balls)"),
            ("off_mask", "WARN", "paste aperture(s) sit partly on solder mask"),
            ("hole", "WARN", "paste aperture(s) over plated holes (paste wicks into the hole; fine only for pin-in-paste)"),
        ]
        for key, st, text in msgs:
            if n[key]:
                pc.worsen(st, f"{side}: {n[key]} {text}")
        if starved:
            pc.worsen("WARN", f"{side}: {starved} SMD pad(s) get under 40% paste coverage (starved joints)")
        if no_paste:
            pc.worsen("INFO", f"{side}: {no_paste} of {len(islands)} SMD pads have no paste (fine for test points and no-paste pads)")
        if big_full:
            pc.worsen("INFO", f"{side}: {big_full} large pad(s) over 6 mm² get full paste: a windowpane pattern (50-80%) "
                              "stops the part floating")

        # fine pitch and BGA: the parts most prone to hidden bridges
        fine = 0
        if len(islands) > 1:
            cents = [p.centroid for p in islands]
            ctree = STRtree(cents)
            for i, cpt in enumerate(cents):
                near = [int(k) for k in ctree.query(cpt.buffer(0.51)) if int(k) != i]
                if any(cpt.distance(cents[k]) <= 0.5 + 1e-3 for k in near):
                    fine += 1
        bga = sum(1 for f in smd if f.func == "BGAPad")
        if fine or bga:
            pc.worsen("INFO", f"{side}: {fine} fine-pitch pad(s) (0.5 mm pitch or less)" + (f", {bga} BGA pad(s)" if bga else "")
                              + ": ask for AOI/X-ray inspection and consider a 0.10 mm stencil")
        if smd and len(fids) < 2:
            pc.worsen("WARN" if (fine or bga) else "INFO",
                      f"{side}: {len(fids)} fiducial(s) on an assembled side" + (" with fine-pitch/BGA parts" if (fine or bga) else "")
                      + ": assemblers want 2-3 for pick-and-place and AOI alignment")
        pc.data[side] = {"apertures": len(parts), "smd_pads": len(islands), "fiducials": len(fids), "fine_pitch_pads": fine,
                         "bga_pads": bga}
        pc.details.append(f"{side}: {len(parts)} paste apertures, {len(islands)} SMD pads, {len(fids)} fiducials")
        if n["ar"]:
            sc.worsen("WARN", f"{side}: {n['ar']} aperture(s) below area ratio {ar_min}{t_note} (lowest {ar_low:.2f}): "
                              "paste may not release; use a thinner stencil or larger apertures")
        if n["asp"]:
            sc.worsen("WARN", f"{side}: {n['asp']} aperture(s) narrower than {asp_min} x thickness{t_note} (narrowest {w_low:.3f} mm)")
        if n["web"]:
            sc.worsen("WARN", f"{side}: {n['web']} stencil web(s) between apertures under {web} mm (assumed rule)")
        sc.details.append(f"{side}: lowest area ratio {ar_low:.2f}, narrowest aperture {w_low:.3f} mm, "
                          f"closest apertures {gap_low:.3f} mm".replace("inf mm", "n/a"))
    if not any_paste:
        if pc.status in ("PASS", "INFO"):
            pc.message = "No paste apertures; nothing to check."
        sc.worsen("INFO", "No paste apertures, so no stencil to check")
        sc.message = "Not applicable."
    if pc.status in ("PASS", "INFO") and any_paste:
        pc.message = "Paste sits on its pads, inside mask openings, with no bridges."
    if sc.status == "PASS":
        sc.message = f"Apertures meet area ratio {ar_min} and aspect ratio {asp_min}{t_note}."
    return [pc, sc]


def check_mask(layers, profile):
    """Solder mask dams: mask strips between two separate openings narrower than the fab can hold."""
    c = Check("mask_dam", "Solder mask dams between pads")
    dam = profile["min_mask_dam_mm"]
    for l in [x for x in layers if x.kind == "mask"]:
        parts, tree = l.tree()
        if tree is None:
            continue
        thin, gmin, seen = 0, math.inf, set()
        for i, p in enumerate(parts):
            for j in tree.query(p, predicate="dwithin", distance=max(dam * 3, 0.3)):
                j = int(j)
                if j <= i:
                    continue
                d = p.distance(parts[j])
                if d <= 1e-6:
                    continue  # openings already merged: no dam intended
                gmin = min(gmin, d)
                if d < dam - TOL:
                    thin += 1
                    a, b = nearest_points(p, parts[j])
                    key = (round((a.x + b.x) / 2, 1), round((a.y + b.y) / 2, 1))
                    if key not in seen:
                        seen.add(key)
                        c.add_loc((a.x + b.x) / 2, (a.y + b.y) / 2, l.side, f"mask dam {d:.3f} mm")
        if gmin < math.inf:
            c.details.append(f"{l.path.name}: narrowest mask dam {gmin:.3f} mm")
        if thin:
            c.worsen("WARN", f"{l.path.name}: {thin} mask dam(s) narrower than {dam} mm; the fab will remove them, "
                             "leaving neighbouring pads in one opening (solder bridge risk)")
    if c.status == "PASS":
        c.message = f"Mask dams are at least {dam} mm."
    return c


def _text_clusters(layer: Layer):
    """Group silkscreen strokes into glyphs, and glyphs into text runs. Returns [(polygon, stroke widths, n_glyphs)]."""
    feats = [f for f in layer.features if f.kind in ("Line", "Arc") and f.width]
    if not feats:
        return []
    u = unary_union([f.geom for f in feats])
    comps = [g for g in getattr(u, "geoms", [u]) if not g.is_empty]
    tree = STRtree(comps)
    segs, widths = [0] * len(comps), [[] for _ in comps]
    for f in feats:
        hit = tree.query(f.geom.representative_point(), predicate="within")
        if len(hit):
            k = int(hit[0])
            segs[k] += 1
            widths[k].append(f.width)
    glyphs = []
    for k, g in enumerate(comps):
        minx, miny, maxx, maxy = g.bounds
        size = max(maxx - minx, maxy - miny)
        if segs[k] >= 2 and size <= 3.0:  # strokes of a character, not an outline
            glyphs.append((g, size, widths[k]))
    if not glyphs:
        return []
    halos = unary_union([g.buffer(max(0.12, 0.3 * s)) for g, s, _ in glyphs])
    runs = [r for r in getattr(halos, "geoms", [halos])]
    rtree = STRtree(runs)
    members = [[] for _ in runs]
    for g, s, w in glyphs:
        hit = rtree.query(g.representative_point(), predicate="within")
        if len(hit):
            members[int(hit[0])].append((g, w))
    out = []
    for m in members:
        if len(m) >= 2:
            out.append((unary_union([g for g, _ in m]), [x for _, w in m for x in w], len(m)))
    return out


def check_silk_text(layers, profile):
    c = Check("silk_text", "Silkscreen text size and stroke")
    hmin_rule, wmin_rule = profile["min_silk_text_height_mm"], profile["min_silk_line_width_mm"]
    for l in [x for x in layers if x.kind == "legend"]:
        l.load_geometry()
        runs = _text_clusters(l)
        if not runs:
            c.details.append(f"{l.path.name}: no text found")
            continue
        small = thin = 0
        hmin = wmin = math.inf
        for poly, widths, n in runs:
            minx, miny, maxx, maxy = poly.bounds
            h = min(maxx - minx, maxy - miny)  # text runs are longer than they are tall
            w = sorted(widths)[len(widths) // 2] if widths else math.inf  # typical stroke, not stray outline lines
            hmin, wmin = min(hmin, h), min(wmin, w)
            pt = poly.centroid
            if h < hmin_rule - 0.02:
                small += 1
                c.add_loc(pt.x, pt.y, l.side, f"text about {h:.2f} mm tall")
            if w < wmin_rule - 1e-6:
                thin += 1
                if h >= hmin_rule - 0.02:
                    c.add_loc(pt.x, pt.y, l.side, f"text stroke {w:.3f} mm")
        c.details.append(f"{l.path.name}: {len(runs)} text runs; smallest about {hmin:.2f} mm tall, thinnest typical stroke {wmin:.3f} mm")
        if small:
            c.worsen("WARN", f"{l.path.name}: {small} text run(s) under {hmin_rule} mm tall may be unreadable")
        if thin:
            c.worsen("WARN", f"{l.path.name}: {thin} text run(s) with strokes under {wmin_rule} mm may not print")
    if c.status == "PASS":
        c.message = f"Text is at least {hmin_rule} mm tall with {wmin_rule} mm strokes (estimated from stroke groups)."
    return c


def check_silk(layers, profile=None):
    c = Check("silk", "Silkscreen over exposed pads")
    gap = (profile or {}).get("silk_to_pad_mm", 0.0) or 0.0
    for side in ("top", "bottom"):
        leg = [l for l in layers if l.kind == "legend" and l.side == side]
        msk = [l for l in layers if l.kind == "mask" and l.side == side]
        if not leg or not msk:
            continue
        leg[0].load_geometry()
        msk[0].load_geometry()
        over = leg[0].union.intersection(msk[0].union.buffer(gap - TOL) if gap > TOL else msk[0].union)
        if over.area > 0.05:
            what = f"within {gap} mm of" if gap > TOL else "on"
            c.worsen("WARN", f"{side}: {over.area:.2f} mm² of silkscreen sits {what} mask openings (the fab will clip it)")
            for g in sorted(getattr(over, "geoms", [over]), key=lambda g: g.area, reverse=True)[:5]:
                pt = g.representative_point()
                c.add_loc(pt.x, pt.y, side, f"{g.area:.2f} mm² silk on pad")
        else:
            c.details.append(f"{side}: no silkscreen on exposed copper")
    c.message = "Silkscreen clear of pads." if c.status == "PASS" else "Some silkscreen overlaps exposed pads."
    return c


# ---------------------------------------------------------------- render
def _path(g):
    out = []
    for poly in getattr(g, "geoms", [g]):
        if poly.is_empty or not hasattr(poly, "exterior"):
            continue
        for ring in [poly.exterior, *poly.interiors]:
            pts = list(ring.coords)
            out.append("M" + " L".join(f"{x:.3f},{-y:.3f}" for x, y in pts) + " Z")
    return " ".join(out)


def render(layers, holes, board, checks, out_dir: Path):
    files = []
    if board is None:
        cu = [l.union for l in layers if l.kind == "copper" and l.union is not None]
        extent = unary_union(cu).bounds if cu else (0, 0, 10, 10)
    else:
        extent = board.bounds
    minx, miny, maxx, maxy = extent
    pad = 3
    vw, vh = maxx - minx + 2 * pad, maxy - miny + 2 * pad
    vb = f"{minx - pad:.2f} {-maxy - pad:.2f} {vw:.2f} {vh:.2f}"
    px_w = 1200
    px_h = round(px_w * vh / vw)
    mark_r = max(1.5, math.hypot(vw, vh) * 0.012)
    for side in ("top", "bottom"):
        cu = [l for l in layers if l.kind == "copper" and l.side == side]
        lg = [l for l in layers if l.kind == "legend" and l.side == side]
        mk = [l for l in layers if l.kind == "mask" and l.side == side]
        for l in cu + lg + mk:
            l.load_geometry()
        body = [f'<rect x="{minx - pad:.2f}" y="{-maxy - pad:.2f}" width="{vw:.2f}" height="{vh:.2f}" fill="#1b1b1b"/>']
        if board is not None:
            body.append(f'<path d="{_path(board)}" fill="#0d4a32" fill-rule="evenodd"/>')
        if cu:
            body.append(f'<path d="{_path(cu[0].union)}" fill="#b8913a" fill-rule="evenodd" opacity="0.9"/>')
        if mk:
            body.append(f'<path d="{_path(mk[0].union)}" fill="#e8d6a0" fill-rule="evenodd" opacity="0.55"/>')
        if lg:
            body.append(f'<path d="{_path(lg[0].union)}" fill="#ffffff" fill-rule="evenodd" opacity="0.9"/>')
        for (x, y, r, plated) in holes:
            body.append(f'<circle cx="{x:.3f}" cy="{-y:.3f}" r="{r:.3f}" fill="#111"/>')
        for chk in checks:
            for loc in chk.locations:
                if loc["side"] in (side, "drill", "outline"):
                    body.append(f'<circle cx="{loc["x"]}" cy="{loc["y"]:.3f}" r="{mark_r:.2f}" fill="none" stroke="#ff2d2d" '
                                f'stroke-width="{mark_r / 4:.2f}">'
                                f'<title>{chk.title}: {loc["note"]}</title></circle>')
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="{px_w}" height="{px_h}">'
               f'<title>{side} view (red rings = findings)</title>{"".join(body)}</svg>')
        p = out_dir / f"preview_{side}.svg"
        p.write_text(svg, encoding="utf-8")
        files.append(str(p))
    return files


# ---------------------------------------------------------------- driver
def find_kicad_cli():
    env = os.environ.get("KICAD_CLI")
    if env and Path(env).exists():
        return env
    w = shutil.which("kicad-cli")
    if w:
        return w
    for v in ("10.0", "9.0", "8.0"):
        p = Path(rf"C:\Program Files\KiCad\{v}\bin\kicad-cli.exe")
        if p.exists():
            return str(p)
    return None


def apply_layer_rules(profile: dict, n_copper: int, overrides: dict | None):
    """Some fabs tighten rules for multilayer boards (JLCPCB: 0.09 mm trace, 0.15 mm ring). Overrides still win."""
    table = profile.get("by_layers") or {}
    for key in sorted(table, key=int):
        if int(key) <= n_copper:
            for k, v in table[key].items():
                if k not in (overrides or {}):
                    profile[k] = v
    profile["_copper_layers"] = n_copper


def run(path: str, profile_name: str = "pcbway_standard", board: str | None = None, out: str | None = None,
        overrides: dict | None = None) -> dict:
    if profile_name not in PROFILES:
        raise ValueError(f"Unknown profile '{profile_name}'. Known: {', '.join(PROFILES)}")
    profile = dict(PROFILES[profile_name])
    for k, v in (overrides or {}).items():
        numeric = [x for x in profile if not x.startswith("_") and isinstance(profile[x], (int, float))]
        if k not in numeric:
            raise ValueError(f"Unknown rule '{k}'. Rules: {', '.join(numeric)}")
        profile[k] = float(v)
    profile["_overrides"] = dict(overrides or {})
    src = Path(path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"No such file or folder: {src}")
    board_file = Path(board).resolve() if board else None
    if board_file and not board_file.exists():
        raise FileNotFoundError(f"No such board file: {board_file}")
    with tempfile.TemporaryDirectory(prefix="gerber_check_") as tmp:
        workdir = Path(tmp)
        gdir = _unpack(src, workdir)
        out_dir = Path(out).resolve() if out else (src.parent if src.is_file() else src) / "_gerber_check"
        out_dir.mkdir(parents=True, exist_ok=True)
        layers, unknown, errors, job = load_set(gdir)
        apply_layer_rules(profile, sum(l.kind == "copper" for l in layers), overrides)
        checks = [check_completeness(layers, unknown, errors, job, profile)]
        oc, board_poly = build_outline(layers, job)
        checks.append(oc)
        checks.append(check_fills(layers, board_file, find_kicad_cli(), workdir))
        dc, holes = check_drills(layers, profile)
        checks.append(dc)
        checks.extend(check_dfm(layers, holes, board_poly, profile))
        checks.extend(check_hole_clearance(layers, holes, profile))
        checks.append(check_pth_clearance(layers, holes, profile))
        checks.append(check_hole_spacing(layers, profile))
        checks.append(check_mask(layers, profile))
        checks.extend(check_paste(layers, holes, profile))
        checks.append(check_silk(layers, profile))
        checks.append(check_silk_text(layers, profile))
        checks.append(check_silk_cutouts(layers, board_poly))
        checks.append(check_vias(layers))
        checks.append(check_pad_mask(layers))
        checks.append(check_acid_traps(layers))
        checks.append(check_aspect_ratio(holes, job, profile))
        checks.append(check_slivers_islands(layers, profile))
        checks.append(check_tombstoning(layers))
        checks.append(check_copper_balance(layers, board_poly))
        checks.append(check_stackup(layers, job, next(c for c in checks if c.id == "paste")))
        previews = render(layers, holes, board_poly, checks, out_dir)
    for c in checks:  # lead each failing row with its most important finding
        if c.status in ("WARN", "FAIL"):
            first = next((d for d in c.details if d.startswith(f"[{c.status}]")), None)
            if first:
                c.message = first.split("] ", 1)[1]
    verdict = "PASS"
    for c in checks:
        if STATUS_RANK[c.status] > STATUS_RANK[verdict]:
            verdict = c.status
    report = {
        "verdict": verdict,
        "profile": profile_name,
        "rules": profile,
        "inner_iso_mm": _inner_iso(profile, sum(l.kind == "copper" for l in layers))[0],
        "source": str(src),
        "board_file": str(board_file) if board_file else None,
        "layers": [{"file": l.path.name, "function": l.function, "kind": l.kind, "side": l.side,
                    "objects": len(l.file.objects)} for l in layers],
        "checks": [c.__dict__ for c in checks],
        "previews": previews,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (out_dir / "report.md").write_text(to_markdown(report), encoding="utf-8")
    report["report_files"] = [str(out_dir / "report.md"), str(out_dir / "report.json")]
    return report


def to_markdown(rep: dict) -> str:
    r = rep["rules"]
    lines = [f"# Gerber check: {rep['verdict']}", "",
             f"Profile **{r['manufacturer']} {r['tier']}** (`{rep['profile']}`): trace/space {r['min_trace_width_mm']}/"
             f"{r['min_trace_clearance_mm']} mm, drill {r['min_drill_mm']} mm, ring {r['min_annular_ring_mm']} mm, "
             f"edge {r['copper_to_edge_mm']} mm, NPTH-to-copper {r['npth_to_copper_mm']} mm, PTH-to-copper "
             f"{r['pth_to_copper_mm']} mm, inner hole-to-copper {rep.get('inner_iso_mm', r['inner_hole_to_copper_mm'])} mm, hole-to-hole {r['min_hole_to_hole_mm']} mm (vias "
             f"{r['min_via_hole_to_hole_mm']} mm), mask dam {r['min_mask_dam_mm']} mm, silk text "
             f"{r['min_silk_text_height_mm']} mm / stroke {r['min_silk_line_width_mm']} mm, stencil "
             f"{r['stencil_thickness_mm']} mm (area ratio {r['min_stencil_area_ratio']}, aspect {r['min_stencil_aspect_ratio']}, "
             f"web {r['min_stencil_web_mm']} mm)."
             + (f" Overrides: {r['_overrides']}." if r.get("_overrides") else ""),
             f"Source: `{rep['source']}`", "",
             "| Check | Status | Result |", "| --- | --- | --- |"]
    for c in rep["checks"]:
        lines.append(f"| {c['title']} | {c['status']} | {c['message']} |")
    for c in rep["checks"]:
        if c["details"] or c["locations"]:
            lines += ["", f"## {c['title']} ({c['status']})"]
            lines += [f"- {d}" for d in c["details"]]
            for loc in c["locations"]:
                lines.append(f"- at ({loc['x']}, {loc['y']}) mm, {loc['side']}: {loc['note']}")
    lines += ["", "Coordinates are in KiCad's frame (Y down). Previews: " + ", ".join(f"`{Path(p).name}`" for p in rep["previews"])]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", help="Folder of Gerber/drill files, or a .zip")
    ap.add_argument("--profile", default="pcbway_standard", choices=sorted(PROFILES))
    ap.add_argument("--board", help=".kicad_pcb to compare copper against a fresh zone refill")
    ap.add_argument("--out", help="Report folder (default: <gerbers>/_gerber_check)")
    ap.add_argument("--set", action="append", default=[], metavar="RULE=VALUE",
                    help="Override one profile rule, e.g. --set min_mask_dam_mm=0.1 (repeatable)")
    ap.add_argument("--json", action="store_true", help="Print the JSON report instead of Markdown")
    a = ap.parse_args(argv)
    overrides = dict(s.split("=", 1) for s in a.set)
    rep = run(a.path, a.profile, a.board, a.out, overrides)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(rep, indent=1) if a.json else to_markdown(rep))
    return {"PASS": 0, "INFO": 0, "WARN": 1, "FAIL": 2}[rep["verdict"]]


if __name__ == "__main__":
    sys.exit(main())

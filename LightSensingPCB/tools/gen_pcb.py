"""Generate LightSensingPCB.kicad_pcb from design.py: outline, footprints, tracks, vias, GND pours.

Run with KiCad's own Python (it provides pcbnew):

    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools\\gen_pcb.py
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).parent))
import design as D  # noqa: E402

FPLIB = Path(r"C:\Program Files\KiCad\10.0\share\kicad\footprints")
OUT = Path(__file__).resolve().parent.parent / "LightSensingPCB.kicad_pcb"
OX, OY = 100.0, 100.0          # board origin on the KiCad page
TRACK, PWR_TRACK = 0.25, 0.5
VIA_D, VIA_DRILL = 0.8, 0.3
PROJECT = "LightSensingPCB"


def P(x, y):
    return pcbnew.VECTOR2I(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))


def sym_uuid(ref):  # must match gen_schematic.py
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{PROJECT}/sym/{ref}"))


board = pcbnew.BOARD()
ds = board.GetDesignSettings()
ds.SetBoardThickness(pcbnew.FromMM(D.BOARD_THICKNESS))
ds.m_TrackMinWidth = pcbnew.FromMM(0.15)
ds.m_MinClearance = pcbnew.FromMM(0.15)
ds.m_CopperEdgeClearance = pcbnew.FromMM(0.3)
ds.m_HoleClearance = pcbnew.FromMM(0.25)
ds.m_HoleToHoleMin = pcbnew.FromMM(0.5)

nets = {}


def net(name):
    if name not in nets:
        ni = pcbnew.NETINFO_ITEM(board, name)
        board.Add(ni)
        nets[name] = ni
    return nets[name]


def netname(n):  # schematic local labels get a "/" prefix
    return n if n in ("+5V", "+3V3", "GND") else "/" + n


# --- outline --------------------------------------------------------------------------------------------------
rect = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_RECTANGLE)
rect.SetStart(P(0, 0))
rect.SetEnd(P(D.BOARD_W, D.BOARD_H))
rect.SetLayer(pcbnew.Edge_Cuts)
rect.SetWidth(pcbnew.FromMM(0.1))
board.Add(rect)

# --- footprints -----------------------------------------------------------------------------------------------
fps = {}


def load(fpid):
    lib, name = fpid.split(":")
    return pcbnew.FootprintLoad(str(FPLIB / f"{lib}.pretty"), name)


def place(part, x, y, rot=0.0):
    fp = load(part["fp"])
    fp.SetFPID(pcbnew.LIB_ID(*part["fp"].split(":")))
    fp.SetReference(part["ref"])
    fp.SetValue(part["value"])
    fp.SetField("LCSC", part["lcsc"])
    fp.SetField("MPN", part["mpn"])
    for f in fp.GetFields():
        if f.GetName() in ("LCSC", "MPN"):
            f.SetVisible(False)
            f.SetLayer(pcbnew.F_Fab)
    fp.SetPath(pcbnew.KIID_PATH("/" + sym_uuid(part["ref"])))
    fp.SetSheetname("/")
    fp.SetSheetfile(f"{PROJECT}.kicad_sch")
    board.Add(fp)
    fp.SetPosition(P(x, y))
    fp.SetOrientationDegrees(rot)
    if part["side"] == "bottom":
        fp.Flip(P(x, y), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    for pad in fp.Pads():
        n = D.NETS[part["ref"]].get(int(pad.GetNumber()))
        if n:
            pad.SetNet(net(netname(n)))
        else:  # same name the schematic gives an unconnected pin
            pin = "NC" if part["ref"] == "U1" else f"Pin_{pad.GetNumber()}"
            pad.SetNet(net(f"unconnected-({part['ref']}-{pin}-Pad{pad.GetNumber()})"))
    fps[part["ref"]] = fp
    return fp


def pad_xy(ref, num):
    p = [pd for pd in fps[ref].Pads() if pd.GetNumber() == str(num)][0].GetPosition()
    return round(pcbnew.ToMM(p.x) - OX, 3), round(pcbnew.ToMM(p.y) - OY, 3)


parts = {p["ref"]: p for p in D.PARTS}

# J1 on the bottom: try orientations until pad 1/2/3 sit where the host port needs them.
want = {1: (D.J1_X0, D.J1_ODD_Y), 2: (D.J1_X0, D.J1_EVEN_Y), 3: (D.J1_X0 + 2.54, D.J1_ODD_Y)}
for rot in (0, 90, 180, 270):
    if "J1" in fps:
        board.Remove(fps.pop("J1"))
    place(parts["J1"], D.J1_X0, D.J1_ODD_Y, rot)
    off = (D.J1_X0 - pad_xy("J1", 1)[0], D.J1_ODD_Y - pad_xy("J1", 1)[1])
    fps["J1"].Move(pcbnew.VECTOR2I(pcbnew.FromMM(off[0]), pcbnew.FromMM(off[1])))
    if all(abs(pad_xy("J1", n)[0] - x) < 0.01 and abs(pad_xy("J1", n)[1] - y) < 0.01 for n, (x, y) in want.items()):
        break
else:
    raise SystemExit("no J1 orientation puts pin 1/2/3 where the port needs them")

# J2 (socket for the breakout): pins along x, pin 1 (VIN) on the left
for rot in (0, 90, 180, 270):
    if "J2" in fps:
        board.Remove(fps.pop("J2"))
    place(parts["J2"], D.J2_X0, D.J2_Y, rot)
    if pad_xy("J2", 2)[0] > pad_xy("J2", 1)[0] + 2 and abs(pad_xy("J2", 2)[1] - D.J2_Y) < 0.01:
        break

place(parts["U1"], 64.0, 10.0, 0)
place(parts["C1"], 59.2, 10.0, 270)
place(parts["C2"], 68.4, 10.0, 270)

# No standoff holes for the breakout: at its hole positions an M2.5 standoff would hit J1's pins and J2's socket.
# The socket holds the breakout (about 2 g).


# --- routing --------------------------------------------------------------------------------------------------
def track(netn, layer, pts, width=TRACK):
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(P(x1, y1))
        t.SetEnd(P(x2, y2))
        t.SetWidth(pcbnew.FromMM(width))
        t.SetLayer(layer)
        t.SetNet(net(netn))
        board.Add(t)


def via(netn, x, y):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(P(x, y))
    v.SetWidth(pcbnew.FromMM(VIA_D))
    v.SetDrill(pcbnew.FromMM(VIA_DRILL))
    v.SetNet(net(netn))
    board.Add(v)


F, B = pcbnew.F_Cu, pcbnew.B_Cu
j1 = {n: pad_xy("J1", n) for n in range(1, 15)}
j2 = {n: pad_xy("J2", n) for n in range(1, 7)}
u1 = {n: pad_xy("U1", n) for n in range(1, 6)}
c1 = {n: pad_xy("C1", n) for n in (1, 2)}
c2 = {n: pad_xy("C2", n) for n in (1, 2)}

# SDA, top: straight down, jog right to J2 pin 4
x8, y8 = j1[8]
track("/SDA", F, [j1[8], (x8, 8.0), (x8 + 0.6, 8.6), (x8 + 0.6, 15.5), (j2[4][0], 15.5 + (j2[4][0] - x8 - 0.6)), j2[4]])
# SCL, bottom: down, diagonal left, down to J2 pin 3
x10, y10 = j1[10]
track("/SCL", B, [j1[10], (x10, 9.0), (j2[3][0], 9.0 + (x10 - j2[3][0])), j2[3]])
# INT, top: up to a corridor along the keyboard-side edge, round the right of the breakout holes, into J2 pin 6
x5, y5 = j1[5]
track("/SENSOR_INT", F, [j1[5], (x5, 1.5), (54.5, 1.5), (54.5, 17.5), (j2[6][0], 17.5), j2[6]])
# GPIO, bottom: left of the breakout holes, then under the J2 row into pin 5
x1, y1 = j1[1]
track("/SENSOR_GPIO", B, [j1[1], (30.5, y1), (29.6, y1 + 0.9), (29.6, 21.6), (30.5, 22.5),
                          (j2[5][0] - 0.9, 22.5), (j2[5][0], 21.6), j2[5]])
# +5V: J1 pin 6 down on top, across SCL to a via, along the bottom to the LDO, bottom jumper for EN
x6, y6 = j1[6]
track("+5V", F, [j1[6], (x6, 14.0), (41.5, 14.0)], PWR_TRACK)
via("+5V", 41.5, 14.0)
va, vb = (61.4, 8.4), (61.4, 11.6)
track("+5V", B, [(41.5, 14.0), (59.0, 14.0), vb], PWR_TRACK)
track("+5V", B, [vb, va], PWR_TRACK)
via("+5V", *va)
via("+5V", *vb)
c1_5v, c1_g = (c1[1], c1[2]) if D.NETS["C1"][1] == "+5V" else (c1[2], c1[1])
track("+5V", F, [u1[1], va], PWR_TRACK)
track("+5V", F, [u1[3], vb], PWR_TRACK)
track("+5V", F, [c1_5v, (c1_5v[0], va[1]), va], PWR_TRACK)
# +3V3: LDO out to C2, then along the board edge under the J2 row into J2 pin 1 (top)
c2_3v = c2[1]
track("+3V3", F, [u1[5], c2_3v], PWR_TRACK)
track("+3V3", F, [c2_3v, (70.5, c2_3v[1]), (70.5, 22.6), (j2[1][0], 22.6), j2[1]], PWR_TRACK)

# GND stitching vias (both pours are GND)
for x, y in [(4, 4), (4, 20), (20, 4), (20, 20), (60, 4), (60, 18), (66, 14), (75, 4), (80, 20), (80, 12), (26, 12),
             (36.9, 12), (47, 12), (57.5, 10.0), (63.0, 13.0)]:
    via("GND", x, y)

# --- GND pours on both layers ------------------------------------------------------------------------------------
for layer in (F, B):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(net("GND"))
    ol = z.Outline()
    ol.NewOutline()
    for x, y in [(0, 0), (D.BOARD_W, 0), (D.BOARD_W, D.BOARD_H), (0, D.BOARD_H)]:
        ol.Append(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))
    z.SetLocalClearance(pcbnew.FromMM(0.3))
    z.SetMinThickness(pcbnew.FromMM(0.25))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    z.SetThermalReliefGap(pcbnew.FromMM(0.3))
    z.SetThermalReliefSpokeWidth(pcbnew.FromMM(0.4))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    board.Add(z)


# --- silkscreen -----------------------------------------------------------------------------------------------
def text(s, x, y, layer=pcbnew.F_SilkS, size=1.0, rot=0, just=None):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetPosition(P(x, y))
    t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(size), pcbnew.FromMM(size)))
    t.SetTextThickness(pcbnew.FromMM(0.15))
    t.SetTextAngleDegrees(rot)
    if layer == pcbnew.B_SilkS:
        t.SetMirrored(True)
    if just == "left":
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
    board.Add(t)


text("LightSensingPCB v0.1", 14.0, 8.5)
text("AS7343 cap", 14.0, 10.5)
text("CardputerZero EXT", 14.0, 12.5)
text("Keyboard side", 76.0, 2.5)
for n, (lbl, _) in D.BREAKOUT_PINS.items():
    text(lbl, j2[n][0], 16.6, rot=90)
text("5V>3V3", 64.0, 13.6)
text("LightSensingPCB v0.1", 70.0, 20.0, pcbnew.B_SilkS)
text("Pin 1 = G26  keyboard side", 42.0, 9.5, pcbnew.B_SilkS)
text("Power off before fitting", 70.0, 17.0, pcbnew.B_SilkS)

board.BuildConnectivity()
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
print("zones filled:", [z.IsFilled() for z in board.Zones()])
board.Save(str(OUT))
print(f"wrote {OUT}")
print("J1 pads:", {n: j1[n] for n in (1, 2, 3, 13, 14)})
print("J2 pads:", j2)
print("U1 pads:", u1, "C1:", c1, "C2:", c2)

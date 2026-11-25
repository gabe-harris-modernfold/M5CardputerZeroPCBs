"""Generate BreadPort.kicad_pcb from design.py: outline, footprints, tracks, vias, GND pours.

Run with KiCad's own Python (it provides pcbnew):

    & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" tools\\gen_pcb.py

Layout (top view, keyboard side at the top edge):
- J1 (host header) on the bottom, centred. Host SDA/SCL drop into Q1/Q2 (I2C isolation) just below it; TX/RX drop
  through R10/R11 into the UART socket.
- Power chain right of J1 along the top: C1, U1 (current-limited switch), C2, U2 (LDO), C3, C4, power LED.
- U3 (ADS1115) under the power chain, R5-R8 under it, into the analog socket.
- Four Qwiic connectors above the I2C sockets; each fans out on the top layer into its socket.
- SDA, SCL and 3V3 run the length of the board as bottom-layer lanes between the Qwiics and the sockets.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).parent))
import design as D  # noqa: E402

FPLIB = D.KICAD / "share" / "kicad" / "footprints"
OUT = Path(__file__).resolve().parent.parent / "BreadPort.kicad_pcb"
OX, OY = 100.0, 100.0          # board origin on the KiCad page
TRACK, PWR_TRACK, FINE = 0.25, 0.5, 0.2
VIA_D, VIA_DRILL = 0.8, 0.3
PROJECT = "BreadPort"


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


# --- outline --------------------------------------------------------------------------------------------------
rect = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_RECTANGLE)
rect.SetStart(P(0, 0))
rect.SetEnd(P(D.BOARD_W, D.BOARD_H))
rect.SetLayer(pcbnew.Edge_Cuts)
rect.SetWidth(pcbnew.FromMM(0.1))
board.Add(rect)

# --- footprints -----------------------------------------------------------------------------------------------
fps = {}
parts = {p["ref"]: p for p in D.PARTS}
# names the schematic gives unconnected pins (net "unconnected-(<ref>-<pin name>-Pad<n>)")
NC_PIN_NAMES = {"U2": {"4": "NC"}, "U3": {"2": "ALERT{slash}RDY"}}


def load(fpid):
    lib, name = fpid.split(":")
    return pcbnew.FootprintLoad(str(FPLIB / f"{lib}.pretty"), name)


def place(ref, x, y, rot=0.0):
    part = parts[ref]
    if ref in fps:
        board.Remove(fps.pop(ref))
    fp = load(part["fp"])
    fp.SetFPID(pcbnew.LIB_ID(*part["fp"].split(":")))
    fp.SetReference(ref)
    fp.SetValue(part["value"])
    fp.SetField("LCSC", part["lcsc"])
    fp.SetField("MPN", part["mpn"])
    for f in fp.GetFields():
        if f.GetName() in ("LCSC", "MPN"):
            f.SetVisible(False)
            f.SetLayer(pcbnew.F_Fab)
    # references go on the fab layer: the board is too dense for them on silk, and the silk labels say what
    # each connector is for
    fp.Reference().SetLayer(pcbnew.F_Fab)   # a bottom part flips this to B.Fab below
    if ref == "U3":   # 0.5 mm pitch: the pads themselves are 0.15 mm apart (JLCPCB/PCBWay minimum is 0.1 mm)
        fp.SetLocalClearance(pcbnew.FromMM(0.15))
    fp.SetPath(pcbnew.KIID_PATH("/" + sym_uuid(ref)))
    fp.SetSheetname("/")
    fp.SetSheetfile(f"{PROJECT}.kicad_sch")
    board.Add(fp)
    fp.SetPosition(P(x, y))
    fp.SetOrientationDegrees(rot)
    if part["side"] == "bottom":
        fp.Flip(P(x, y), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    for pad in fp.Pads():
        num = pad.GetNumber()
        n = D.NETS[ref].get(int(num) if num.isdigit() else num)
        if n:
            pad.SetNet(net(D.pcb_net(n)))
        else:
            pin = NC_PIN_NAMES.get(ref, {}).get(num, f"Pin_{num}")
            pad.SetNet(net(f"unconnected-({ref}-{pin}-Pad{num})"))
    fps[ref] = fp
    return fp


def pad_xy(ref, num):
    p = [pd for pd in fps[ref].Pads() if pd.GetNumber() == str(num)][0].GetPosition()
    return round(pcbnew.ToMM(p.x) - OX, 3), round(pcbnew.ToMM(p.y) - OY, 3)


def place_oriented(ref, x, y, test, rots=(0, 90, 180, 270)):
    """Place at (x, y), trying rotations until test() is true."""
    for rot in rots:
        place(ref, x, y, rot)
        if test():
            return
    raise SystemExit(f"no orientation of {ref} passes its placement test")


def near(a, b):
    return abs(a[0] - b[0]) < 0.01 and abs(a[1] - b[1]) < 0.01


# J1 on the bottom: find the orientation that puts pads 1/2/3 where the host port needs them, then nudge.
want = {1: (D.J1_X0, D.J1_ODD_Y), 2: (D.J1_X0, D.J1_EVEN_Y), 3: (D.J1_X0 + 2.54, D.J1_ODD_Y)}
for rot in (0, 90, 180, 270):
    place("J1", D.J1_X0, D.J1_ODD_Y, rot)
    off = (D.J1_X0 - pad_xy("J1", 1)[0], D.J1_ODD_Y - pad_xy("J1", 1)[1])
    fps["J1"].Move(pcbnew.VECTOR2I(pcbnew.FromMM(off[0]), pcbnew.FromMM(off[1])))
    if all(near(pad_xy("J1", n), xy) for n, xy in want.items()):
        break
else:
    raise SystemExit("no J1 orientation puts pin 1/2/3 where the port needs them")

# Sockets: pins along x, pin 1 on the left
for ref, (x0, _, _) in D.SOCKETS.items():
    place_oriented(ref, x0, D.SOCKET_Y,
                   lambda r=ref: pad_xy(r, 2)[0] > pad_xy(r, 1)[0] + 2 and abs(pad_xy(r, 2)[1] - D.SOCKET_Y) < 0.01)

# Qwiic connectors, signal pads facing the sockets (+y), pin 1 (GND) on the left
QC = {}
for q, s in D.QWIIC.items():
    QC[q] = D.SOCKETS[s][0] + 1.5 * D.P
    place(q, QC[q], D.QWIIC_Y, 0)

# I2C isolation FETs. Q1: drain on the right, under J1 pin 8. Q2: drain on the left, under J1 pin 10.
SDA_X, SCL_X, TX_X, RX_X = (D.J1_X0 + k * 2.54 for k in (3, 4, 5, 6))
place_oriented("Q1", SDA_X - 0.938, 10.5, lambda: near(pad_xy("Q1", 3), (SDA_X, 10.5)))
place_oriented("Q2", SCL_X + 0.938, 12.5, lambda: near(pad_xy("Q2", 3), (SCL_X, 12.5)))
# R2 (SDA pull-up): SDA pad on the left, on Q1's source line. R3 (SCL pull-up): SCL pad on the right.
place_oriented("R2", 41.3, 13.6, lambda: pad_xy("R2", 2)[0] < pad_xy("R2", 1)[0])
place_oriented("R3", 47.075, 16.2, lambda: pad_xy("R3", 2)[0] > pad_xy("R3", 1)[0])
# UART series resistors, host side (pin 1) up
place_oriented("R10", RX_X, 14.6, lambda: pad_xy("R10", 1)[1] < pad_xy("R10", 2)[1])
place_oriented("R11", RX_X + 2.54, 14.6, lambda: pad_xy("R11", 1)[1] < pad_xy("R11", 2)[1])


def vertical(ref, x, y, pin1_up):
    place_oriented(ref, x, y, lambda: (pad_xy(ref, 1)[1] < pad_xy(ref, 2)[1]) == pin1_up and
                   abs(pad_xy(ref, 1)[0] - x) < 0.01)


# Power chain
vertical("C1", 53.0, 4.4, pin1_up=False)          # +5V at the bottom
place_oriented("U1", 56.2, 4.4, lambda: pad_xy("U1", 5)[0] < 56.2 and pad_xy("U1", 5)[1] > 4.4)  # IN bottom-left
place_oriented("R1", 59.2, 1.5, lambda: pad_xy("R1", 1)[0] < pad_xy("R1", 2)[0])
vertical("C2", 59.6, 5.0, pin1_up=False)          # +5V_SW at the bottom, GND on top
place("U2", 63.1, 4.4, 0)                          # VIN top-left, EN bottom-left, VOUT top-right
vertical("C3", 66.5, 4.4, pin1_up=True)
vertical("C4", 68.3, 4.4, pin1_up=True)
place_oriented("R9", 71.0, 2.0, lambda: pad_xy("R9", 1)[0] < pad_xy("R9", 2)[0])
place_oriented("D1", 74.0, 2.0, lambda: pad_xy("D1", 2)[0] < pad_xy("D1", 1)[0])   # anode on the left

# ADC: rotated so pins 6-10 (AIN2, AIN3, VDD, SDA, SCL) face up and 1-5 face down; ADDR at the bottom-left
ADC_X, ADC_Y = 63.6, 11.6
place_oriented("U3", ADC_X, ADC_Y, lambda: pad_xy("U3", 1)[0] < ADC_X and pad_xy("U3", 1)[1] > ADC_Y)
R_ADC_Y = 16.6
for ref, x in (("R5", 64.1), ("R6", 65.7), ("R7", 67.3), ("R8", 68.9)):
    vertical(ref, x, R_ADC_Y, pin1_up=True)        # AIN side up

# Fiducials for pick-and-place (U3 is 0.5 mm pitch): board-only, no schematic symbol, not in the BOM
for i, (x, y) in enumerate([(1.8, 10.5), (81.6, 1.8), (36.9, 1.2)], 1):
    fid = load("Fiducial:Fiducial_1mm_Mask2mm")
    fid.SetReference(f"FID{i}")
    fid.Reference().SetLayer(pcbnew.F_Fab)
    fid.SetBoardOnly(True)
    fid.SetExcludedFromBOM(True)
    board.Add(fid)
    fid.SetPosition(P(x, y))

# --- routing --------------------------------------------------------------------------------------------------
F, B = pcbnew.F_Cu, pcbnew.B_Cu


SEGS, VIAS = [], []   # collected, then split at T-junctions and vias before they go on the board


def track(netn, layer, pts, width=TRACK):
    pts = [(round(x, 4), round(y, 4)) for x, y in pts]
    for a, b in zip(pts, pts[1:]):
        if a != b:
            SEGS.append((netn, layer, a, b, width))


def via(netn, x, y):
    VIAS.append((netn, round(x, 4), round(y, 4)))


def _on_segment(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > 1e-6 * max(1.0, abs(bx - ax) + abs(by - ay)):
        return False
    dot = (px - ax) * (bx - ax) + (py - ay) * (by - ay)
    return 1e-6 < dot < (bx - ax) ** 2 + (by - ay) ** 2 - 1e-6


def commit_copper():
    """KiCad joins copper only where a track ends, so split every segment at same-net vias and track ends."""
    for netn, layer, a, b, width in SEGS:
        anchors = {(x, y) for n, x, y in VIAS if n == netn}
        anchors |= {e for n, l, s, t, _ in SEGS if n == netn and l == layer for e in (s, t)}
        cuts = sorted((p for p in anchors if _on_segment(p, a, b)),
                      key=lambda p: (p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2)
        chain = [a, *cuts, b]
        for (x1, y1), (x2, y2) in zip(chain, chain[1:]):
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(P(x1, y1))
            t.SetEnd(P(x2, y2))
            t.SetWidth(pcbnew.FromMM(width))
            t.SetLayer(layer)
            t.SetNet(net(D.pcb_net(netn)))
            board.Add(t)
    for netn, x, y in VIAS:
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(P(x, y))
        v.SetWidth(pcbnew.FromMM(VIA_D))
        v.SetDrill(pcbnew.FromMM(VIA_DRILL))
        v.SetNet(net(D.pcb_net(netn)))
        board.Add(v)


def pad(ref, n):
    return pad_xy(ref, n)


j1 = {n: pad("J1", n) for n in range(1, 15)}
LY = D.LANE_Y

# Qwiic -> socket fan-outs (top), with vias down to the bottom-layer lanes
lane_x = {"SDA": [], "SCL": [], "+3V3": []}
for q, s in D.QWIIC.items():
    for n, sock_pin in ((1, 1), (2, 2), (3, 3), (4, 4)):
        qx, qy = pad(q, n)
        sx, sy = pad(s, sock_pin)
        dx = sx - qx
        y0 = qy + 1.3
        track(D.QWIIC_NETS[n], F, [(qx, qy), (qx, y0), (sx, y0 + abs(dx)), (sx, sy)])
        nn = D.QWIIC_NETS[n]
        if nn in LY:
            via(nn, sx, LY[nn])
            lane_x[nn].append(sx)
    for p in fps[q].Pads():   # both mounting pads to GND through a via just outside them
        if p.GetNumber() == "MP":
            x, y = round(pcbnew.ToMM(p.GetPosition().x) - OX, 3), round(pcbnew.ToMM(p.GetPosition().y) - OY, 3)
            vx = x - 1.3 if x < QC[q] else x + 1.3
            track("GND", F, [(x, y), (vx, y)], 0.4)
            via("GND", vx, y)

# Host I2C -> Q1/Q2 drains (top)
track("SDA_HOST", F, [j1[8], pad("Q1", 3)])
track("SCL_HOST", F, [j1[10], pad("Q2", 3)])
# Q1 source -> SDA lane; R2 sits on this line
q1s = pad("Q1", 2)
track("SDA", F, [q1s, (q1s[0], pad("R2", 2)[1]), (q1s[0], LY["SDA"])])
via("SDA", q1s[0], LY["SDA"])
lane_x["SDA"].append(q1s[0])
# Q2 source -> SCL lane; R3 sits on this line
q2s = pad("Q2", 2)
SCL_DROP = 47.9
track("SCL", F, [q2s, (SCL_DROP, q2s[1]), (SCL_DROP, pad("R3", 2)[1]), (SCL_DROP, LY["SCL"])])
via("SCL", SCL_DROP, LY["SCL"])
lane_x["SCL"].append(SCL_DROP)

# 3V3 spine on the bottom (y 9.55) to the FET gates and pull-ups, fed from the 3V3 track at x 53.5
SPINE_Y = 9.55
q1g, q2g = pad("Q1", 1), pad("Q2", 1)
g1v = (q1g[0] - 1.5, q1g[1])
track("+3V3", F, [q1g, g1v])
via("+3V3", *g1v)
g2v = (q2g[0], q2g[1] + 1.15)
track("+3V3", F, [q2g, g2v])
via("+3V3", *g2v)
r2p = pad("R2", 1)
r2v = (r2p[0], r2p[1] + 1.1)
track("+3V3", F, [r2p, r2v])
via("+3V3", *r2v)
track("+3V3", F, [pad("R3", 1), g2v])
SPINE_END = 53.5
track("+3V3", B, [g1v, (SPINE_END, SPINE_Y)])
for x, y in (g2v, r2v):
    track("+3V3", B, [(x, y), (x, SPINE_Y)])
via("+3V3", SPINE_END, SPINE_Y)

# UART: J1 pin 12/14 jog right one pitch, through R10/R11, into the socket
track("TX_HOST", F, [j1[12], (TX_X, 8.6), (RX_X, 8.6 + 2.54), pad("R10", 1)])
track("TX", F, [pad("R10", 2), pad("J10", 3)])
track("RX_HOST", F, [j1[14], (RX_X, 7.4), (RX_X + 2.54, 7.4 + 2.54), pad("R11", 1)])
track("RX", F, [pad("R11", 2), pad("J10", 4)])

# +5V: J1 pin 6 on the bottom, under the I2C/UART lines, up to the power chain
V5 = (52.0, 8.0)
track("+5V", B, [j1[6], (j1[6][0], V5[1]), V5], PWR_TRACK)
via("+5V", *V5)
u1 = {n: pad("U1", n) for n in range(1, 6)}
c1 = {n: pad("C1", n) for n in (1, 2)}
X5 = 54.3
track("+5V", F, [V5, (V5[0], 6.3), (X5, 6.3), (X5, u1[4][1]), u1[4]], PWR_TRACK)
track("+5V", F, [(X5, u1[5][1]), u1[5]], PWR_TRACK)
track("+5V", F, [c1[1], (c1[1][0], 6.3)], PWR_TRACK)
via("GND", c1[2][0], c1[2][1] - 1.3)
track("GND", F, [c1[2], (c1[2][0], c1[2][1] - 1.3)], PWR_TRACK)
# U1: GND to C2's GND pad, ISET to R1, OUT to C2
c2 = {n: pad("C2", n) for n in (1, 2)}
track("GND", F, [u1[2], (c2[2][0] - 0.8, u1[2][1]), c2[2]], 0.3)
via("GND", c2[2][0], c2[2][1] - 1.15)
track("GND", F, [c2[2], (c2[2][0], c2[2][1] - 1.15)], 0.3)
r1 = {n: pad("R1", n) for n in (1, 2)}
track("ISET", F, [u1[3], (u1[3][0] + 0.6, u1[3][1]), r1[1]], 0.45)   # wide: balances R1's pour-side pad heat
# R1's GND pad joins the top pour through thermal spokes (a stub to a via here made it a tombstoning risk)
track("+5V_SW", F, [u1[1], (c2[1][0] - 1.0, c2[1][1]), c2[1]], PWR_TRACK)
# C2 -> U2 VIN and EN (left of U2's GND pin, so U2's GND leaves under the package)
u2 = {n: pad("U2", n) for n in range(1, 6)}
XSW = 60.75
track("+5V_SW", F, [c2[1], (XSW, c2[1][1]), (XSW, u2[1][1]), u2[1]], 0.3)
track("+5V_SW", F, [(XSW, u2[3][1]), u2[3]], 0.3)
c3 = {n: pad("C3", n) for n in (1, 2)}
c4 = {n: pad("C4", n) for n in (1, 2)}
track("GND", F, [u2[2], (c3[2][0] - 1.1, u2[2][1]), (c3[2][0] - 0.15, c3[2][1]), c3[2]], 0.3)
track("GND", F, [c3[2], c4[2]], 0.4)
via("GND", (c3[2][0] + c4[2][0]) / 2, c3[2][1] + 0.75)
# +3V3: U2 VOUT along C3/C4, down to the 3V3 track at y 7.0
X33 = 69.6
Y33 = 7.0
track("+3V3", F, [u2[5], c3[1], c4[1], (X33, c4[1][1]), (X33, Y33)], PWR_TRACK)
track("+3V3", F, [(SPINE_END, SPINE_Y), (SPINE_END, Y33), (X33, Y33), (71.0, Y33), (71.0, LY["+3V3"])],
      PWR_TRACK)
via("+3V3", 71.0, LY["+3V3"])
lane_x["+3V3"].append(71.0)
# Power LED
r9 = {n: pad("R9", n) for n in (1, 2)}
d1 = {n: pad("D1", n) for n in (1, 2)}
track("+3V3", F, [(X33, c4[1][1]), (X33, r9[1][1]), r9[1]])
track("LED_A", F, [r9[2], d1[2]], 0.45)   # wide: balances D1's pour-side pad heat
# D1's cathode joins the top pour through thermal spokes, like R1

# ADS1115
u3 = {n: pad("U3", n) for n in range(1, 11)}
top_y = u3[8][1] - 0.75           # top edge of the upper pad row
# VDD straight up to the 3V3 track
track("+3V3", F, [u3[8], (u3[8][0], Y33)], TRACK)
# SDA: up, left, via, bottom layer down to the SDA lane
SDA_V = (60.6, 8.0)
track("SDA", F, [u3[9], (u3[9][0], SDA_V[1]), SDA_V], FINE)
via("SDA", *SDA_V)
track("SDA", B, [SDA_V, (SDA_V[0], LY["SDA"])])
lane_x["SDA"].append(SDA_V[0])
# SCL: up, left, down to the SCL lane
ADC_SCL_X = 61.4
track("SCL", F, [u3[10], (u3[10][0], 8.45), (ADC_SCL_X, 8.45), (ADC_SCL_X, LY["SCL"])], FINE)
via("SCL", ADC_SCL_X, LY["SCL"])
lane_x["SCL"].append(ADC_SCL_X)
# AIN2 / AIN3 round the right of the package into R7 / R8
r = {ref: {n: pad(ref, n) for n in (1, 2)} for ref in ("R5", "R6", "R7", "R8")}
track("AIN2", F, [u3[6], (u3[6][0], 8.45), (r["R7"][1][0], 8.45), r["R7"][1]], FINE)
track("AIN3", F, [u3[7], (u3[7][0], 8.0), (r["R8"][1][0], 8.0), r["R8"][1]], FINE)
# AIN0 / AIN1 straight down into R5 / R6
track("AIN0", F, [u3[4], r["R5"][1]], FINE)
track("AIN1", F, [u3[5], (u3[5][0], u3[5][1] + 1.0), r["R6"][1]], FINE)
# ADDR and GND to a GND via (ALERT/RDY between them is unconnected)
gx = (u3[1][0], u3[1][1] + 1.5)
track("GND", F, [u3[1], gx], FINE)
track("GND", F, [u3[3], (u3[3][0], u3[3][1] + 1.05), (u3[3][0] - 0.45, gx[1]), gx], FINE)
via("GND", gx[0] - 0.3, gx[1] + 0.8)
track("GND", F, [gx, (gx[0] - 0.3, gx[1] + 0.8)], FINE)
# R5-R8 -> analog socket A0-A3
for ref, sock_pin in (("R5", 3), ("R6", 4), ("R7", 5), ("R8", 6)):
    (rx, ry), (sx, sy) = r[ref][2], pad("J11", sock_pin)
    y0 = ry + 0.5
    track({"R5": "A0", "R6": "A1", "R7": "A2", "R8": "A3"}[ref], F,
          [(rx, ry), (rx, y0), (sx, y0 + abs(rx - sx)), (sx, sy)])

# Sockets' 3V3 pins that have no Qwiic: straight up to the 3V3 lane on the bottom
for ref in ("J10", "J11"):
    x, y = pad(ref, 2)
    track("+3V3", B, [(x, y), (x, LY["+3V3"])])
    lane_x["+3V3"].append(x)

# The lanes themselves (bottom layer)
for nn, xs in lane_x.items():
    track(nn, B, [(min(xs), LY[nn]), (max(xs), LY[nn])], PWR_TRACK if nn == "+3V3" else TRACK)

# GND stitching vias (both pours are GND)
for x, y in [(3, 3), (10, 3), (17, 3), (24, 3), (31, 3), (3, 8), (10, 8), (17, 8), (24, 8), (31, 8),
             (4.5, 12), (17.24, 12), (28.67, 12), (17.24, 21.6), (28.67, 21.6), (41.37, 21.6), (54.06, 21.6),
             (71.2, 21.6), (79, 4), (82, 4), (79, 7.5), (82, 7.5), (82.6, 12), (2, 16), (2, 21.6)]:
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
def text(s, x, y, layer=pcbnew.F_SilkS, size=1.0, rot=0, just=None, thick=0.16):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetPosition(P(x, y))
    t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(size), pcbnew.FromMM(size)))
    t.SetTextThickness(pcbnew.FromMM(thick))
    t.SetTextAngleDegrees(rot)
    if layer == pcbnew.B_SilkS:
        t.SetMirrored(True)
    if just == "left":
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
    board.Add(t)


text("BreadPort v0.1", 17.0, 4.0, size=1.5, thick=0.25)
text("I2C x4 / UART / Analog", 17.0, 6.5, size=0.9)
text("Keyboard side", 7.0, 1.4, size=1.0)
for s, (x0, names, _) in D.SOCKETS.items():
    for i, lbl in enumerate(names):
        text(lbl, x0 + i * D.P, 19.55, size=0.8)
for q, s in D.QWIIC.items():
    text(parts[s]["label"], QC[q], D.QWIIC_Y - 3.6, size=0.9)
text("ADS1115", 57.9, 12.6, size=0.8)
text("PWR", 77.2, 2.0, size=0.8)
text("BreadPort v0.1", 20.0, 12.0, pcbnew.B_SilkS)
text("Pin 1 = G26  keyboard side", D.J1_X0 + 7.6, 9.4, pcbnew.B_SilkS)
text("Power off before fitting", 66.0, 14.6, pcbnew.B_SilkS)
text("Enable 5V: Settings > ExtPort", 64.0, 16.2, pcbnew.B_SilkS)

commit_copper()
board.BuildConnectivity()
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
print("zones filled:", [z.IsFilled() for z in board.Zones()])
board.Save(str(OUT))
print(f"wrote {OUT}")
for ref in ("Q1", "Q2", "U1", "U2", "U3"):
    print(ref, {n: pad(ref, n) for n in sorted({p.GetNumber() for p in fps[ref].Pads()}, key=str)})

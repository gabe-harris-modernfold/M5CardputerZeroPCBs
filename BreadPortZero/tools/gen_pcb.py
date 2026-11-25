"""Generate BreadPortZero.kicad_pcb from design.py: outline, footprints, tracks, vias, GND pours.

Run with KiCad's own Python (it provides pcbnew):

    & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" tools\\gen_pcb.py

Layout (top view, keyboard side at the top edge):
- J1 (host header) on the bottom, centred. G22/G23 (BreadPortZero's own bus) and G2/G3 (host I2C1) drop to JP1/JP2 and
  Q1/Q2 (I2C isolation) just below it, then U4 (ESD); TX/RX drop through R10/R11 and U5 (ESD) into the UART socket.
- SPI at the left end, between Qwiic 1 and 2: U7 (74LVC125 buffer) with C10 and R21 (MISO pull-up) above it and the
  TVS diodes D5-D8 below, straight into the SPI socket. G11/G10/G9/G7 reach U7 from J1 in a band along the top edge of
  the bottom layer, then down beside it to vias under the package.
- Power chain right of J1 along the top: C1, U1 (host current limit), R1, D3 (host diode), C2, U2 (buck), L1, C3, C9,
  power LED. The FAULT LED (D2, R14) sits under U1, the buck's feedback divider (R25, R26) under U2.
- USB-C jack (J13) in the top-right corner, opening on the right edge; U6 (USB current limit), D4, C11, the CC
  pull-downs R22/R23 and R24 below it. U6's output runs back to the buck along the bottom layer.
- U3 (ADS1115) under the power chain with C4 at its VDD pin, R5-R8 and C5-C8 under it, straight into the analog socket.
  Its address jumpers JP4-JP6 are on the bottom, under its left side. Divider pads R17-R20 sit in the gaps around it.
- Power switch right of the ADC: R15 (EN pull-up) above, Q3 and R16 between the AIN3 track and the 3V3 trunk. EN,
  +5V, G7 and PWR_OFF reach it on the bottom layer, through JP9 (bottom, right of the trunk).
- Qwiic 2 and 3 above their I2C sockets, each fanning out on the top layer into its socket; Qwiic 1 at the left edge
  (above the SPI socket) and Qwiic 4 in the bottom-right corner drop straight to the lanes. The pull-ups (R2, R3, JP3)
  sit between Qwiic 2 and 3.
- SDA, SCL and 3V3 run the length of the board as bottom-layer lanes between the Qwiics and the sockets. A second 3V3
  run on the bottom (the spine, y 14.6) feeds Q1/Q2's gates, U4, U5 and JP4.
- Bottom silkscreen: the board's manual. Setup steps and a ports table at the SPI end, the host pinout at J1, jumper
  notes beside the jumpers, a power-path diagram and LED key by the power section, socket names along the edge.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).parent))
import design as D  # noqa: E402

FPLIB = D.KICAD / "share" / "kicad" / "footprints"
OUT = Path(__file__).resolve().parent.parent / "BreadPortZero.kicad_pcb"
OX, OY = 100.0, 100.0          # board origin on the KiCad page
TRACK, PWR_TRACK, FINE = 0.25, 0.5, 0.2
VIA_D, VIA_DRILL = 0.8, 0.3
PROJECT = "BreadPortZero"


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
NC_PIN_NAMES = {"U6": {"4": "~{FAULT}"},
                "J13": {"A6": "D+", "B6": "D+", "A7": "D-", "B7": "D-", "A8": "SBU1", "B8": "SBU2"}}
SOLID_GND = {("U1", "2"), ("U2", "2"),   # regulator GND pins: full pour connection, it's their main heat path
             ("J13", "SH")}             # USB-C shell legs: VBUS and the GND pads leave room for one spoke only
# USB-C GND pads: no pour connection (spokes would run into the plastic pegs' holes); a track ties each to a shell leg
NO_ZONE = {("J13", "A1"), ("J13", "A12"), ("J13", "B1"), ("J13", "B12")}


def load(fpid):
    lib, name = fpid.split(":")
    path = OUT.parent / f"{PROJECT}.pretty" if lib == PROJECT else FPLIB / f"{lib}.pretty"   # own: make_footprints.py
    return pcbnew.FootprintLoad(str(path), name)


def place(ref, x, y, rot=0.0):
    part = parts[ref]
    if ref in fps:
        board.Remove(fps.pop(ref))
    fp = load(part["fp"])
    fp.SetFPID(pcbnew.LIB_ID(*part["fp"].split(":")))
    fp.SetReference(ref)
    fp.SetValue(part["value"])
    if part["lcsc"]:
        fp.SetField("LCSC", part["lcsc"])
        fp.SetField("MPN", part["mpn"])
    for f in fp.GetFields():
        if f.GetName() in ("LCSC", "MPN"):
            f.SetVisible(False)
            f.SetLayer(pcbnew.F_Fab)
    if not part.get("bom", True):   # jumpers, test pads and divider pads: copper only
        fp.SetExcludedFromBOM(True)
        fp.SetExcludedFromPosFiles(True)
    if part.get("dnp"):
        fp.SetDNP(True)
    # references go on the fab layer: the board is too dense for them on silk, and the silk labels say what
    # each connector is for
    fp.Reference().SetLayer(pcbnew.F_Fab)   # a bottom part flips this to B.Fab below
    fp.Value().SetLayer(pcbnew.F_Fab)
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
        if (ref, num) in SOLID_GND:
            pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
        elif (ref, num) in NO_ZONE:
            pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_NONE)
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


def vertical(ref, x, y, pin1_up):
    place_oriented(ref, x, y, lambda: (pad_xy(ref, 1)[1] < pad_xy(ref, 2)[1]) == pin1_up and
                   abs(pad_xy(ref, 1)[0] - x) < 0.01)


def horizontal(ref, x, y, pin1_left=True):
    place_oriented(ref, x, y, lambda: (pad_xy(ref, 1)[0] < pad_xy(ref, 2)[0]) == pin1_left and
                   abs(pad_xy(ref, 1)[1] - y) < 0.01)


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

# Qwiic connectors, signal pads facing the sockets (+y), pin 1 (GND) on the left. Qwiic 2/3 centre on their I2C
# sockets; Qwiic 1 sits at the left edge (above the SPI socket's GND/3V3 pins); Qwiic 4 is turned round in the
# bottom-right corner, signal pads facing up to the lanes
QC = {"J2": 4.6, "J5": 79.5}
for q, s in D.QWIIC.items():
    if s:
        QC[q] = D.SOCKETS[s][0] + 1.5 * D.P
J5_Y = 20.85
for q in D.QWIIC:
    place(q, QC[q], J5_Y if q == "J5" else D.QWIIC_Y, 180 if q == "J5" else 0)

j1 = {n: pad_xy("J1", n) for n in range(1, 15)}
COL = [D.J1_X0 + k * 2.54 for k in range(7)]          # J1 columns
CH = [c + 1.27 for c in COL]                           # channels between columns
ROW_MID = (D.J1_ODD_Y + D.J1_EVEN_Y) / 2               # between J1's rows
SDA_X, SCL_X = CH[2], COL[4]                           # Q1 drain under G22's channel, Q2 drain right of G3

# Host bus select (top): JP1 G22 | G2, JP2 G3 | G23. Both open: BreadPortZero's bus is on G22/G23.
JP_Y = 8.6
horizontal("JP1", SDA_X + 0.65, JP_Y)                  # pad 1 (SDA_HOST) under G22's channel, pad 2 under G2
horizontal("JP2", COL[4] + 0.65, JP_Y)                 # pad 1 (I2C1_SCL) under G3, pad 2 (SCL_HOST)
SCL_X = pad_xy("JP2", 2)[0]
# I2C isolation FETs, drain up under their jumper, gate bottom-left, source bottom-right
Q_Y = 12.0
for q, x in (("Q1", SDA_X), ("Q2", SCL_X)):
    place_oriented(q, x, Q_Y, lambda q=q, x=x: pad_xy(q, 3)[1] < Q_Y and abs(pad_xy(q, 3)[0] - x) < 0.01
                   and pad_xy(q, 1)[0] < x)
# ESD array between the SDA and SCL lines: pins 1-2-3 along the bottom, 6-5-4 along the top
U4_X, U4_Y = pad_xy("J10", 1)[0], 16.1                  # GND pin straight above J10 pin 1
place_oriented("U4", U4_X, U4_Y, lambda: pad_xy("U4", 1)[0] < U4_X and pad_xy("U4", 1)[1] > U4_Y
               and pad_xy("U4", 6)[1] < U4_Y)
# UART series resistors (host side, pin 1, up) just below J1's TX/RX pins, then U5 (ESD) on the socket side:
# flow-through, TX 6 -> 1 down its left column, RX 4 -> 3 down its right, 3V3 (5) top middle, GND (2) bottom middle
U5_X = COL[6]                                          # centred under J1's RX pin
TX_X, RX_X = U5_X - 1.12, U5_X + 1.12                  # courtyards clear of Q2's
vertical("R10", TX_X, 9.0, pin1_up=True)
vertical("R11", RX_X, 9.0, pin1_up=True)
U5_Y = 12.7
place_oriented("U5", U5_X, U5_Y, lambda: pad_xy("U5", 6)[0] < U5_X and pad_xy("U5", 6)[1] < U5_Y
               and pad_xy("U5", 1)[0] < U5_X and pad_xy("U5", 1)[1] > U5_Y)

# Pull-ups between Qwiic 2 and 3: JP3 (cut to remove, upright) above, R2/R3 below
PU_X = (QC["J3"] + QC["J4"]) / 2
place_oriented("JP3", PU_X, 8.0, lambda: pad_xy("JP3", 1)[1] < pad_xy("JP3", 3)[1]
               and abs(pad_xy("JP3", 1)[0] - PU_X) < 0.01)                # PU_SDA top, 3V3 middle, PU_SCL bottom
vertical("R2", PU_X - 0.825, 13.0, pin1_up=True)
vertical("R3", PU_X + 0.825, 13.0, pin1_up=True)
horizontal("JP7", COL[0] - 2.48, D.J1_ODD_Y)            # pad 1 ALERT, pad 2 ALERT_HOST next to pin 1

# SPI block between Qwiic 1 and Qwiic 2. U7 (74LVC125) has its pin rows top (VCC 4OE 4A 4Y 3OE 3A 3Y) and bottom
# (1OE 1A 1Y 2OE 2A 2Y GND): host lines come in on the bottom layer to vias under it, SCK/MOSI leave down from the
# bottom row, CS and MISO leave up from the top row and come down its right side. C10 at VCC, R21 (MISO pull-up)
# above. One TVS per socket line.
U7_X, U7_Y = 12.0, 9.5
place_oriented("U7", U7_X, U7_Y, lambda: pad_xy("U7", 14)[0] < U7_X and pad_xy("U7", 14)[1] < U7_Y
               and pad_xy("U7", 1)[0] < U7_X and pad_xy("U7", 1)[1] > U7_Y)
vertical("C10", 8.0, 6.6, pin1_up=True)                 # +3V3 top, GND bottom
horizontal("R21", 11.4, 4.2)                            # pad 1 +3V3 left, pad 2 MISO right
SCK_X, MOSI_X = pad_xy("U7", 3)[0], pad_xy("U7", 6)[0]  # 1Y, 2Y
CS_X, MISO_X = 15.6, 18.0                               # down the buffer's right side
horizontal("D5", SCK_X - 0.7, 14.6, pin1_left=False)    # cathode on the line, anode (GND) left
horizontal("D6", MOSI_X + 0.7, 15.2)                    # cathode on the line, anode (GND) right
horizontal("D7", CS_X + 0.7, 13.5)                      # cathode on the line, anode right (a row up: courtyards)
horizontal("D8", MISO_X + 0.7, 15.2)
# Host-side pull-ups above J1's odd row, one pad over G22 (pin 5) and G23 (pin 3); their 3.3 V comes along the top
# edge on the bottom layer through JP8
PUH_Y = 1.5
horizontal("R12", COL[2] + 0.775, PUH_Y)               # pad 1 over pin 5
horizontal("R13", COL[1] - 0.775, PUH_Y, pin1_left=False)   # pad 1 over pin 3
horizontal("JP8", 53.5, PUH_Y + 0.4)    # bottom: pad 1 PU_HOST, pad 2 +3V3; courtyard 0.6 mm from the edge

# Power chain along the top: host 5 V -> C1 -> U1 (current limit) -> D3 -> +5V_SW (C2) -> U2 (buck) -> L1 -> +3V3
# (C3) -> the trunk (x 72.3). The buck's feedback divider (R25/R26) and output cap sit under it.
vertical("C1", 53.2, 4.0, pin1_up=True)                 # +5V on top
place_oriented("U1", 56.4, 4.0, lambda: pad_xy("U1", 1)[0] < 56.4 and pad_xy("U1", 1)[1] < 4.0)  # IN top-left
vertical("R1", 59.75, 4.4, pin1_up=True)               # ILIM on top
vertical("D3", 62.0, 3.7, pin1_up=False)               # anode (U1_OUT) up, cathode (+5V_SW) down
vertical("C2", 64.3, 3.35, pin1_up=True)                # +5V_SW on top
U2_X, U2_Y = 67.4, 3.35
place_oriented("U2", U2_X, U2_Y, lambda: pad_xy("U2", 4)[0] < U2_X and pad_xy("U2", 4)[1] < U2_Y)  # VIN top-left
horizontal("L1", 71.5, 2.95)                            # pad 1 (SW) left, pad 2 (+3V3) on the trunk
horizontal("C3", 71.15, 5.55, pin1_left=False)          # buck output: +3V3 right (trunk), GND left
vertical("R25", 65.35, 6.4, pin1_up=True)               # +3V3 top, FB bottom
vertical("R26", 67.15, 6.4, pin1_up=False)              # FB bottom, GND top
# right of the trunk, beside the jack: bulk cap, PWR LED
vertical("C9", 74.15, 3.0, pin1_up=True)
vertical("R9", 73.8, 7.2, pin1_up=True)                 # +3V3 top, LED_A bottom
vertical("D1", 75.4, 7.2, pin1_up=True)                 # cathode top, anode bottom
# USB-C jack in the top-right corner, opening to the right edge (front face 1 mm past it); Qwiic 4 below it
J13_X, J13_Y = D.BOARD_W + 1.0 - 3.675, 5.9
place_oriented("J13", J13_X, J13_Y, lambda: pad_xy("J13", "A1")[0] < J13_X and pad_xy("J13", "A1")[1] > J13_Y)
# USB input block between the jack and Qwiic 4: U6 (VBUS -> +5V_SW, IN top-right), D4 (clamp) and C11 by its input,
# R24 (limit) under it, CC pull-downs R22/R23 and JP9 to the right
U6_X, U6_Y = 75.2, 13.6
place_oriented("U6", U6_X, U6_Y, lambda: pad_xy("U6", 1)[0] > U6_X and pad_xy("U6", 1)[1] < U6_Y)
vertical("D4", 77.9, 12.6, pin1_up=True)                # cathode (VBUS) up
vertical("C11", 79.2, 12.3, pin1_up=True)               # VBUS up
vertical("R24", U6_X, 16.7, pin1_up=True)               # ILIM up
vertical("R22", 80.5, 13.5, pin1_up=True)               # CC1 up
vertical("R23", 82.1, 13.5, pin1_up=True)               # CC2 up
horizontal("JP9", 74.84, 8.6)                           # bottom: pad 1 (G7) left, pad 2 (PWR_OFF) right
# FAULT LED under U1: R14 from the 5 V track, D2 with its cathode under U1's FAULT pin
STRIP_Y = 6.75
horizontal("R14", 53.6, STRIP_Y)                       # pad 1 clear of the wide 5 V track (even pad heating)
horizontal("D2", 57.15, STRIP_Y, pin1_left=False)
place("TP1", 52.45, 11.6)
place("TP2", 78.3, 15.9)

# ADC: pins 6-10 (AIN2, AIN3, VDD, SDA, SCL) face up, 1-5 (ADDR, ALERT, GND, AIN0, AIN1) face down, ADDR at the
# bottom-left. x puts AIN0 straight above the A0 socket pin.
A_X = [pad_xy("J11", n)[0] for n in (3, 4, 5, 6)]       # A0-A3 socket pins
ADC_X, ADC_Y = A_X[0] - 0.5, 11.6
place_oriented("U3", ADC_X, ADC_Y, lambda: pad_xy("U3", 1)[0] < ADC_X and pad_xy("U3", 1)[1] > ADC_Y)
horizontal("C4", ADC_X + 1.575, 7.2)                    # VDD decoupling, pad 1 next to the VDD pin
# input resistors straight above the socket pins, filter capacitors (0402) beside them
RC_Y = 17.55
for i, x in enumerate(A_X):
    vertical(f"R{5 + i}", x, RC_Y, pin1_up=True)
    vertical(f"C{5 + i}", x + 1.27, RC_Y - 0.775 + 0.48, pin1_up=True)
# address jumpers on the bottom, under the ADC's left side
JP4_X, JPA_X = ADC_X - 1.65, ADC_X - 4.55
place_oriented("JP4", JP4_X, 11.2, lambda: pad_xy("JP4", 3)[1] > pad_xy("JP4", 1)[1]
               and abs(pad_xy("JP4", 3)[0] - JP4_X) < 0.01)          # GND top, VDD bottom (above the 3V3 spine)
place_oriented("JP5", JPA_X, 9.6, lambda: pad_xy("JP5", 2)[1] < pad_xy("JP5", 1)[1]
               and abs(pad_xy("JP5", 2)[0] - JPA_X) < 0.01)          # SCL top, ADDR bottom
place_oriented("JP6", JPA_X, 13.05, lambda: pad_xy("JP6", 1)[1] < pad_xy("JP6", 2)[1]
               and abs(pad_xy("JP6", 1)[0] - JPA_X) < 0.01)          # ADDR top, SDA bottom
# Divider pads (unfitted), each next to its AINx node: R17 left of R5, R18 above C6, R19/R20 between the AIN2 and
# AIN3 tracks
horizontal("R17", A_X[0] - 2.45, RC_Y - 0.65, pin1_left=False)       # pad 1 (AIN0) towards R5
vertical("R18", A_X[1] + 1.27, 14.4, pin1_up=False)                  # pad 1 (AIN1) down to C6
DIV_X = (A_X[2] + A_X[3]) / 2
vertical("R19", DIV_X, 9.9, pin1_up=True)                            # pad 1 (AIN2) up, GND down
vertical("R20", DIV_X, 13.95, pin1_up=False)                         # pad 1 (AIN3) down, GND up

# Power switch between the AIN3 track and the 3V3 trunk: R15 (EN pull-up, 0402) above Q3 (drain up), R16 below
Q3_X = (A_X[3] + 72.3) / 2
horizontal("R15", 70.9, 7.75, pin1_left=False)                       # pad 1 +5V right, pad 2 EN over Q3's drain
place_oriented("Q3", Q3_X, 10.31, lambda: pad_xy("Q3", 3)[1] < 10.31 and pad_xy("Q3", 1)[0] < Q3_X)
vertical("R16", pad_xy("Q3", 1)[0] + 0.04, 14.45, pin1_up=True)      # pad 1 (PWR_OFF) under Q3's gate

# Fiducials for pick-and-place (U3 is 0.5 mm pitch): board-only, no schematic symbol, not in the BOM
for i, (x, y) in enumerate([(1.8, 1.8), (71.9, 21.9), (28.9, 1.85)], 1):
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


LY = D.LANE_Y

# Qwiic 2/3 -> socket fan-outs (top), with vias down to the bottom-layer lanes on the socket pins
lane_x = {"SDA": [], "SCL": [], "+3V3": []}
for q, s in D.QWIIC.items():
    if not s:
        continue
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
# Mounting pads to GND through a via beside them (outside), or just above where that is crowded
MP_VIA_ABOVE = {("J2", 0), ("J3", 0), ("J3", 1), ("J5", 0), ("J5", 1)}       # (connector, 0 = left pad / 1 = right pad)
MP_VIA_BELOW = {("J4", 0)}                                          # clear of the ALERT run and the JP3 3V3 hop
for q in D.QWIIC:
    for p in fps[q].Pads():
        if p.GetNumber() == "MP":
            x, y = round(pcbnew.ToMM(p.GetPosition().x) - OX, 3), round(pcbnew.ToMM(p.GetPosition().y) - OY, 3)
            side = 0 if x < QC[q] else 1
            if (q, side) in MP_VIA_ABOVE:
                v = (x, y - 1.55)
            elif (q, side) in MP_VIA_BELOW:
                v = (x, y + 1.55)
            else:
                v = (x - 1.3 if side == 0 else x + 1.3, y)
            track("GND", F, [(x, y), v], 0.4)
            via("GND", *v)
# Qwiic 1 (no socket): each signal pad straight down to its lane via; GND to a via down-left
q1p = {n: pad("J2", n) for n in (1, 2, 3, 4)}
for n, nn in ((2, "+3V3"), (3, "SDA"), (4, "SCL")):
    track(nn, F, [q1p[n], (q1p[n][0], LY[nn])])
    via(nn, q1p[n][0], LY[nn])
    lane_x[nn].append(q1p[n][0])
gq1 = (q1p[1][0] - 0.9, 15.0)
track("GND", F, [q1p[1], (q1p[1][0], gq1[1] - 0.9), gq1], 0.4)
via("GND", *gq1)
# Qwiic 4 (turned round, signal pads facing up): SDA and SCL straight up to their lanes, 3V3 to a via just above its
# pad (the 3V3 lane steps up to it on the bottom), GND to a via above its pad
q4p = {n: pad("J5", n) for n in (1, 2, 3, 4)}
for n, nn in ((3, "SDA"), (4, "SCL")):
    track(nn, F, [q4p[n], (q4p[n][0], LY[nn])])
    via(nn, q4p[n][0], LY[nn])
    lane_x[nn].append(q4p[n][0])
q4v = (q4p[2][0], 17.95)
track("+3V3", F, [q4p[2], q4v])
via("+3V3", *q4v)
track("+3V3", B, [q4v, (q4v[0], LY["+3V3"])])
lane_x["+3V3"].append(q4v[0])
gq4 = (q4p[1][0], 17.6)
track("GND", F, [q4p[1], gq4], 0.4)
via("GND", *gq4)

# --- host side of the bus ---
# G22 (SDA_HOST): pin 5 through the channel between pins 6 and 8, JP1 pad 1, Q1 drain (top)
track("SDA_HOST", F, [j1[5], (CH[2], ROW_MID), pad("JP1", 1), pad("Q1", 3)])
# G2 (I2C1_SDA) to JP1 pad 2; G3 (I2C1_SCL) to JP2 pad 1 (top)
track("I2C1_SDA", F, [j1[8], pad("JP1", 2)])
track("I2C1_SCL", F, [j1[10], pad("JP2", 1)])
# G23 (SCL_HOST): pin 3 on the bottom through the channel between pins 4 and 6, under JP1/JP2, up to Q2's drain
G23_Y = 9.0
g23v = (SCL_X + 1.06, G23_Y)
track("SCL_HOST", B, [j1[3], (CH[1], ROW_MID), (CH[1], G23_Y), g23v])
via("SCL_HOST", *g23v)
track("SCL_HOST", F, [pad("JP2", 2), pad("Q2", 3)])
track("SCL_HOST", F, [g23v, (g23v[0] - 0.5, G23_Y + 0.5), (SCL_X, G23_Y + 0.5)])   # square T, no acid trap
# Q1 source -> SDA line -> SDA lane; Q2 source -> SCL line -> SCL lane; U4 taps both lines
q1s, q2s = pad("Q1", 2), pad("Q2", 2)
track("SDA", F, [q1s, (q1s[0], LY["SDA"])])
via("SDA", q1s[0], LY["SDA"])
lane_x["SDA"].append(q1s[0])
track("SCL", F, [q2s, (q2s[0], LY["SCL"])])
via("SCL", q2s[0], LY["SCL"])
lane_x["SCL"].append(q2s[0])
u4 = {n: pad("U4", n) for n in range(1, 7)}
track("SDA", F, [(q1s[0], u4[6][1]), u4[6]])
track("SDA", F, [(q1s[0], u4[1][1]), u4[1]])
track("SCL", F, [(q2s[0], u4[4][1]), u4[4]])
track("SCL", F, [(q2s[0], u4[3][1]), u4[3]])
track("GND", F, [u4[2], pad("J10", 1)], 0.4)          # U4's ground straight to the UART socket's GND pin
# Gates and U4's VBUS on the 3V3 spine (bottom, y 14.6), fed from the 3V3 trunk at x 72.3
SPINE_Y, TRUNK_X = 14.6, 72.3
q1g, q2g = pad("Q1", 1), pad("Q2", 1)
track("+3V3", F, [q1g, (q1g[0], SPINE_Y)])
via("+3V3", q1g[0], SPINE_Y)
g2v = (q2g[0] - 1.14, q2g[1] + 0.51)
track("+3V3", F, [q2g, (q2g[0], g2v[1]), g2v])
track("+3V3", F, [(q2g[0], g2v[1]), (q2g[0], g2v[1] + 0.3), (u4[5][0], g2v[1] + 0.65), u4[5]])
via("+3V3", *g2v)
track("+3V3", B, [g2v, (g2v[0], SPINE_Y)])
track("+3V3", B, [(q1g[0], SPINE_Y), (TRUNK_X, SPINE_Y)])
via("+3V3", TRUNK_X, SPINE_Y)

# UART: J1 pin 12/14 jog to R10/R11, then through U5 (flow-through, its two pins per line joined under the package)
# and down into the socket
u5 = {n: pad("U5", n) for n in range(1, 7)}
UART_JOG_Y = 16.2
for host, sig, r, top, bot, sock in (("TX_HOST", "TX", "R10", 6, 1, 3), ("RX_HOST", "RX", "R11", 4, 3, 4)):
    pin = j1[12] if r == "R10" else j1[14]
    rx = pad(r, 1)[0]
    track(host, F, [pin, (rx, pin[1] + 1.27), pad(r, 1)])
    sx, sy = pad("J10", sock)
    bx = u5[bot][0]
    track(sig, F, [pad(r, 2), u5[top], u5[bot], (bx, UART_JOG_Y), (sx, UART_JOG_Y + abs(sx - bx)), (sx, sy)])
# U5's 3V3 pin: up to a via, then on the bottom down to the spine. Its GND pin: down to a via between the spine and
# the ALERT run
u5v = (u5[5][0], u5[5][1] - 1.26)
track("+3V3", F, [u5[5], u5v])
via("+3V3", *u5v)
track("+3V3", B, [u5v, (u5v[0], SPINE_Y)])
u5g = (u5[2][0], 15.4)
track("GND", F, [u5[2], u5g], 0.4)
via("GND", *u5g)

# Host-side pull-ups: R12/R13 joined along the top edge on the top layer (the bottom layer's top edge carries the SPI
# host lines), then a via down to JP8 (bottom) and on to +3V3 along the very edge of the bottom layer
r12, r13 = {n: pad("R12", n) for n in (1, 2)}, {n: pad("R13", n) for n in (1, 2)}
track("SDA_HOST", F, [j1[5], r12[1]])
track("SCL_HOST", F, [j1[3], r13[1]])
PUL_Y = 0.65
puv = (51.3, 1.25)
track("PU_HOST", F, [r13[2], (r13[2][0], PUL_Y), (r12[2][0], PUL_Y), r12[2]])
track("PU_HOST", F, [(r12[2][0], PUL_Y), (puv[0], PUL_Y), puv])
via("PU_HOST", *puv)
jp8 = {n: pad("JP8", n) for n in (1, 2)}
track("PU_HOST", B, [puv, jp8[1]])
TOP3_Y = 0.75
TOPV = (TRUNK_X, TOP3_Y)
track("+3V3", B, [jp8[2], (jp8[2][0], TOP3_Y), TOPV])
via("+3V3", *TOPV)

# --- SPI ---
# Host side, bottom layer: each J1 pin up into a band along the top edge, left to beside U7, down, and in to a via
# under U7. Band order (top to bottom) G7, MISO, MOSI, SCK; each turns down further right than the one above it, so
# nothing crosses.
u7 = {n: pad("U7", n) for n in range(1, 15)}
cx, cy = U7_X, U7_Y
uv = {"G7a": (cx - 1.3, cy - 1.375), "MH": (cx, cy - 0.55), "G1": (cx + 0.9, cy - 1.375), "G7b": (cx + 1.9, cy - 1.2),
      "SH": (cx - 1.3, cy + 1.375), "G2": (cx, cy + 0.6), "MOH": (cx + 0.9, cy + 1.375)}
UV_NET = {"G7a": "G7_HOST", "MH": "MISO_HOST", "G1": "GND", "G7b": "G7_HOST", "SH": "SCK_HOST", "G2": "GND",
          "MOH": "MOSI_HOST"}
for k, (x, y) in uv.items():
    via(UV_NET[k], x, y)
# under-package stubs (top layer) from each inner pad end to its via
for pin, k in ((13, "G7a"), (11, "MH"), (10, "G1"), (9, "G7b"), (2, "SH"), (4, "G2"), (5, "MOH")):
    px_, py_ = u7[pin]
    inner = (px_, py_ + (0.7375 if py_ < cy else -0.7375))
    track(UV_NET[k], F, [u7[pin], inner, uv[k]], FINE)
SPI_HOST = [(7, "SCK_HOST", 2.075, 18.4, cy + 2.3, uv["SH"]), (9, "MOSI_HOST", 1.625, 17.8, cy + 1.375, uv["MOH"]),
            (11, "MISO_HOST", 1.175, 17.2, cy - 0.3, uv["MH"]), (13, "G7_HOST", 0.725, 16.6, cy - 2.3, uv["G7a"])]
for pin, nn, band_y, down_x, in_y, v in SPI_HOST:
    track(nn, B, [j1[pin], (j1[pin][0], band_y), (down_x, band_y), (down_x, in_y), (v[0], in_y), v])
track("G7_HOST", B, [uv["G7b"], (uv["G7b"][0], cy - 2.3)])
# U7's supply: C10 at VCC, fed (with R21) from a via on a bottom-layer 3.3 V run from the lanes' left end
c10 = {n: pad("C10", n) for n in (1, 2)}
r21 = {n: pad("R21", n) for n in (1, 2)}
v33 = (c10[1][0], 4.4)
track("+3V3", F, [u7[14], (u7[14][0], c10[1][1]), c10[1], v33, (r21[1][0], v33[1]), r21[1]])
via("+3V3", *v33)
track("+3V3", B, [(q1p[2][0], LY["+3V3"]), (3.3, LY["+3V3"] - 0.8), (3.3, v33[1]), v33])
gc10 = (c10[2][0], c10[2][1] + 0.95)
track("GND", F, [c10[2], gc10], 0.4)
via("GND", *gc10)
# Socket side, top layer: SCK (1Y) and MOSI (2Y) down from the bottom row; CS (3Y) and MISO (4A, via R21) up from the
# top row and down U7's right side; each jogs at 45 degrees onto its socket pin. A TVS on each, anode to a GND via.
jsk = {n: pad("J6", n) for n in range(1, 7)}
def spi_line(nn, pts, sock_pin, jog_y):
    x_ = pts[-1][0]
    sx_, sy_ = jsk[sock_pin]
    track(nn, F, pts + [(x_, jog_y), (sx_, jog_y + abs(x_ - sx_)), (sx_, sy_)])
spi_line("SCK", [u7[3]], 3, 16.6)
spi_line("MOSI", [u7[6]], 4, 16.6)
track("CS", F, [u7[8], (u7[8][0], 5.3), (CS_X, 5.3)])
spi_line("CS", [(CS_X, 5.3)], 5, 16.9)
track("MISO", F, [u7[12], (u7[12][0], 5.3), (r21[2][0], r21[2][1] + 0.3), r21[2]])
track("MISO", F, [r21[2], (MISO_X, r21[2][1])])
spi_line("MISO", [(MISO_X, r21[2][1])], 6, 17.3)
# GND: U7's 1OE and GND pins to vias beside its bottom row; TVS anodes to vias
g1oe, gu7 = (9.4, 14.0), (14.3, 14.0)
track("GND", F, [u7[1], g1oe], 0.3)
track("GND", F, [u7[7], gu7], 0.3)
via("GND", *g1oe)
via("GND", *gu7)
for d, gv in (("D5", g1oe), ("D6", gu7), ("D7", (CS_X + 1.4, 14.55)), ("D8", (MISO_X + 1.4, 16.2))):
    a = pad(d, 2)
    track("GND", F, [a, gv], 0.3)
    if gv not in (g1oe, gu7):
        via("GND", *gv)

# --- pull-ups (between Qwiic 2 and 3) ---
# JP3 is upright: PU_SDA leaves its top pad to the left and down to R2; PU_SCL leaves its bottom pad down to R3; its
# middle (3V3) pad hops right to a via, down on the bottom, back up just above the lanes and down to the 3V3 lane
jp3 = {n: pad("JP3", n) for n in (1, 2, 3)}
r2p, r3p = {n: pad("R2", n) for n in (1, 2)}, {n: pad("R3", n) for n in (1, 2)}
track("PU_SDA", F, [jp3[1], (PU_X - 1.25, jp3[1][1]), (PU_X - 1.25, r2p[1][1] - 0.8), r2p[1]])
track("PU_SCL", F, [jp3[3], (jp3[3][0], jp3[3][1] + 0.7), (r3p[1][0], jp3[3][1] + 1.525), r3p[1]])
pu3a, pu3b = (PU_X + 1.3, jp3[2][1]), (PU_X + 1.3, 16.25)
track("+3V3", F, [jp3[2], pu3a])
via("+3V3", *pu3a)
track("+3V3", B, [pu3a, pu3b])
via("+3V3", *pu3b)
pu3c = (pu3b[0] - 0.5, LY["+3V3"])
track("+3V3", F, [pu3b, (pu3b[0], LY["+3V3"] - 0.9), (pu3c[0], LY["+3V3"] - 0.4), pu3c])
via("+3V3", *pu3c)
lane_x["+3V3"].append(pu3c[0])
track("SDA", F, [r2p[2], (r2p[2][0], LY["SDA"])])
via("SDA", r2p[2][0], LY["SDA"])
lane_x["SDA"].append(r2p[2][0])
scl3 = (r3p[2][0] - 0.75, LY["SCL"])
track("SCL", F, [r3p[2], (r3p[2][0], r3p[2][1] + 0.6), (scl3[0], r3p[2][1] + 1.2), scl3])
via("SCL", *scl3)
lane_x["SCL"].append(scl3[0])

# --- power ---
# +5V: J1 pin 6 down on the bottom, under the bus lines, up at x 52 to C1/U1 IN, R14 and TP1
V5 = (52.0, 7.7)
track("+5V", B, [j1[6], (j1[6][0], V5[1]), V5], PWR_TRACK)
via("+5V", *V5)
u1 = {n: pad("U1", n) for n in range(1, 7)}
c1 = {n: pad("C1", n) for n in (1, 2)}
track("+5V", F, [V5, (V5[0], c1[1][1]), c1[1], u1[1]], PWR_TRACK)
tp1 = pad("TP1", 1)
track("+5V", F, [V5, (V5[0], tp1[1]), tp1], 0.3)
# FAULT LED: 5 V track -> R14 -> D2 anode; D2 cathode up to U1's FAULT pin
r14 = {n: pad("R14", n) for n in (1, 2)}
d2 = {n: pad("D2", n) for n in (1, 2)}
track("+5V", F, [(V5[0], r14[1][1]), r14[1]])
track("FLT_A", F, [r14[2], d2[2]])
track("FAULT", F, [d2[1], (d2[1][0], u1[4][1] + 0.95), (u1[4][0], u1[4][1] + 0.55), u1[4]])
# Bottom-layer runs under the power chain (top to bottom): +3V3 to JP8 (y 0.75), +5V_SW from U6 back to the buck
# (y 1.75), G7 to JP9 (y 4.9 / 4.4), EN (y 6.3), +5V to R15 (y 8.1)
SWB_Y, G7_Y, EN_Y, P5_Y = 1.75, 4.4, 6.3, 8.1
# EN: straight down from U1 to a via, then on the bottom to U2, R15/Q3 and U6
env = (u1[3][0], u1[3][1] + 0.92)
track("EN", F, [u1[3], env])
via("EN", *env)
u6 = {n: pad("U6", n) for n in range(1, 7)}
en6 = (73.2, 11.6)                                      # U6's EN via
track("EN", B, [env, (env[0], EN_Y), (en6[0], EN_Y), en6])
via("EN", *en6)
track("EN", F, [en6, u6[3]])
# +5V for R15: on from V5 along the bottom to a via between Q3's drain and the trunk
r15 = {n: pad("R15", n) for n in (1, 2)}
p5v = (71.42, 8.8)
track("+5V", B, [V5, (V5[0] + 0.3, P5_Y), (p5v[0], P5_Y), p5v])
via("+5V", *p5v)
# G7: from J1's pin on the bottom, down between its rows, along under the power chain and down to JP9 (right of the
# trunk). PWR_OFF: JP9 to a via, down and back under the trunk to Q3's gate
jp9 = {n: pad("JP9", n) for n in (1, 2)}
track("G7_HOST", B, [j1[13], (j1[13][0], ROW_MID), (56.0, ROW_MID), (56.5, G7_Y), (jp9[1][0], G7_Y), jp9[1]])
q3 = {n: pad("Q3", n) for n in (1, 2, 3)}
PO_Y = 12.6
pog = (q3[1][0], PO_Y)
track("PWR_OFF", B, [jp9[2], (jp9[2][0], jp9[2][1] + 1.0), (74.4, jp9[2][1] + 2.1), (74.4, PO_Y), pog])
via("PWR_OFF", *pog)
# The G7, EN and +5V runs box in a pocket of the bottom pour (it holds C4's GND via): tie it to the top pour here
via("GND", 51.1, 5.75)
# C1 and U1 ground to one via between them; U1's GND pad also has a full pour connection
gv1 = (54.15, 4.15)
track("GND", F, [c1[2], (c1[2][0] + 0.4, gv1[1] + 0.55), gv1], 0.4)
track("GND", F, [u1[2], gv1], 0.4)
via("GND", *gv1)
# ILIM to R1. R1's GND pad sits in a pocket of the top pour boxed in by U1_OUT, +5V_SW and the ADC's rail: a via ties
# it down (between the G7 and EN runs on the bottom)
r1 = {n: pad("R1", n) for n in (1, 2)}
track("ILIM", F, [u1[5], (r1[1][0], u1[5][1])], 0.45)
gr1 = (60.75, 5.35)
track("GND", F, [r1[2], (r1[2][0], gr1[1]), gr1], 0.3)
via("GND", *gr1)
# Host path: U1 OUT along the top edge to D3's anode; D3's cathode round to C2 (+5V_SW) and the buck's VIN
d3 = {n: pad("D3", n) for n in (1, 2)}
c2 = {n: pad("C2", n) for n in (1, 2)}
u2 = {n: pad("U2", n) for n in range(1, 6)}
OUT_Y = 2.0
track("U1_OUT", F, [u1[6], (58.1, u1[6][1]), (58.1, OUT_Y), (d3[2][0], OUT_Y), d3[2]], PWR_TRACK)
SWX = 63.2
swv = (SWX, SWB_Y)                                      # +5V_SW from U6 comes up here
track("+5V_SW", F, [d3[1], (SWX, d3[1][1]), (SWX, c2[1][1]), c2[1], u2[4]], 0.3)
track("+5V_SW", F, [(SWX, c2[1][1]), swv], 0.5)
via("+5V_SW", *swv)
SWV2 = (76.6, 15.85)                                    # +5V_SW from U6's OUT pin
track("+5V_SW", B, [swv, (SWV2[0], SWB_Y), SWV2], 0.5)
via("+5V_SW", *SWV2)
gc2 = (c2[2][0], 5.35)
track("GND", F, [c2[2], gc2], 0.4)
via("GND", *gc2)
# Buck: SW -> L1 -> +3V3 (C3 and the trunk); U2's GND to a via, its EN to a via on the EN run
l1 = {n: pad("L1", n) for n in (1, 2)}
c3 = {n: pad("C3", n) for n in (1, 2)}
track("SW", F, [u2[3], (l1[1][0], u2[3][1])], 0.6)
gu2 = (69.55, u2[2][1])                                # ring clear of U2's GND pad opening
track("GND", F, [u2[2], gu2], 0.4)
via("GND", *gu2)
track("GND", F, [c3[2], (69.6, c3[2][1] - 0.6), (69.6, gu2[1] + 0.4), gu2], 0.3)
enu2 = (68.9, 5.4)
track("EN", F, [u2[1], enu2])
via("EN", *enu2)
track("EN", B, [enu2, (enu2[0], EN_Y)])
# Feedback divider under U2: FB down to R25 (to +3V3) and R26 (to GND). R25's 3V3 comes from C4 (the ADC's rail).
r25 = {n: pad("R25", n) for n in (1, 2)}
r26 = {n: pad("R26", n) for n in (1, 2)}
track("FB", F, [u2[5], (u2[5][0], r25[2][1]), r25[2]], FINE)
track("FB", F, [(u2[5][0], r26[1][1]), r26[1]], 0.35)
gr26 = (67.6, 5.15)
track("GND", F, [r26[2], gr26])
via("GND", *gr26)
# 3V3: L1 -> C3 -> trunk (x 72.3) from the top-edge via down to the 3V3 lane
track("+3V3", F, [TOPV, (TRUNK_X, LY["+3V3"])], PWR_TRACK)
track("+3V3", F, [l1[2], (TRUNK_X, l1[2][1])], PWR_TRACK)
track("+3V3", F, [c3[1], (TRUNK_X, c3[1][1])], PWR_TRACK)
via("+3V3", TRUNK_X, LY["+3V3"])
lane_x["+3V3"].append(TRUNK_X)
c4 = {n: pad("C4", n) for n in (1, 2)}
# bulk cap and PWR LED right of the trunk
c9 = {n: pad("C9", n) for n in (1, 2)}
track("+3V3", F, [c9[1], (TRUNK_X, c9[1][1])], 0.4)
gc9 = (75.6, 3.4)
track("GND", F, [c9[2], (c9[2][0] + 0.8, c9[2][1]), gc9], 0.4)
via("GND", *gc9)
r9 = {n: pad("R9", n) for n in (1, 2)}
d1 = {n: pad("D1", n) for n in (1, 2)}
track("+3V3", F, [(TRUNK_X, r9[1][1]), r9[1]])
track("LED_A", F, [r9[2], d1[2]], 0.45)   # wide: balances D1's pour-side pad heat
gd1 = (d1[1][0], 5.5)
track("GND", F, [d1[1], gd1], 0.4)
via("GND", *gd1)

# --- USB-C input ---
# VBUS: both pad pairs to a track down the jack's left side, into U6's IN, D4's cathode and C11
J13p = {n: pad("J13", n) for n in ("A4", "B4", "A9", "B9", "A5", "B5", "A1", "B1", "A12", "B12")}
VBX = 76.5
vb_top = J13p["A9"] if J13p["A9"][1] < J13p["A4"][1] else J13p["A4"]
vb_bot = J13p["A4"] if vb_top is J13p["A9"] else J13p["A9"]
d4 = {n: pad("D4", n) for n in (1, 2)}
c11 = {n: pad("C11", n) for n in (1, 2)}
VB_Y = 11.75
track("VBUS", F, [vb_top, (VBX, vb_top[1]), (VBX, VB_Y), (c11[1][0], VB_Y), c11[1]], 0.35)
track("VBUS", F, [vb_bot, (VBX, vb_bot[1])], 0.35)
track("VBUS", F, [(VBX, VB_Y), u6[1]], 0.35)
track("VBUS", F, [(d4[1][0], VB_Y), d4[1]], 0.35)
# CC1/CC2: right to vias under the jack, down between its shell legs on the bottom, up to R22/R23
r22 = {n: pad("R22", n) for n in (1, 2)}
r23 = {n: pad("R23", n) for n in (1, 2)}
for cc, rr, bx, v2 in (("CC1", r22, 79.95, (80.1, 11.35)), ("CC2", r23, 80.85, (81.2, 11.6))):
    p_ = J13p["A5"] if cc == "CC1" else J13p["B5"]
    v1 = (79.0, p_[1])
    track(cc, F, [p_, v1], FINE)
    via(cc, *v1)
    track(cc, B, [v1, (bx, p_[1]), (bx, v2[1] - (v2[0] - bx)), v2])
    via(cc, *v2)
    track(cc, F, [v2, rr[1]], 0.4)                       # wide: balances the GND pad's pour heat
# jack GND pads to the shell legs (GND), which join both pours
for gp in ("A1", "A12"):
    sh = min((pd for pd in fps["J13"].Pads() if pd.GetNumber() == "SH"),
             key=lambda pd: abs(pcbnew.ToMM(pd.GetPosition().y) - OY - J13p[gp][1]) +
             abs(pcbnew.ToMM(pd.GetPosition().x) - OX - J13p[gp][0]))
    track("GND", F, [J13p[gp], (round(pcbnew.ToMM(sh.GetPosition().x) - OX, 3),
                                round(pcbnew.ToMM(sh.GetPosition().y) - OY, 3))], 0.4)
# U6: OUT to the +5V_SW via (and TP2), ILIM down to R24
track("+5V_SW", F, [u6[6], (u6[6][0], SWV2[1]), SWV2], 0.5)
track("+5V_SW", F, [SWV2, pad("TP2", 1)], 0.4)
track("ILIM2", F, [u6[5], pad("R24", 1)], 0.45)
gu6 = (u6[2][0], 11.0)
track("GND", F, [u6[2], gu6], 0.4)
via("GND", *gu6)

# --- ADS1115 ---
u3 = {n: pad("U3", n) for n in range(1, 11)}
# VDD up to C4 pad 1, C4 pad 2 to a GND via. VDD's 3.3 V comes from the spine: a via by JP4's VDD pad, then under
# the package to the VDD pin. C4 also feeds R25 (the buck's feedback divider senses the rail at the ADC).
track("+3V3", F, [u3[8], (u3[8][0], c4[1][1]), c4[1]], TRACK)
vddv = (58.6, 13.6)
track("+3V3", F, [vddv, (vddv[0], 10.6), (u3[8][0], 10.6), u3[8]], FINE)
via("+3V3", *vddv)
track("+3V3", F, [c4[1], (c4[1][0], 6.25), (r25[1][0], 6.25), r25[1]], FINE)
gv4 = (c4[2][0] + 1.0, c4[2][1])
track("GND", F, [c4[2], gv4], 0.4)
via("GND", *gv4)
# SDA / SCL: up, left to their drops beside the jumpers, down to the lanes
SDA_DROP, SCL_DROP = 53.3, 54.5
track("SDA", F, [u3[9], (u3[9][0], 7.6), (SDA_DROP, 7.6), (SDA_DROP, LY["SDA"])], FINE)
via("SDA", SDA_DROP, LY["SDA"])
lane_x["SDA"].append(SDA_DROP)
track("SCL", F, [u3[10], (u3[10][0], 8.1), (SCL_DROP, 8.1), (SCL_DROP, LY["SCL"])], FINE)
via("SCL", SCL_DROP, LY["SCL"])
lane_x["SCL"].append(SCL_DROP)
# AIN2 / AIN3 round the top-right of the package down to R7 / R8; AIN0 straight down, AIN1 diagonally
r = {f"R{i}": {n: pad(f"R{i}", n) for n in (1, 2)} for i in range(5, 9)}
c = {f"C{i}": {n: pad(f"C{i}", n) for n in (1, 2)} for i in range(5, 9)}
track("AIN3", F, [u3[7], (u3[7][0], 8.2), (r["R8"][1][0], 8.2), r["R8"][1]], FINE)
track("AIN2", F, [u3[6], (u3[6][0], 8.6), (r["R7"][1][0], 8.6), r["R7"][1]], FINE)
track("AIN0", F, [u3[4], r["R5"][1]], FINE)
d = r["R6"][1][0] - u3[5][0]
track("AIN1", F, [u3[5], (u3[5][0], 14.45), (r["R6"][1][0], 14.45 + d), r["R6"][1]], FINE)
for i, an in enumerate(("AIN0", "AIN1", "AIN2", "AIN3")):
    track(an, F, [r[f"R{5 + i}"][1], c[f"C{5 + i}"][1]], 0.4)     # filter cap beside its resistor
    (rx, ry), (sx, sy) = r[f"R{5 + i}"][2], pad("J11", 3 + i)
    track(f"A{i}", F, [(rx, ry), (sx, sy)])
# Divider pads: R17 to R5's AIN0 pad, R18 down to C6, R19/R20 across to the AIN2/AIN3 tracks. R17's and R18's GND
# pads join the top pour; R18 and R19/R20 also get a GND via (their pour pockets are boxed in by the AIN tracks)
dv = {f"R{i}": {n: pad(f"R{i}", n) for n in (1, 2)} for i in range(17, 21)}
track("AIN0", F, [dv["R17"][1], r["R5"][1]])
track("AIN1", F, [dv["R18"][1], (c["C6"][1][0], c["C6"][1][1] - 0.18)])   # ends inside C6's pad: no acid trap
track("AIN2", F, [dv["R19"][1], (A_X[2], dv["R19"][1][1])])
track("AIN3", F, [dv["R20"][1], (A_X[3], dv["R20"][1][1])])
gv18 = (dv["R18"][2][0], dv["R18"][2][1] - 1.15)
track("GND", F, [dv["R18"][2], gv18], 0.4)
via("GND", *gv18)
gvd = (DIV_X, (dv["R19"][2][1] + dv["R20"][2][1]) / 2)
track("GND", F, [dv["R19"][2], gvd, dv["R20"][2]], 0.4)
via("GND", *gvd)
# GND pin to a via below it; ALERT down, left to a via, then on the bottom to JP7 by J1 pin 1
gvu = (u3[3][0] - 0.25, 15.75)
track("GND", F, [u3[3], (u3[3][0], gvu[1] - 0.45), gvu], FINE)
via("GND", *gvu)
ALV = (JP4_X - 0.2, 15.6)
track("ALERT", F, [u3[2], (u3[2][0], 14.85), (ALV[0], 14.85), ALV], FINE)
via("ALERT", *ALV)
jp7 = {n: pad("JP7", n) for n in (1, 2)}
jv = (jp7[1][0], 5.2)
ALERT_Y = 16.2                                          # below U5's GND via
track("ALERT", B, [ALV, (ALV[0] - 0.6, ALERT_Y), (37.4, ALERT_Y), (37.4, 8.6), (jv[0], 8.6), jv])
via("ALERT", *jv)
track("ALERT", F, [jv, jp7[1]])
track("ALERT_HOST", F, [jp7[2], j1[1]])
# ADDR: from pin 1 under the package to a via, then (bottom) to JP4's middle pad, JP5 and JP6
adv = (u3[1][0] + 0.75, ADC_Y)
track("ADDR", F, [u3[1], (u3[1][0], adv[1] + 0.8), adv], FINE)
via("ADDR", *adv)
jp4 = {n: pad("JP4", n) for n in (1, 2, 3)}
jp5 = {n: pad("JP5", n) for n in (1, 2)}
jp6 = {n: pad("JP6", n) for n in (1, 2)}
track("ADDR", B, [adv, (jp4[2][0] + 0.4, jp4[2][1]), jp4[2]])
track("ADDR", B, [jp5[1], jp6[1]])
track("ADDR", B, [(jp5[1][0], jp4[2][1]), jp4[2]])
track("+3V3", B, [jp4[3], (jp4[3][0], SPINE_Y)])                     # JP4's VDD pad straight down to the spine
track("+3V3", B, [vddv, (jp4[3][0], vddv[1])])
# JP5 (SCL option) from a via on the SCL drop; JP6 (SDA option) from a via on the SDA drop
sclv = (SCL_DROP, jp5[2][1])
via("SCL", *sclv)
track("SCL", B, [sclv, jp5[2]])
sdav = (SDA_DROP, jp6[2][1])
via("SDA", *sdav)
track("SDA", B, [sdav, jp6[2]])

# --- power switch (right of the ADC) ---
r16 = {n: pad("R16", n) for n in (1, 2)}
track("+5V", F, [p5v, r15[1]])
enq = (r15[2][0], 6.95)                                 # R15/Q3 EN: a via up from the EN run
track("EN", F, [enq, r15[2], q3[3]])
via("EN", *enq)
track("EN", B, [enq, (enq[0], EN_Y)])
track("PWR_OFF", F, [q3[1], pog, r16[1]])
gq3 = (q3[2][0] - 0.1, 13.45)                           # Q3 source: GND via below it, clear of PWR_OFF on the bottom
track("GND", F, [q3[2], (q3[2][0], gq3[1] - 0.5), gq3], 0.4)
via("GND", *gq3)
gr16 = (r16[2][0] + 1.05, r16[2][1] + 0.1)              # R16's GND pad: via to its right, above the SDA lane
track("GND", F, [r16[2], gr16])
via("GND", *gr16)

# J1's GND pin is boxed in on the bottom by G23 and ALERT: tie it to a via so it doesn't hang on one thermal spoke
gj = (j1[4][0] - 1.27, 7.75)
track("GND", B, [j1[4], gj], 0.4)
via("GND", *gj)

# Sockets' 3V3 pins that have no Qwiic: straight up to the 3V3 lane on the bottom
for ref in ("J6", "J10", "J11"):
    x, y = pad(ref, 2)
    track("+3V3", B, [(x, y), (x, LY["+3V3"])])
    lane_x["+3V3"].append(x)

# The lanes themselves (bottom layer)
for nn, xs in lane_x.items():
    track(nn, B, [(min(xs), LY[nn]), (max(xs), LY[nn])], PWR_TRACK if nn == "+3V3" else TRACK)

# GND stitching vias (both pours are GND), the bottom-left ones under the reference text.
for x, y in [(31.5, 13.6), (31.5, 15.6), (17.24, 21.6), (28.67, 21.6), (41.37, 21.6), (54.06, 21.6)]:
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
    elif just == "right":
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_RIGHT)
    board.Add(t)


BS = pcbnew.B_SilkS
text(f"BreadPortZero v{D.VERSION}", 15.0, 1.9, size=1.5, thick=0.25)
text("I2C SPI UART Analog", 24.4, 5.0, size=0.9)
text("Keyboard side", 46.2, 0.75, size=0.8)
for s, (x0, names, _) in D.SOCKETS.items():
    for i, lbl in enumerate(names):
        up = s == "J6" and i in (3, 5)                   # MOSI / MISO: too wide for one 2.54 mm row with SCK and CS
        text(lbl, x0 + i * D.P, 18.75 if up else 19.55, size=0.8)
for q in D.QWIIC:
    if q == "J5":
        text(D.QWIIC_LABEL[q], 74.45, J5_Y + 0.75, size=0.8, rot=90)      # between FID2 and the connector
    else:
        text(D.QWIIC_LABEL[q], QC[q], D.QWIIC_Y - 3.6, size=0.9)
text("ADS1115", ADC_X + 2.8, ADC_Y - 0.4, size=0.8, rot=90)
text("PWR", d1[2][0] - 0.5, d1[2][1] + 1.2, size=0.8)
text("FAULT", d2[1][0] - 0.8, 9.0, size=0.8)
text("I2C1", (SDA_X + SCL_X) / 2, 10.3, size=0.8)
text("SPI0 CE1", (pad("J6", 3)[0] + pad("J6", 6)[0]) / 2, 16.95, size=0.8)   # which bus the SPI socket is on
text("PU", PU_X + 3.35, 7.2, size=0.8)
text("ALERT", jp7[1][0] - 2.9, D.J1_ODD_Y, size=0.8)
for tp, lbl, dx, dy in (("TP1", "5V", 0, 1.5), ("TP2", "5VSW", 2.6, 0)):
    x, y = pad(tp, 1)
    text(lbl, x + dx, y + dy, size=0.8)
# --- bottom silkscreen: the board's manual ----------------------------------------------------------------------
# Read from below, so mirrored: a "left"-aligned bottom text starts at x and runs towards smaller board x. Body text is
# 0.8 mm: 1.25 mm pitch for prose (descenders, commas and underscores), 1.1 mm for the pin table; headings are bolder.
BODY, PITCH, TPITCH = 0.8, 1.25, 1.1


def btext(s, x, y, size=BODY, rot=0, align="left", bold=False):
    text(s, x, y, BS, size, rot, None if align == "center" else align, 0.2 if bold else 0.15)


def bline(pts, width=0.15):
    for a, b_ in zip(pts, pts[1:]):
        sh = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        sh.SetStart(P(*a))
        sh.SetEnd(P(*b_))
        sh.SetLayer(BS)
        sh.SetWidth(pcbnew.FromMM(width))
        board.Add(sh)


def barrow(x0, x1, y):
    """Arrow on the bottom silk along y, from board x0 to x1, head at x1."""
    bline([(x0, y), (x1, y)])
    h = 0.35 if x1 > x0 else -0.35
    bline([(x1 - h, y - 0.25), (x1, y), (x1 - h, y + 0.25)])


def twidth(s, size=BODY, bold=False):
    """Width in mm of a text as KiCad's font draws it."""
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(size), pcbnew.FromMM(size)))
    t.SetTextThickness(pcbnew.FromMM(0.2 if bold else 0.15))
    return pcbnew.ToMM(t.GetBoundingBox().GetWidth())


def V(u):
    """Board x of a position u mm from the left edge as seen from below."""
    return D.BOARD_W - u


# SPI end: title, setup steps, ports table, where the docs are
QA = 31.9
btext(f"BreadPortZero v{D.VERSION}", QA, 1.25, size=1.3, bold=True)
btext("SETUP", QA, 2.7, bold=True)
for k, (num, line) in enumerate([("1", "Fit with the CardputerZero off"), ("2", "Settings > ExtPort: 5V ON"),
                                 ("3", "config.txt: dtoverlay=i2c-gpio,bus=3,"),
                                 ("", "i2c_gpio_sda=22,i2c_gpio_scl=23"), ("4", "i2cdetect -y 3: ADC at 0x48")]):
    y = 3.95 + PITCH * k
    btext(num, QA, y)
    btext(line, QA - 1.3, y)
btext("PORTS (3.3 V logic)", QA, 10.3, bold=True)
for k, (lbl, line) in enumerate([("I2C", "bus 3: G22 SDA, G23 SCL, 4.7k pull-ups"),
                                 ("SPI", "/dev/spidev0.1, CS = G7 (CE1)"),
                                 ("UART", "G14 TX, G15 RX: serial console off"),
                                 ("A0-A3", "ADS1115 16-bit, 0-3.3 V max"),
                                 ("3V3", "250 mA from the host, 1 A with USB-C")]):
    y = 11.55 + PITCH * k
    btext(lbl, QA, y)
    btext(line, QA - 5.0, y)
btext("Docs: github.com/gabe-harris-modernfold/", QA, 17.9)
btext("M5CardputerZeroPCBs > BreadPortZero", QA, 19.15)

# Host header: each pin's GPIO beside it, then a pin table
for n in range(1, 15):
    name = D.HOST_PINS[n][0].split()[0]
    lbl = {"5VIN": "NC", "5VOUT": "5V"}.get(name, name)
    x, y = j1[n]
    btext(lbl, x, 1.25 if n % 2 else 8.35, align="center")
btext("HOST PORT J1", 51.2, 9.55, bold=True)
ODD = ["1 G26 ALERT", "3 G23 SCL", "5 G22 SDA", "7 G11 SCK", "9 G10 MOSI", "11 G9 MISO", "13 G7 CS/OFF"]
EVEN = ["2 5VIN NC", "4 GND", "6 5VOUT", "8 G2 I2C1", "10 G3 I2C1", "12 G14 TX", "14 G15 RX"]
for k, (a, b_) in enumerate(zip(ODD, EVEN)):
    btext(a, 51.2, 10.7 + TPITCH * k)
    btext(b_, 41.8, 10.7 + TPITCH * k)
btext("Grove port shares the 5V", 51.2, 18.55)        # the host's 5VOUT budget and G14/G15 (its UART default)
btext("budget and G14/G15 (UART)", 51.2, 19.8)

# Jumpers: JP8 by its pads, the top-side ones explained below it
btext("JP8 host pull-ups", jp8[2][0] + 1.1, PUH_Y + 0.4, align="right")
btext("JUMPERS (JP1-3, JP7: top)", 72.3, 3.2, bold=True)
for k, line in enumerate(["JP1+JP2: bus on I2C1, cut JP8", "JP3 cut: no 4.7k pull-ups", "JP7: ADC ALERT/RDY to G26"]):
    btext(line, 72.3, 4.6 + PITCH * k)
# JP9 and the ADC address, beside JP9 (read from below, right of it) and left of JP4-JP6
btext("JP9 = G7 OFF", 72.6, 8.2, bold=True)
for k, line in enumerate(["bridged: G7 high", "= cap off, no SPI"]):
    btext(line, 72.6, 9.45 + PITCH * k)
btext("ADC ADDRESS", 72.6, 11.95, bold=True)
for k, line in enumerate(["48 as made", "4A/4B: cut JP4"]):
    btext(line, 72.6, 13.2 + PITCH * k)
btext("4B", JPA_X - 2.1, jp5[2][1] + 0.65, align="center")
btext("4A", JPA_X - 2.1, jp6[1][1] + 0.65, align="center")
btext("48", JP4_X, jp4[1][1] - 1.55, align="center")   # above pad 1 (GND), below pad 3 (VDD)
btext("49", JP4_X, jp4[3][1] + 1.55, align="center")

# Power: where 3.3 V comes from (drawn), when USB counts, what the LEDs mean. Laid out left to right as read from
# below, from measured text widths.
def chain(y, items, u, gap=0.4):
    for kind, val in items:
        if kind == "arrow":
            barrow(V(u), V(u + 1.4), y)
            u += 1.4 + gap
        else:
            btext(val, V(u), y, bold=kind == "bold")
            u += twidth(val, bold=kind == "bold") + gap
    return u


PA, PB, P0 = 15.6, 17.1, 7.8
ua = chain(PA, [("text", "USB-C"), ("arrow", None), ("text", "1.1 A max")], P0)
ub = chain(PB, [("text", "HOST 5V"), ("arrow", None), ("text", "250 mA max")], P0)
um = max(ua, ub) + 0.6                                  # where the two paths join
bline([(V(ua), PA), (V(um), PA), (V(um), PB)])
bline([(V(ub), PB), (V(um), PB)])
chain(PB, [("arrow", None), ("bold", "BUCK 3V3")], um, gap=0.3)
btext("USB-C only while ExtPort 5V is ON", V(P0), 18.5)
btext("PWR LED on; FAULT = host overload", V(P0), 19.75)

# USB-C jack, between its shell legs; socket names along the edge
btext("USB-C 5V", 82.45, 5.9, rot=90, align="center", bold=True)
btext("no data", 81.25, 5.9, rot=90, align="center")
for ref, name in (("J6", "SPI"), ("J7", "I2C 2"), ("J8", "I2C 3"), ("J10", "UART"), ("J11", "ANALOG")):
    x0, names, _ = D.SOCKETS[ref]
    btext(name, x0 + (len(names) - 1) * D.P / 2, 23.2, align="center", bold=True)

commit_copper()
board.BuildConnectivity()
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
print("zones filled:", [z.IsFilled() for z in board.Zones()])
board.Save(str(OUT))
print(f"wrote {OUT}")

"""Write BreadPortZero.pretty: stock KiCad footprints this board needs changed. Run with KiCad's own Python:

    & "$env:LOCALAPPDATA\Programs\KiCad\10.0\bin\python.exe" tools\make_footprints.py

USB_C_Receptacle_HCTL_HC-TYPE-C-16P-01A_GNDTrim: KiCad's HCTL HC-TYPE-C-16P-01A land pattern puts the outer GND
pads (A1/B12, A12/B1) 0.18 mm from the plastic pegs' unplated holes, under the fabs' 0.2 mm rule (an engineering
query) and the board's 0.25 mm hole clearance. Each is trimmed 0.1 mm at its peg end, under the jack body; the
outer end, where the solder fillet forms, stays put. That leaves 0.28 mm. The shell legs' pads grow 0.1 mm each way:
their slots had a 0.20 mm ring, at the fabs' limit; now 0.25 mm.
"""
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).parent))
import design as D  # noqa: E402

LIB = Path(__file__).resolve().parent.parent / "BreadPortZero.pretty"
TRIM = 0.1


def usb_c():
    stock = "USB_C_Receptacle_HCTL_HC-TYPE-C-16P-01A"
    fp = pcbnew.FootprintLoad(str(D.KICAD / "share" / "kicad" / "footprints" / "Connector_USB.pretty"), stock)
    name = stock + "_GNDTrim"
    fp.SetFPID(pcbnew.LIB_ID("BreadPortZero", name))
    holes = [p.GetPosition() for p in fp.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH]
    cut = pcbnew.FromMM(TRIM)
    grow = pcbnew.FromMM(0.1)
    for p in fp.Pads():
        if p.GetNumber() == "SH":
            sz = p.GetSize(pcbnew.F_Cu)
            p.SetSize(pcbnew.F_Cu, pcbnew.VECTOR2I(sz.x + grow, sz.y + grow))
            continue
        if p.GetNumber() not in ("A1", "A12", "B1", "B12"):
            continue
        assert p.GetOrientationDegrees() % 180 == 0
        pos, sz = p.GetPosition(), p.GetSize(pcbnew.F_Cu)
        h = min(holes, key=lambda q: (q.x - pos.x) ** 2 + (q.y - pos.y) ** 2)
        if sz.y > sz.x:                                   # long along y: trim the end towards the peg
            d = 1 if h.y > pos.y else -1
            p.SetSize(pcbnew.F_Cu, pcbnew.VECTOR2I(sz.x, sz.y - cut))
            p.SetPosition(pcbnew.VECTOR2I(pos.x, pos.y - d * cut // 2))
        else:
            d = 1 if h.x > pos.x else -1
            p.SetSize(pcbnew.F_Cu, pcbnew.VECTOR2I(sz.x - cut, sz.y))
            p.SetPosition(pcbnew.VECTOR2I(pos.x - d * cut // 2, pos.y))
    fp.SetLibDescription(fp.GetLibDescription() + f" Outer GND pads trimmed {TRIM} mm at the peg end for 0.28 mm "
                         "to the peg holes; shell-leg pads 0.1 mm larger for a 0.25 mm ring (BreadPortZero).")
    return fp


LIB.mkdir(exist_ok=True)
io = pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.KICAD_SEXP)
for f in (usb_c(),):
    io.FootprintSave(str(LIB), f)
    print("wrote", LIB / f"{f.GetFPID().GetLibItemName()}.kicad_mod")

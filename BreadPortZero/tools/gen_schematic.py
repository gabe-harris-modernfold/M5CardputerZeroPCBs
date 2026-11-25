"""Generate BreadPortZero.kicad_sch from design.py (KiCad 10 format).

Symbols are copied from KiCad's own libraries (flattened if they extend another symbol) and from the project library
BreadPortZero.kicad_sym. Nets are made with labels and power symbols placed exactly on pin ends, so no wires are needed.
Run with any Python 3.10+:

    python tools/gen_schematic.py
"""
from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import design as D  # noqa: E402

KICAD_SYM = D.KICAD / "share" / "kicad" / "symbols"
HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "BreadPortZero.kicad_sch"
PROJECT_LIB = HERE / "BreadPortZero.kicad_sym"
ROOT_UUID = "3b0d7f52-8e3c-4f6a-9d1e-7a5c2b8e4f10"
PROJECT = "BreadPortZero"


# --- minimal s-expression reader/writer ----------------------------------------------------------------------
class Sym(str):
    """A bare (unquoted) atom."""


TOK = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


def parse(text: str):
    stack, cur, pos = [], [], 0
    while True:
        m = TOK.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        if m.group(1):
            stack.append(cur)
            cur = []
        elif m.group(2):
            done, cur = cur, stack.pop()
            cur.append(done)
        elif m.group(3) is not None:
            cur.append(m.group(3).replace('\\"', '"').replace("\\\\", "\\"))
        else:
            cur.append(Sym(m.group(4)))
    return cur[0]


def dump(x, ind=0) -> str:
    if isinstance(x, list):
        if not x:
            return "()"
        simple = all(not isinstance(e, list) for e in x)
        if simple:
            return "(" + " ".join(dump(e) for e in x) + ")"
        out = "(" + dump(x[0])
        for e in x[1:]:
            out += ("\n" + "\t" * (ind + 1) + dump(e, ind + 1)) if isinstance(e, list) else " " + dump(e)
        return out + "\n" + "\t" * ind + ")"
    if isinstance(x, Sym):
        return str(x)
    if isinstance(x, (int, float)):
        return f"{x:g}" if isinstance(x, float) else str(x)
    return '"' + str(x).replace("\\", "\\\\").replace('"', '\\"') + '"'


def S(*a):
    return [Sym(a[0]), *a[1:]]


def find(node, key):
    return [e for e in node if isinstance(e, list) and e and e[0] == key]


# --- library symbols -----------------------------------------------------------------------------------------
_LIBS: dict[str, list] = {}


def lib_symbol(lib_id: str) -> list:
    lib, name = lib_id.split(":")
    if lib not in _LIBS:
        path = PROJECT_LIB if lib == PROJECT else KICAD_SYM / f"{lib}.kicad_sym"
        _LIBS[lib] = parse(path.read_text(encoding="utf-8"))
    syms = {e[1]: e for e in find(_LIBS[lib], "symbol")}
    s = [list(e) if isinstance(e, list) else e for e in syms[name]]
    ext = find(s, "extends")
    if ext:  # flatten: parent's graphics and pins, child's properties
        parent = lib_symbol(f"{lib}:{ext[0][1]}")
        pname = parent[1].split(":")[1]
        s = [e for e in s if not (isinstance(e, list) and e[0] == "extends")]
        keep = {e[1] for e in find(s, "property")}
        for e in parent[2:]:
            if isinstance(e, list) and e[0] == "symbol":
                sub = list(e)
                sub[1] = sub[1].replace(pname, name, 1)
                s.append(sub)
            elif isinstance(e, list) and e[0] == "property" and e[1] not in keep:
                s.append(e)
            elif isinstance(e, list) and e[0] in ("pin_names", "pin_numbers", "exclude_from_sim", "in_bom",
                                                    "on_board") and not find(s, e[0]):
                s.insert(2, e)
    s[1] = f"{lib}:{name}"
    return s


def pins_of(sym: list) -> dict[str, tuple[float, float, float]]:
    """pin number -> (x, y, angle) of the pin's connection end, in symbol coordinates (y up)."""
    out = {}

    def walk(n):
        for e in n:
            if isinstance(e, list):
                if e and e[0] == "pin":
                    at = find(e, "at")[0]
                    num = find(e, "number")[0][1]
                    out[str(num)] = (float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0)
                else:
                    walk(e)
    walk(sym)
    return out


# --- schematic building ----------------------------------------------------------------------------------------
def uid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{PROJECT}/{seed}"))


def font(size=1.27, **kw):
    f = S("effects", S("font", S("size", size, size)))
    if kw.get("justify"):
        f.append(S("justify", *[Sym(j) for j in kw["justify"].split()]))
    if kw.get("hide"):
        f.append(S("hide", Sym("yes")))
    return f


def prop(name, value, x, y, hide=False, angle=0):
    p = S("property", name, value, S("at", x, y, angle))
    if hide:
        p.append(S("hide", Sym("yes")))
    p.append(font())
    return p


def placed(ref, lib_id, value, x, y, fp="", fields=None, pins=(), in_bom=True, unit=1, dnp=False):
    s = S("symbol", S("lib_id", lib_id), S("at", x, y, 0), S("unit", unit), S("exclude_from_sim", Sym("no")),
          S("in_bom", Sym("yes" if in_bom else "no")), S("on_board", Sym("yes" if in_bom or fp else "no")),
          S("dnp", Sym("yes" if dnp else "no")), S("uuid", uid("sym/" + ref)))
    s += [prop("Reference", ref, x, y - 6, hide=ref.startswith("#")), prop("Value", value, x, y + 6,
                                                                          hide=ref.startswith("#")),
          prop("Footprint", fp, x, y, hide=True), prop("Datasheet", "", x, y, hide=True),
          prop("Description", "", x, y, hide=True)]
    for k, v in (fields or {}).items():
        s.append(prop(k, v, x, y, hide=True))
    for p in pins:
        s.append(S("pin", str(p), S("uuid", uid(f"pin/{ref}/{p}"))))
    s.append(S("instances", S("project", PROJECT, S("path", "/" + ROOT_UUID, S("reference", ref), S("unit", unit)))))
    return s


def main():
    items, lib_syms, used = [], [], set()

    def need(lib_id):
        if lib_id not in used:
            used.add(lib_id)
            lib_syms.append(lib_symbol(lib_id))
        return lib_symbol(lib_id)

    G = 2.54
    layout = {
        # power: host 5 V -> current-limited switch -> LDO
        "J1": (20, 22), "C1": (40, 14), "U1": (50, 14), "R1": (62, 18), "D3": (60, 9), "C2": (70, 14),
        "U2": (80, 14), "L1": (87, 11), "R25": (88, 20), "R26": (88, 26),
        "C3": (92, 14), "C4": (99, 14), "C9": (103, 14), "R9": (108, 12), "D1": (108, 22),
        # USB-C supplemental input: jack, CC pull-downs, TVS, input cap, current-limited switch
        "J13": (62, 30), "R22": (74, 26), "R23": (74, 32), "D4": (80, 30), "C11": (86, 30), "U6": (96, 30),
        "R24": (106, 32),
        # SPI buffer, its decoupling and MISO pull-up, socket ESD
        "U7": (148, 72), "C10": (158, 64), "R21": (158, 72),
        "D5": (140, 92), "D6": (146, 92), "D7": (152, 92), "D8": (158, 92),
        # FAULT LED; power switch on U1's EN (R15 pull-up, Q3 + R16 driven from G7 through JP9)
        "R14": (116, 12), "D2": (116, 22), "R15": (126, 12), "Q3": (136, 16), "R16": (144, 20), "JP9": (152, 12),
        # host bus select, I2C isolation, pull-ups, ESD, UART series resistors
        "JP1": (14, 40), "JP2": (28, 40), "JP3": (52, 40), "JP8": (42, 34), "R12": (62, 40), "R13": (70, 40),
        "Q1": (22, 50), "Q2": (36, 50), "R2": (48, 50), "R3": (56, 50), "R10": (66, 50), "R11": (76, 50),
        "U4": (36, 66), "U5": (62, 66),
        # ADC, its address/ALERT jumpers, input resistors, filter capacitors and divider pads
        "U3": (92, 50), "R5": (108, 44), "R6": (108, 50), "R7": (108, 56), "R8": (108, 62),
        "C5": (116, 44), "C6": (116, 50), "C7": (116, 56), "C8": (116, 62),
        "R17": (132, 44), "R18": (132, 50), "R19": (132, 56), "R20": (132, 62),
        "JP4": (84, 66), "JP5": (94, 66), "JP6": (104, 66), "JP7": (122, 50),
        # test pads
        "TP1": (126, 30), "TP2": (132, 30),
        # connectors
        "J2": (14, 82), "J3": (28, 82), "J4": (42, 82), "J5": (56, 82),
        "J6": (72, 82), "J7": (84, 82), "J8": (96, 82), "J10": (122, 82), "J11": (136, 82),
    }
    power = {n: f"power:{n}" for n in D.POWER_NETS}
    pcount = 0
    for part in D.PARTS:
        sym = need(part["lib"])
        x, y = (v * G for v in layout[part["ref"]])
        pins = pins_of(sym)
        fields = {"LCSC": part["lcsc"], "MPN": part["mpn"]} if part["lcsc"] else {}
        items.append(placed(part["ref"], part["lib"], part["value"], x, y, part["fp"], fields, pins.keys(),
                            in_bom=part.get("bom", True), dnp=part.get("dnp", False)))
        for num, (px, py, ang) in pins.items():
            key = int(num) if num.isdigit() else num
            net = D.NETS[part["ref"]].get(key)
            ex, ey = round(x + px, 3), round(y - py, 3)
            if net is None:
                items.append(S("no_connect", S("at", ex, ey), S("uuid", uid(f"nc/{part['ref']}/{num}"))))
            elif net in power:
                pcount += 1
                need(power[net])
                items.append(placed(f"#PWR{pcount:02d}", power[net], net, ex, ey, pins=["1"], in_bom=False))
            else:
                la = (ang + 180) % 360
                just = {0: "left bottom", 180: "right bottom", 90: "left bottom", 270: "right bottom"}[int(la)]
                items.append(S("label", net, S("at", ex, ey, la), font(justify=just),
                               S("uuid", uid(f"label/{part['ref']}/{num}"))))
    # PWR_FLAGs: +5V, GND and VBUS come from connectors and +3V3 from the buck's inductor (passive pins), so tell ERC
    # they are driven
    flags = {"+5V": (30 * G, 6 * G), "GND": (35 * G, 6 * G), "+3V3": (40 * G, 6 * G), "VBUS": (45 * G, 6 * G)}
    for i, (net, (x, y)) in enumerate(flags.items(), 1):
        need("power:PWR_FLAG")
        items.append(placed(f"#FLG0{i}", "power:PWR_FLAG", "PWR_FLAG", x, y, pins=["1"], in_bom=False))
        if net in power:
            need(power[net])
            pcount += 1
            items.append(placed(f"#PWR{pcount:02d}", power[net], net, x, y, pins=["1"], in_bom=False))
        else:
            items.append(S("label", net, S("at", x, y, 0), font(justify="left bottom"),
                           S("uuid", uid(f"label/flag/{net}"))))
    notes = [
        "BreadPortZero: I2C x4 (STEMMA QT/Qwiic + 0.1 in sockets), SPI, UART and 4 analog inputs for the "
        "CardputerZero top port, with a USB-C supplemental 5 V input",
        "J1 pin map from M5Stack's port photo: 12 = G14 TX, 14 = G15 RX (the docs table swaps them)",
        "Power: 5VOUT (pin 6, Settings > ExtPort) -> U1 TPS2553 (234-303 mA, R1 = 100k) -> D3 -> +5V_SW -> "
        "U2 TLV62569 buck -> 3.3 V",
        "USB-C J13 (5.1k Rd on CC) -> D4 TVS -> U6 TPS2553 (1.09-1.26 A, R24 = 22k) -> +5V_SW. U6 shares U1's EN, so "
        "USB only powers the cap while the host's 5VOUT is on; D3 makes USB (higher) carry the load and blocks it "
        "from the host",
        "Neither source can back-feed the other: D3 blocks USB from the host's 5V, U6 blocks the host from VBUS "
        "(1 uA max reverse with VIN = 0). D2 lights while U1 (host path) limits current or is in thermal shutdown "
        "(U6's FAULT is left open)",
        "SPI: G11/G10/G9 are shared with the CardputerZero's display (CE0), so U7 (74LVC125) buffers them. MISO "
        "drives G9 only while CS (G7) is low; Ioff isolates the display's bus when the cap is off. D5-D8: socket ESD",
        "Power switch: R15 holds EN (U1, U6, U2) high. Bridge JP9 and drive G7 high to turn BreadPortZero off (Q3 "
        "pulls EN low); R16 keeps it on while G7 floats or carries the Pi's default pull-up. JP9 bridged = no SPI CS",
        "External I2C bus on G22 (SDA) / G23 (SCL), a software bus (dtoverlay=i2c-gpio) apart from the host's I2C1 "
        "(keyboard, RTC, IMU). Bridge JP1 + JP2 to put it on I2C1 instead",
        "Q1/Q2: I2C isolation, gates on BreadPortZero's 3.3 V; unpowered, the external bus is cut off from the host. "
        "R12/R13: host-side pull-ups (cut JP8 if JP1/JP2 are bridged). "
        "U4: ESD protection. JP3: cut to remove the 4.7k pull-ups",
        "U3 ADS1115: JP4 1-2 (as made) = 0x48, 2-3 = 0x49; cut JP4 and bridge JP6 = 0x4A or JP5 = 0x4B. "
        "JP7: bridge to bring ALERT/RDY to G26",
        "Analog inputs 0-3.3 V: R5-R8 with C5-C8 = 1.6 kHz anti-alias filter, and they limit fault current. "
        "R17-R20 (not fitted): divider pads, e.g. 1.8k for 0-5 V inputs",
        "R10/R11 (4.7k) limit current into or out of a UART device that is unpowered or runs at 5 V. "
        "U5: ESD protection on the UART socket",
    ]
    for i, t in enumerate(notes):
        items.append(S("text", t, S("exclude_from_sim", Sym("no")), S("at", 10 * G, (100 + 2 * i) * G, 0),
                       font(justify="left bottom"), S("uuid", uid(f"note/{i}"))))
    sch = S("kicad_sch", S("version", 20260101), S("generator", "eeschema"), S("generator_version", "10.0"),
            S("uuid", ROOT_UUID), S("paper", "A3"),
            S("title_block", S("title", "BreadPortZero"), S("date", "2026-11-25"), S("rev", D.VERSION),
              S("comment", 1, "I2C / UART / analog breakout cap for M5Stack CardputerZero")),
            S("lib_symbols", *lib_syms), *items,
            S("sheet_instances", S("path", "/", S("page", "1"))), S("embedded_fonts", Sym("no")))
    OUT.write_text(dump(sch) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

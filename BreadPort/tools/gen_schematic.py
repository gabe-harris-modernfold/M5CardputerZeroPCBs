"""Generate BreadPort.kicad_sch from design.py (KiCad 10 format).

Symbols are copied from KiCad's own libraries (flattened if they extend another symbol) and from the project library
BreadPort.kicad_sym. Nets are made with labels and power symbols placed exactly on pin ends, so no wires are needed.
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
OUT = HERE / "BreadPort.kicad_sch"
PROJECT_LIB = HERE / "BreadPort.kicad_sym"
ROOT_UUID = "3b0d7f52-8e3c-4f6a-9d1e-7a5c2b8e4f10"
PROJECT = "BreadPort"


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


def placed(ref, lib_id, value, x, y, fp="", fields=None, pins=(), in_bom=True, unit=1):
    s = S("symbol", S("lib_id", lib_id), S("at", x, y, 0), S("unit", unit), S("exclude_from_sim", Sym("no")),
          S("in_bom", Sym("yes" if in_bom else "no")), S("on_board", Sym("yes" if in_bom or fp else "no")),
          S("dnp", Sym("no")), S("uuid", uid("sym/" + ref)))
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
        "J1": (20, 22), "C1": (40, 14), "U1": (50, 14), "R1": (62, 18), "C2": (70, 14), "U2": (80, 14),
        "C3": (92, 14), "C4": (99, 14), "R9": (108, 12), "D1": (108, 22),
        # I2C isolation, pull-ups, UART series resistors
        "Q1": (22, 50), "Q2": (36, 50), "R2": (48, 50), "R3": (56, 50), "R10": (66, 50), "R11": (76, 50),
        # ADC and its input resistors
        "U3": (92, 50), "R5": (108, 44), "R6": (108, 50), "R7": (108, 56), "R8": (108, 62),
        # connectors
        "J2": (14, 82), "J3": (28, 82), "J4": (42, 82), "J5": (56, 82),
        "J6": (72, 82), "J7": (84, 82), "J8": (96, 82), "J9": (108, 82), "J10": (122, 82), "J11": (136, 82),
    }
    power = {n: f"power:{n}" for n in D.POWER_NETS}
    pcount = 0
    for part in D.PARTS:
        sym = need(part["lib"])
        x, y = (v * G for v in layout[part["ref"]])
        pins = pins_of(sym)
        fields = {"LCSC": part["lcsc"], "MPN": part["mpn"]}
        items.append(placed(part["ref"], part["lib"], part["value"], x, y, part["fp"], fields, pins.keys()))
        for num, (px, py, ang) in pins.items():
            key = int(num) if num.isdigit() else num
            net = D.NETS[part["ref"]].get(key)
            ex, ey = round(x + px, 3), round(y - py, 3)
            if net is None:
                if not (part["ref"] == "U2" and num == "4"):  # AP2112K pin 4 is already a no-connect pin
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
    # PWR_FLAGs: +5V and GND come from the connector, so tell ERC they are driven
    for i, (net, (x, y)) in enumerate({"+5V": (30 * G, 6 * G), "GND": (35 * G, 6 * G)}.items(), 1):
        need("power:PWR_FLAG")
        need(power[net])
        items.append(placed(f"#FLG0{i}", "power:PWR_FLAG", "PWR_FLAG", x, y, pins=["1"], in_bom=False))
        pcount += 1
        items.append(placed(f"#PWR{pcount:02d}", power[net], net, x, y, pins=["1"], in_bom=False))
    notes = [
        "BreadPort: I2C x4 (STEMMA QT/Qwiic + 0.1 in sockets), UART and 4 analog inputs for the CardputerZero top port",
        "J1 pin map from M5Stack's port photo: 12 = G14 TX, 14 = G15 RX (the docs table swaps them)",
        "Power: 5VOUT (pin 6, switch on in Settings > ExtPort) -> U1 SY6280AAC, limit 6800 / R1 = 378 mA typ (+-25%) -> U2 3.3 V LDO",
        "U1 blocks reverse current, so nothing on BreadPort can back-feed the host's 5 V",
        "Q1/Q2: I2C isolation. Gates on BreadPort's 3.3 V; unpowered, the external bus is cut off from the host's I2C1",
        "U3 ADS1115 at 0x48 (host bus uses 0x10, 0x32, 0x34, 0x4F, 0x55, 0x68). Inputs 0-3.3 V; R5-R8 limit fault current",
        "R10/R11 limit current into or out of a UART device that is unpowered or runs at 5 V",
    ]
    for i, t in enumerate(notes):
        items.append(S("text", t, S("exclude_from_sim", Sym("no")), S("at", 10 * G, (100 + 2 * i) * G, 0),
                       font(justify="left bottom"), S("uuid", uid(f"note/{i}"))))
    sch = S("kicad_sch", S("version", 20260101), S("generator", "eeschema"), S("generator_version", "10.0"),
            S("uuid", ROOT_UUID), S("paper", "A3"),
            S("title_block", S("title", "BreadPort"), S("date", "2026-11-25"), S("rev", "0.1"),
              S("comment", 1, "I2C / UART / analog breakout cap for M5Stack CardputerZero")),
            S("lib_symbols", *lib_syms), *items,
            S("sheet_instances", S("path", "/", S("page", "1"))), S("embedded_fonts", Sym("no")))
    OUT.write_text(dump(sch) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

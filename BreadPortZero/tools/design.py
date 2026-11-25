"""BreadPortZero design data, shared by gen_schematic.py and gen_pcb.py.

Coordinates for the PCB are in mm in a top view of the board (the side facing away from the CardputerZero), x to
the right, y down, origin at the top-left board corner. y = 0 is the edge on the keyboard side of the CardputerZero.
"""
import os
from pathlib import Path

VERSION = "0.4"                 # schematic revision and silkscreen
BOARD_W, BOARD_H = 84.0, 24.0   # same footprint as M5Stack's Cap modules (and LightSensingPCB)
BOARD_THICKNESS = 1.6

# KiCad 10: default all-users install, or the per-user install winget makes
KICAD = next((p for p in (Path(os.environ.get("KICAD_DIR", "")),
                          Path(r"C:\Program Files\KiCad\10.0"),
                          Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "KiCad" / "10.0")
              if p.name and (p / "share" / "kicad").is_dir()), Path(r"C:\Program Files\KiCad\10.0"))

# CardputerZero top expansion port (EXT 2.54-14P, female on the host). Same pin map and position as LightSensingPCB:
# odd row on the keyboard side, pin 1 (G26) on the left looking down at the top edge.
J1_X0 = BOARD_W / 2 - 3 * 2.54          # pin 1 / pin 2 column; header centred along the 84 mm edge
J1_ODD_Y, J1_EVEN_Y = 3.63, 6.17

HOST_PINS = {
    1: ("G26", "ALERT_HOST"), 2: ("5VIN", None), 3: ("G23", "SCL_HOST"), 4: ("GND", "GND"), 5: ("G22", "SDA_HOST"),
    6: ("5VOUT", "+5V"), 7: ("G11 SPI SCLK", "SCK_HOST"), 8: ("G2 I2C1 SDA", "I2C1_SDA"),
    9: ("G10 SPI MOSI", "MOSI_HOST"), 10: ("G3 I2C1 SCL", "I2C1_SCL"), 11: ("G9 SPI MISO", "MISO_HOST"),
    12: ("G14 UART TX", "TX_HOST"), 13: ("G7 SPI CE1", "G7_HOST"), 14: ("G15 UART RX", "RX_HOST"),
}
# SPI0 (G11/G10/G9) also drives the CardputerZero's own display (CE0, G8), so the SPI socket is buffered (U7). G7 (CE1)
# is the socket's chip select, and also reaches the power switch through JP9 (open as made): bridged, G7 high turns the
# cap off, so the two uses exclude each other.
# BreadPortZero's bus runs on its own host pins, G22 (SDA) / G23 (SCL), as a software I2C bus (dtoverlay=i2c-gpio), so
# external devices can't hang or clash with the host's I2C1 (keyboard, RTC, IMU). JP1/JP2 bridge it onto I2C1 instead.

# --- breadboard socket row along the back edge ----------------------------------------------------------------
P = 2.54
SOCKET_Y = 21.6
# pin-1 x of each socket. The UART socket sits one pitch right of J1's TX/RX pins so TX/RX jog right once.
SOCKETS = {   # ref: (pin-1 x, pin names, nets)
    # SPI: CS before MISO, the order the buffer's outputs reach the socket without crossing
    "J6":  (2.63,  ["GND", "3V3", "SCK", "MOSI", "CS", "MISO"], ["GND", "+3V3", "SCK", "MOSI", "CS", "MISO"]),
    "J7":  (19.14, ["GND", "3V3", "SDA", "SCL"], ["GND", "+3V3", "SDA", "SCL"]),
    "J8":  (30.57, ["GND", "3V3", "SDA", "SCL"], ["GND", "+3V3", "SDA", "SCL"]),
    "J10": (44.54, ["GND", "3V3", "TXO", "RXI"], ["GND", "+3V3", "TX", "RX"]),
    "J11": (55.97, ["GND", "3V3", "A0", "A1", "A2", "A3"], ["GND", "+3V3", "A0", "A1", "A2", "A3"]),
}
# STEMMA QT / Qwiic connectors (pin 1 GND, 2 3V3, 3 SDA, 4 SCL). Qwiic 2 and 3 sit above their I2C sockets; Qwiic 1
# (left edge, above the SPI socket) and Qwiic 4 (bottom-right corner, below the USB-C jack) have no socket of their own.
QWIIC_Y = 12.0
QWIIC = {"J2": None, "J3": "J7", "J4": "J8", "J5": None}
QWIIC_LABEL = {"J2": "I2C 1", "J3": "I2C 2", "J4": "I2C 3", "J5": "I2C 4"}
QWIIC_NETS = {1: "GND", 2: "+3V3", 3: "SDA", 4: "SCL", "MP": "GND"}

# Bottom-layer bus lanes under the socket row (top to bottom)
LANE_Y = {"SDA": 17.0, "SCL": 17.9, "+3V3": 18.8}

SOCKET_LCSC = {4: ("CONNFLY DS1023-1x4SF11", "C7509546"), 6: ("BOOMELE 2.54-1*6P", "C40877")}


def _socket(ref, label):
    n = len(SOCKETS[ref][1])
    mpn, lcsc = SOCKET_LCSC[n]
    return dict(ref=ref, lib=f"Connector_Generic:Conn_01x0{n}", value=f"Socket 1x{n}", label=label,
                fp=f"Connector_PinSocket_2.54mm:PinSocket_1x0{n}_P2.54mm_Vertical", side="top", mpn=mpn, lcsc=lcsc)


def _qwiic(ref, n):
    return dict(ref=ref, lib="Connector_Generic_MountingPin:Conn_01x04_MountingPin", value="Qwiic SH 4P",
                label=f"Qwiic {n}",
                fp="Connector_JST:JST_SH_BM04B-SRSS-TB_1x04-1MP_P1.00mm_Vertical", side="top",
                mpn="Megastar ZX-SH1.0-4PLT", lcsc="C7430453")


def _r(ref, value, lcsc, mpn, size="0603"):
    fp = {"0402": "Resistor_SMD:R_0402_1005Metric", "0603": "Resistor_SMD:R_0603_1608Metric"}[size]
    return dict(ref=ref, lib="Device:R", value=value, fp=fp, side="top", mpn=mpn, lcsc=lcsc)


def _c(ref, value, lcsc, mpn, size="0603"):
    fp = {"0402": "Capacitor_SMD:C_0402_1005Metric", "0603": "Capacitor_SMD:C_0603_1608Metric",
          "0805": "Capacitor_SMD:C_0805_2012Metric"}[size]
    return dict(ref=ref, lib="Device:C", value=value, fp=fp, side="top", mpn=mpn, lcsc=lcsc)


def _jp(ref, kind, label, side="top"):
    """Solder jumper: PCB copper only, not in the BOM or the position file."""
    fp = {"2_Open": "SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm",
          "2_Bridged": "SolderJumper-2_P1.3mm_Bridged_RoundedPad1.0x1.5mm",
          "3_Bridged12": "SolderJumper-3_P1.3mm_Bridged12_RoundedPad1.0x1.5mm",
          "3_Bridged123": "SolderJumper-3_P1.3mm_Bridged123_RoundedPad1.0x1.5mm"}[kind]
    return dict(ref=ref, lib=f"Jumper:SolderJumper_{kind}", value=label, label=label, fp=f"Jumper:{fp}", side=side,
                mpn="", lcsc="", bom=False)


def _dnp(ref, label):
    """Unfitted 0603 resistor position (divider pad): copper only, marked DNP, not in the BOM or the position file."""
    return dict(ref=ref, lib="Device:R", value="DNP", label=label, fp="Resistor_SMD:R_0603_1608Metric", side="top",
                mpn="", lcsc="", bom=False, dnp=True)


def _tp(ref, label):
    return dict(ref=ref, lib="Connector:TestPoint", value=label, label=label, fp="TestPoint:TestPoint_Pad_D1.0mm",
                side="top", mpn="", lcsc="", bom=False)


R1K = ("1k", "C21190", "0603WAF1001T5E")
R4K7 = ("4.7k", "C23162", "0603WAF4701T5E")
R10K = ("10k", "C25804", "0603WAF1002T5E")
R100K = ("100k", "C25803", "0603WAF1003T5E")
USBLC6 = dict(lib="Power_Protection:USBLC6-2SC6", value="USBLC6-2SC6", fp="Package_TO_SOT_SMD:SOT-23-6", side="top",
              mpn="USBLC6-2SC6", lcsc="C7519")
BSS138 = dict(lib="Transistor_FET:BSS138", value="BSS138", fp="Package_TO_SOT_SMD:SOT-23", side="top",
              mpn="BSS138LT1G", lcsc="C82045")
C100N = ("100nF", "C14663", "CC0603KRX7R9BB104")
# filter caps: 0402 fits between the 2.54 mm-pitch input resistors
C100N_0402 = ("100nF", "C1525", "CL05B104KO5NNNC", "0402")
C10U = ("10uF", "C15850", "CL21A106KAYNNNE", "0805")
R5K1 = ("5.1k", "C23186", "0603WAF5101T5E")
# 0402 where space is tight (around the USB-C jack and the buck)
R22K_0402 = ("22k", "C25768", "0402WGF2202TCE", "0402")
R100K_0402 = ("100k", "C25741", "0402WGF1003TCE", "0402")
ESD5Z = dict(lib="Device:D_Zener", value="ESD5Z5.0", fp="Diode_SMD:D_SOD-523", side="top", mpn="ESD5Z5.0T1G",
             lcsc="C5199848")
TPS2553 = dict(lib="BreadPortZero:TPS2553DBV", value="TPS2553", fp="Package_TO_SOT_SMD:SOT-23-6", side="top",
               mpn="TPS2553DBVR", lcsc="C55266")

# Parts. lcsc numbers are used for the JLCPCB BOM; mpn for PCBWay.
PARTS = [
    dict(ref="J1", lib="Connector_Generic:Conn_02x07_Odd_Even", value="CardputerZero EXT 2x7",
         fp="Connector_PinHeader_2.54mm:PinHeader_2x07_P2.54mm_Vertical", side="bottom",
         mpn="BOOMELE 2.54-2*7P", lcsc="C71397"),
    _qwiic("J2", 1), _qwiic("J3", 2), _qwiic("J4", 3), _qwiic("J5", 4),
    _socket("J6", "SPI"), _socket("J7", "I2C 2"), _socket("J8", "I2C 3"),
    _socket("J10", "UART"), _socket("J11", "Analog"),
    # USB-C jack for supplemental 5 V (power only: D+/D-/SBU unconnected; 5.1k on CC1/CC2 makes it a sink). KiCad's
    # footprint with its outer GND pads trimmed clear of the peg holes (make_footprints.py)
    dict(ref="J13", lib="Connector:USB_C_Receptacle_USB2.0_16P", value="USB-C 5V",
         fp="BreadPortZero:USB_C_Receptacle_HCTL_HC-TYPE-C-16P-01A_GNDTrim", side="top", mpn="HC-TYPE-C-16P-01A",
         lcsc="C2894897"),
    # TPS2553 current-limited switch: limit 234-303 mA over temperature and process with R1 = 100k (TI's I_OS min/max
    # equations), so BreadPortZero can never draw the host's whole 500 mA ExtPort budget
    dict(ref="U1", **TPS2553),
    # second TPS2553 on the USB input: limit 1.09-1.26 A with R24 = 22k; enabled from the same EN as U1, so USB only
    # powers the cap while the host's 5VOUT is on
    dict(ref="U6", **TPS2553),
    # 3.3 V buck (replaces v0.3's AP2112K LDO): 2 A, about 90% efficient
    dict(ref="U2", lib="BreadPortZero:TLV62569DBV", value="TLV62569", fp="Package_TO_SOT_SMD:SOT-23-5", side="top",
         mpn="TLV62569DBVR", lcsc="C141836"),
    dict(ref="L1", lib="Device:L", value="2.2uH", fp="Inductor_SMD:L_Changjiang_FTC252012S", side="top",
         mpn="FTC252012S2R2MBCA", lcsc="C5832372"),
    # host path diode: lets USB (about 0.45 V higher) take the load, and stops USB back-feeding the host's 5VOUT
    dict(ref="D3", lib="Device:D_Schottky", value="B5819W", fp="Diode_SMD:D_SOD-123", side="top",
         mpn="B5819W SL", lcsc="C8598"),
    dict(ref="D4", **ESD5Z),                    # VBUS clamp: hot-plug ringing (TPS2553 IN is 7 V abs max)
    # SPI buffer: SCK/MOSI/CS out to the socket, MISO back only while CS (G7) is low; Ioff isolates when unpowered
    dict(ref="U7", lib="BreadPortZero:74LVC125_PW", value="74LVC125", fp="Package_SO:TSSOP-14_4.4x5mm_P0.65mm",
         side="top", mpn="SN74LVC125APWR", lcsc="C7813"),
    # SPI socket ESD: one TVS per line, each beside its track (the buffer's interleaved pinout leaves no room for a
    # 4-line array between it and the socket)
    *[dict(ref=r, **ESD5Z) for r in ("D5", "D6", "D7", "D8")],
    dict(ref="U3", lib="Analog_ADC:ADS1115IDGS", value="ADS1115", fp="Package_SO:MSOP-10_3x3mm_P0.5mm",
         side="top", mpn="ADS1115IDGSR", lcsc="C37593"),
    dict(ref="U4", **USBLC6), dict(ref="U5", **USBLC6),
    dict(ref="Q1", **BSS138), dict(ref="Q2", **BSS138), dict(ref="Q3", **BSS138),
    _r("R1", *R100K),
    _r("R2", *R4K7), _r("R3", *R4K7),
    _r("R5", *R1K), _r("R6", *R1K), _r("R7", *R1K), _r("R8", *R1K), _r("R9", *R1K),
    _r("R10", *R4K7), _r("R11", *R4K7),
    _r("R12", *R10K), _r("R13", *R10K),
    _r("R14", *R1K), _r("R15", *R100K_0402), _r("R16", *R10K),
    # divider pads, AINx to GND (unfitted)
    _dnp("R17", "A0 div"), _dnp("R18", "A1 div"), _dnp("R19", "A2 div"), _dnp("R20", "A3 div"),
    _r("R21", *R100K),                          # MISO pull-up (socket side): defined level with nothing plugged in
    _r("R22", *R5K1), _r("R23", *R5K1),         # USB-C CC1/CC2 pull-downs (Rd)
    _r("R24", *R22K_0402),                      # U6 current limit
    _r("R25", *R100K_0402), _r("R26", *R22K_0402),   # buck feedback: 0.6 V x (1 + 100k/22k) = 3.33 V
    _c("C1", *C10U), _c("C2", *C10U), _c("C3", *C10U),
    _c("C4", *C100N),
    _c("C5", *C100N_0402), _c("C6", *C100N_0402), _c("C7", *C100N_0402), _c("C8", *C100N_0402),
    _c("C9", "22uF", "C45783", "CL21A226MAQNNNE", "0805"),
    _c("C10", *C100N),                          # U7 decoupling
    _c("C11", "1uF", "C52923", "CL05A105KA5NQNC", "0402"),   # USB VBUS input (small: limits hot-plug ringing)
    dict(ref="D1", lib="Device:LED", value="PWR", fp="LED_SMD:LED_0603_1608Metric", side="top",
         mpn="19-217/G7C-AN1P2/6T", lcsc="C2986030"),
    dict(ref="D2", lib="Device:LED", value="FAULT", fp="LED_SMD:LED_0603_1608Metric", side="top",
         mpn="KT-0603R", lcsc="C2286"),
    # solder jumpers
    _jp("JP1", "2_Open", "I2C1 SDA"), _jp("JP2", "2_Open", "I2C1 SCL"),
    _jp("JP3", "3_Bridged123", "Pull-ups"),
    _jp("JP4", "3_Bridged12", "ADDR GND/VDD", side="bottom"),
    _jp("JP5", "2_Open", "ADDR SCL", side="bottom"), _jp("JP6", "2_Open", "ADDR SDA", side="bottom"),
    _jp("JP7", "2_Open", "ALERT G26"),
    _jp("JP8", "2_Bridged", "Host pull-ups", side="bottom"),
    _jp("JP9", "2_Open", "G7 power off (no SPI)", side="bottom"),
    # test pads
    # (v0.3's TP3-TP6 are gone: every socket already exposes 3V3, GND, SDA and SCL)
    _tp("TP1", "+5V"), _tp("TP2", "+5V_SW"),
]

# pin -> net per part (schematic and PCB use the same table). None = not connected.
NETS = {
    "J1": {p: n for p, (_, n) in HOST_PINS.items()},
    **{q: dict(QWIIC_NETS) for q in QWIIC},
    **{ref: {i + 1: n for i, n in enumerate(nets)} for ref, (_, _, nets) in SOCKETS.items()},
    # TPS2553: 1 IN, 2 GND, 3 EN (active high), 4 FAULT (open drain), 5 ILIM, 6 OUT
    "U1": {1: "+5V", 2: "GND", 3: "EN", 4: "FAULT", 5: "ILIM", 6: "U1_OUT"},
    "D3": {1: "+5V_SW", 2: "U1_OUT"},       # Device:D_Schottky pin 1 = K, pin 2 = A
    # USB-C input: VBUS -> TVS, 1 uF -> U6 -> +5V_SW. U6 shares U1's EN (host 5V on and not
    # switched off). Its FAULT pin is left open: the FAULT LED watches the host path (U1) only
    "J13": {"A1": "GND", "B1": "GND", "A12": "GND", "B12": "GND", "SH": "GND",
            "A4": "VBUS", "B4": "VBUS", "A9": "VBUS", "B9": "VBUS", "A5": "CC1", "B5": "CC2",
            "A6": None, "B6": None, "A7": None, "B7": None, "A8": None, "B8": None},
    "R22": {1: "CC1", 2: "GND"}, "R23": {1: "CC2", 2: "GND"},
    "D4": {1: "VBUS", 2: "GND"},            # Device:D_Zener pin 1 = K
    "C11": {1: "VBUS", 2: "GND"},
    "U6": {1: "VBUS", 2: "GND", 3: "EN", 4: None, 5: "ILIM2", 6: "+5V_SW"},
    "R24": {1: "ILIM2", 2: "GND"},
    # EN: R15 holds it high (on whenever 5VOUT is); Q3 pulls it low when G7 drives its gate through JP9. R16 keeps
    # Q3 off when JP9 is open or G7 floats, and holds the gate below Q3's threshold against the Pi's default pull-up on
    # G7 (about 50k: 3.3 V x 10k / 60k = 0.55 V, under the BSS138's 0.85 V minimum V_GS(th)). G = 1, S = 2, D = 3.
    "R15": {1: "+5V", 2: "EN"},
    "Q3": {1: "PWR_OFF", 2: "GND", 3: "EN"},
    "R16": {1: "PWR_OFF", 2: "GND"},
    "JP9": {1: "G7_HOST", 2: "PWR_OFF"},
    # FAULT LED from the unswitched 5 V: lights while U1 limits current or is in thermal shutdown (about 3 mA)
    "R14": {1: "+5V", 2: "FLT_A"},
    "D2": {1: "FAULT", 2: "FLT_A"},        # Device:LED pin 1 = K, pin 2 = A
    "R1": {1: "ILIM", 2: "GND"},
    "C1": {1: "+5V", 2: "GND"},
    "C2": {1: "+5V_SW", 2: "GND"},          # buck input
    # TLV62569: 1 EN, 2 GND, 3 SW, 4 VIN, 5 FB. EN on the shared EN, so the buck stops with the switches
    "U2": {1: "EN", 2: "GND", 3: "SW", 4: "+5V_SW", 5: "FB"},
    "L1": {1: "SW", 2: "+3V3"},
    "R25": {1: "+3V3", 2: "FB"}, "R26": {1: "FB", 2: "GND"},
    "C3": {1: "+3V3", 2: "GND"},
    "C9": {1: "+3V3", 2: "GND"},        # bulk: rides out hot-plugged Qwiic boards' input capacitance
    "C4": {1: "+3V3", 2: "GND"},        # ADS1115 VDD decoupling, at the pin
    "R9": {1: "+3V3", 2: "LED_A"},
    "D1": {1: "GND", 2: "LED_A"},          # Device:LED pin 1 = K, pin 2 = A
    # I2C isolation: BSS138 pass gates, gate on BreadPortZero's 3.3 V. Unpowered, they open and the body diodes face
    # the host, so the external bus can't load the host's pins. G = 1, S = 2, D = 3.
    "Q1": {1: "+3V3", 2: "SDA", 3: "SDA_HOST"},
    "Q2": {1: "+3V3", 2: "SCL", 3: "SCL_HOST"},
    # JP1/JP2 (open): bridge to put BreadPortZero's bus on the host's I2C1 instead of G22/G23
    "JP1": {1: "SDA_HOST", 2: "I2C1_SDA"},
    "JP2": {1: "I2C1_SCL", 2: "SCL_HOST"},
    # Host-side pull-ups for G22/G23 (Q1/Q2 need pull-ups on both sides). Cut JP8 when JP1/JP2 are bridged: I2C1
    # has its own, and these would load it through BreadPortZero's unpowered 3.3 V rail.
    "R12": {1: "SDA_HOST", 2: "PU_HOST"},
    "R13": {1: "SCL_HOST", 2: "PU_HOST"},
    "JP8": {1: "PU_HOST", 2: "+3V3"},
    # Pull-ups, cut JP3 to remove them. Needed: Q1/Q2 stop conducting at ~3.3 V - VGS(th), below 0.7 x 3.3 V.
    "R2": {1: "PU_SDA", 2: "SDA"},
    "R3": {1: "PU_SCL", 2: "SCL"},
    "JP3": {1: "PU_SDA", 2: "+3V3", 3: "PU_SCL"},
    # ESD protection for the user-facing bus, next to Q1/Q2 (1/6 and 3/4 are joined inside)
    "U4": {1: "SDA", 2: "GND", 3: "SCL", 4: "SCL", 5: "+3V3", 6: "SDA"},
    # UART: 4.7k series resistors limit current into or out of an unpowered or 5 V device to < 0.6 mA
    "R10": {1: "TX_HOST", 2: "TX"},
    "R11": {1: "RX_HOST", 2: "RX"},
    # ESD protection for the UART socket, on the socket side of R10/R11 (flow-through: 6 -> 1 TX, 4 -> 3 RX)
    "U5": {1: "TX", 2: "GND", 3: "RX", 4: "RX", 5: "+3V3", 6: "TX"},
    # SPI buffer, 74LVC125: 1OE/2OE/3OE tied low (always on), so SCK, MOSI and CS follow the host while the cap is
    # powered; 4OE is CS itself, so MISO only drives the host's G9 while the socket's device is selected. Unpowered,
    # Ioff leaves both sides floating: the display's bus doesn't see the socket.
    "U7": {1: "GND", 2: "SCK_HOST", 3: "SCK", 4: "GND", 5: "MOSI_HOST", 6: "MOSI", 7: "GND",
           8: "CS", 9: "G7_HOST", 10: "GND", 11: "MISO_HOST", 12: "MISO", 13: "G7_HOST", 14: "+3V3"},
    "C10": {1: "+3V3", 2: "GND"},
    "R21": {1: "+3V3", 2: "MISO"},
    # ESD protection for the SPI socket (Device:D_Zener pin 1 = K on the line, pin 2 = A to GND)
    "D5": {1: "SCK", 2: "GND"}, "D6": {1: "MOSI", 2: "GND"}, "D7": {1: "CS", 2: "GND"}, "D8": {1: "MISO", 2: "GND"},
    # ADS1115: 1 ADDR, 2 ALERT/RDY, 3 GND, 4-7 AIN0-3, 8 VDD, 9 SDA, 10 SCL.
    # ADDR: JP4 1-2 (as made) = GND, 0x48; JP4 2-3 = VDD, 0x49; JP4 cut + JP6 = SDA, 0x4A; JP4 cut + JP5 = SCL, 0x4B
    "U3": {1: "ADDR", 2: "ALERT", 3: "GND", 4: "AIN0", 5: "AIN1", 6: "AIN2", 7: "AIN3", 8: "+3V3", 9: "SDA", 10: "SCL"},
    "JP4": {1: "GND", 2: "ADDR", 3: "+3V3"},
    "JP5": {1: "ADDR", 2: "SCL"},
    "JP6": {1: "ADDR", 2: "SDA"},
    "JP7": {1: "ALERT", 2: "ALERT_HOST"},   # open: bridge to bring ALERT/RDY to G26 (use the host's pull-up)
    # input protection and anti-alias filter: 1k + 100 nF = 1.6 kHz
    "R5": {1: "AIN0", 2: "A0"}, "R6": {1: "AIN1", 2: "A1"}, "R7": {1: "AIN2", 2: "A2"}, "R8": {1: "AIN3", 2: "A3"},
    "C5": {1: "AIN0", 2: "GND"}, "C6": {1: "AIN1", 2: "GND"}, "C7": {1: "AIN2", 2: "GND"}, "C8": {1: "AIN3", 2: "GND"},
    # divider pads (unfitted): with R5-R8 = 1k, a 1.8k here scales 0-5 V to 0-3.2 V
    "R17": {1: "AIN0", 2: "GND"}, "R18": {1: "AIN1", 2: "GND"}, "R19": {1: "AIN2", 2: "GND"},
    "R20": {1: "AIN3", 2: "GND"},
    "TP1": {1: "+5V"}, "TP2": {1: "+5V_SW"},
}

POWER_NETS = ("+5V", "+3V3", "GND")   # these use KiCad power symbols; everything else is a local label


def pcb_net(n):
    """Net name as KiCad gives it in the PCB: local schematic labels get a '/' prefix."""
    return n if n in POWER_NETS else "/" + n

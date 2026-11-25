"""BreadPort design data, shared by gen_schematic.py and gen_pcb.py.

Coordinates for the PCB are in mm in a top view of the board (the side facing away from the CardputerZero), x to
the right, y down, origin at the top-left board corner. y = 0 is the edge on the keyboard side of the CardputerZero.
"""
import os
from pathlib import Path

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
    1: ("G26", None), 2: ("5VIN", None), 3: ("G23", None), 4: ("GND", "GND"), 5: ("G22", None),
    6: ("5VOUT", "+5V"), 7: ("G11 SPI CLK", None), 8: ("G2 I2C1 SDA", "SDA_HOST"), 9: ("G10 SPI MOSI", None),
    10: ("G3 I2C1 SCL", "SCL_HOST"), 11: ("G9 SPI MISO", None), 12: ("G14 UART TX", "TX_HOST"),
    13: ("G7 SPI CS1", None), 14: ("G15 UART RX", "RX_HOST"),
}

# --- breadboard socket row along the back edge ----------------------------------------------------------------
P = 2.54
SOCKET_Y = 21.6
# pin-1 x of each socket. The UART socket sits one pitch right of J1's TX/RX pins so TX/RX jog right once.
SOCKETS = {   # ref: (pin-1 x, pin names, nets)
    "J6":  (7.71,  ["GND", "3V3", "SDA", "SCL"], ["GND", "+3V3", "SDA", "SCL"]),
    "J7":  (19.14, ["GND", "3V3", "SDA", "SCL"], ["GND", "+3V3", "SDA", "SCL"]),
    "J8":  (30.57, ["GND", "3V3", "SDA", "SCL"], ["GND", "+3V3", "SDA", "SCL"]),
    "J10": (44.54, ["GND", "3V3", "TX", "RX"], ["GND", "+3V3", "TX", "RX"]),
    "J11": (55.97, ["GND", "3V3", "A0", "A1", "A2", "A3"], ["GND", "+3V3", "A0", "A1", "A2", "A3"]),
    "J9":  (73.75, ["GND", "3V3", "SDA", "SCL"], ["GND", "+3V3", "SDA", "SCL"]),
}
# STEMMA QT / Qwiic connector above each I2C socket (pin 1 GND, 2 3V3, 3 SDA, 4 SCL), centred on it
QWIIC_Y = 12.0
QWIIC = {"J2": "J6", "J3": "J7", "J4": "J8", "J5": "J9"}
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


def _r(ref, value, lcsc, mpn):
    return dict(ref=ref, lib="Device:R", value=value, fp="Resistor_SMD:R_0603_1608Metric", side="top",
                mpn=mpn, lcsc=lcsc)


R1K = ("1k", "C21190", "0603WAF1001T5E")

# Parts. lcsc numbers are used for the JLCPCB BOM; mpn for PCBWay.
PARTS = [
    dict(ref="J1", lib="Connector_Generic:Conn_02x07_Odd_Even", value="CardputerZero EXT 2x7",
         fp="Connector_PinHeader_2.54mm:PinHeader_2x07_P2.54mm_Vertical", side="bottom",
         mpn="BOOMELE 2.54-2*7P", lcsc="C71397"),
    _qwiic("J2", 1), _qwiic("J3", 2), _qwiic("J4", 3), _qwiic("J5", 4),
    _socket("J6", "I2C 1"), _socket("J7", "I2C 2"), _socket("J8", "I2C 3"), _socket("J9", "I2C 4"),
    _socket("J10", "UART"), _socket("J11", "Analog"),
    dict(ref="U1", lib="BreadPort:SY6280AAC", value="SY6280AAC", fp="Package_TO_SOT_SMD:SOT-23-5", side="top",
         mpn="SY6280AAC", lcsc="C55136"),
    dict(ref="U2", lib="Regulator_Linear:AP2112K-3.3", value="AP2112K-3.3", fp="Package_TO_SOT_SMD:SOT-23-5",
         side="top", mpn="AP2112K-3.3TRG1", lcsc="C51118"),
    dict(ref="U3", lib="Analog_ADC:ADS1115IDGS", value="ADS1115", fp="Package_SO:MSOP-10_3x3mm_P0.5mm",
         side="top", mpn="ADS1115IDGSR", lcsc="C37593"),
    dict(ref="Q1", lib="Transistor_FET:BSS138", value="BSS138", fp="Package_TO_SOT_SMD:SOT-23", side="top",
         mpn="BSS138LT1G", lcsc="C82045"),
    dict(ref="Q2", lib="Transistor_FET:BSS138", value="BSS138", fp="Package_TO_SOT_SMD:SOT-23", side="top",
         mpn="BSS138LT1G", lcsc="C82045"),
    _r("R1", "18k", "C25810", "0603WAF1802T5E"),
    _r("R2", "4.7k", "C23162", "0603WAF4701T5E"), _r("R3", "4.7k", "C23162", "0603WAF4701T5E"),
    _r("R5", *R1K), _r("R6", *R1K), _r("R7", *R1K), _r("R8", *R1K), _r("R9", *R1K), _r("R10", *R1K),
    _r("R11", *R1K),
    dict(ref="C1", lib="Device:C", value="10uF", fp="Capacitor_SMD:C_0805_2012Metric", side="top",
         mpn="CL21A106KAYNNNE", lcsc="C15850"),
    dict(ref="C2", lib="Device:C", value="10uF", fp="Capacitor_SMD:C_0805_2012Metric", side="top",
         mpn="CL21A106KAYNNNE", lcsc="C15850"),
    dict(ref="C3", lib="Device:C", value="10uF", fp="Capacitor_SMD:C_0805_2012Metric", side="top",
         mpn="CL21A106KAYNNNE", lcsc="C15850"),
    dict(ref="C4", lib="Device:C", value="100nF", fp="Capacitor_SMD:C_0603_1608Metric", side="top",
         mpn="CC0603KRX7R9BB104", lcsc="C14663"),
    dict(ref="D1", lib="Device:LED", value="PWR", fp="LED_SMD:LED_0603_1608Metric", side="top",
         mpn="19-217/G7C-AN1P2/6T", lcsc="C2986030"),
]

# pin -> net per part (schematic and PCB use the same table). None = not connected.
NETS = {
    "J1": {p: n for p, (_, n) in HOST_PINS.items()},
    **{q: dict(QWIIC_NETS) for q in QWIIC},
    **{ref: {i + 1: n for i, n in enumerate(nets)} for ref, (_, _, nets) in SOCKETS.items()},
    # SY6280AAC: 1 OUT, 2 GND, 3 ISET, 4 EN, 5 IN. Current limit = 6800 / R_SET = 378 mA with 18k.
    "U1": {1: "+5V_SW", 2: "GND", 3: "ISET", 4: "+5V", 5: "+5V"},
    "R1": {1: "ISET", 2: "GND"},
    "C1": {1: "+5V", 2: "GND"},
    "C2": {1: "+5V_SW", 2: "GND"},
    # AP2112K: 1 VIN, 2 GND, 3 EN, 4 NC, 5 VOUT
    "U2": {1: "+5V_SW", 2: "GND", 3: "+5V_SW", 4: None, 5: "+3V3"},
    "C3": {1: "+3V3", 2: "GND"},
    "C4": {1: "+3V3", 2: "GND"},
    "R9": {1: "+3V3", 2: "LED_A"},
    "D1": {1: "GND", 2: "LED_A"},          # Device:LED pin 1 = K, pin 2 = A
    # I2C isolation: BSS138 pass gates, gate on BreadPort's 3.3 V. Unpowered, they open and the body diodes face
    # the host, so the external bus can't load the host's I2C1 (keyboard, RTC, IMU). G = 1, S = 2, D = 3.
    "Q1": {1: "+3V3", 2: "SDA", 3: "SDA_HOST"},
    "Q2": {1: "+3V3", 2: "SCL", 3: "SCL_HOST"},
    "R2": {1: "+3V3", 2: "SDA"},
    "R3": {1: "+3V3", 2: "SCL"},
    # UART: 1k series resistors limit current into an unpowered or 5 V device
    "R10": {1: "TX_HOST", 2: "TX"},
    "R11": {1: "RX_HOST", 2: "RX"},
    # ADS1115: 1 ADDR, 2 ALERT/RDY, 3 GND, 4-7 AIN0-3, 8 VDD, 9 SDA, 10 SCL. ADDR = GND -> address 0x48.
    "U3": {1: "GND", 2: None, 3: "GND", 4: "AIN0", 5: "AIN1", 6: "AIN2", 7: "AIN3", 8: "+3V3", 9: "SDA", 10: "SCL"},
    "R5": {1: "AIN0", 2: "A0"}, "R6": {1: "AIN1", 2: "A1"}, "R7": {1: "AIN2", 2: "A2"}, "R8": {1: "AIN3", 2: "A3"},
}

POWER_NETS = ("+5V", "+3V3", "GND")   # these use KiCad power symbols; everything else is a local label


def pcb_net(n):
    """Net name as KiCad gives it in the PCB: local schematic labels get a '/' prefix."""
    return n if n in POWER_NETS else "/" + n

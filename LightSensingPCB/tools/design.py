"""LightSensingPCB design data, shared by gen_schematic.py and gen_pcb.py.

Coordinates for the PCB are in mm in a top view of the board (the side facing away from the CardputerZero), x to
the right, y down, origin at the top-left board corner. y = 0 is the edge on the keyboard side of the CardputerZero.
"""

BOARD_W, BOARD_H = 84.0, 24.0   # same footprint as M5Stack's Cap modules (Cap LoRa-1262 drawing: 84.0 x 24.0 mm)
BOARD_THICKNESS = 1.6

# CardputerZero top expansion port (EXT 2.54-14P, female on the host), from M5Stack's port photo
# (docs.m5stack.com/en/CardputerZero, cap interface image) and the Cap LoRa-1262 drawing:
# odd row on the keyboard side, pin 1 (G26) on the left when looking down at the top edge.
J1_X0 = BOARD_W / 2 - 3 * 2.54          # pin 1 / pin 2 column; header centred along the 84 mm edge
J1_ODD_Y, J1_EVEN_Y = 3.63, 6.17        # rows 3.6 / 6.2 mm from the keyboard-side edge (Cap drawing)

HOST_PINS = {
    1: ("G26", "SENSOR_GPIO"), 2: ("5VIN", None), 3: ("G23", None), 4: ("GND", "GND"), 5: ("G22", "SENSOR_INT"),
    6: ("5VOUT", "+5V"), 7: ("G11 SPI CLK", None), 8: ("G2 I2C1 SDA", "SDA"), 9: ("G10 SPI MOSI", None),
    10: ("G3 I2C1 SCL", "SCL"), 11: ("G9 SPI MISO", None), 12: ("G14 UART TX", None), 13: ("G7 SPI CS1", None),
    14: ("G15 UART RX", None),
}

# Adafruit AS7343 breakout (product 6477) 6-pin header: VIN, GND, SCL, SDA, GPIO, INT (fab print), 0.1 in pitch.
J2_Y = 20.0
J2_X0 = BOARD_W / 2 - 2.5 * 2.54       # pin 1 (VIN); breakout centred on the board
BREAKOUT_PINS = {1: ("VIN", "+3V3"), 2: ("GND", "GND"), 3: ("SCL", "SCL"), 4: ("SDA", "SDA"),
                 5: ("GPIO", "SENSOR_GPIO"), 6: ("INT", "SENSOR_INT")}
# The breakout's own holes (0.80 x 0.50 in rectangle, lower pair in line with the header) are not used: standoffs
# there would hit J1's pins and J2's socket. The socket holds the breakout.

# Parts. lcsc numbers are used for the JLCPCB BOM; mpn for PCBWay.
PARTS = [
    dict(ref="J1", lib="Connector_Generic:Conn_02x07_Odd_Even", value="CardputerZero EXT 2x7",
         fp="Connector_PinHeader_2.54mm:PinHeader_2x07_P2.54mm_Vertical", side="bottom",
         mpn="BOOMELE 2.54-2*7P", lcsc="C71397",
         note="Mounted on the bottom; mates with the CardputerZero's female EXT 2.54-14P port"),
    dict(ref="J2", lib="Connector_Generic:Conn_01x06", value="AS7343 breakout",
         fp="Connector_PinSocket_2.54mm:PinSocket_1x06_P2.54mm_Vertical", side="top",
         mpn="BOOMELE 2.54-1*6P", lcsc="C40877",
         note="Takes the Adafruit 6477 breakout on its 6-pin header"),
    dict(ref="U1", lib="Regulator_Linear:AP2112K-3.3", value="AP2112K-3.3",
         fp="Package_TO_SOT_SMD:SOT-23-5", side="top", mpn="AP2112K-3.3TRG1", lcsc="C51118",
         note="5 V to 3.3 V, 600 mA; the port has no 3.3 V pin"),
    dict(ref="C1", lib="Device:C", value="10uF", fp="Capacitor_SMD:C_0805_2012Metric", side="top",
         mpn="CL21A106KAYNNNE", lcsc="C15850", note="LDO input"),
    dict(ref="C2", lib="Device:C", value="10uF", fp="Capacitor_SMD:C_0805_2012Metric", side="top",
         mpn="CL21A106KAYNNNE", lcsc="C15850", note="LDO output"),
]

# pin -> net per part (schematic and PCB use the same table)
NETS = {
    "J1": {p: n for p, (_, n) in HOST_PINS.items()},
    "J2": {p: n for p, (_, n) in BREAKOUT_PINS.items()},
    "U1": {1: "+5V", 2: "GND", 3: "+5V", 4: None, 5: "+3V3"},
    "C1": {1: "+5V", 2: "GND"},
    "C2": {1: "+3V3", 2: "GND"},
}

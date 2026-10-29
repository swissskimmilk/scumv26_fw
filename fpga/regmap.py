"""The FPGA's register map: the one definition the RTL and the host share.

    python fpga/regmap.py        rewrite fpga/rtl/scum_regs_pkg.sv and fpga/REGISTERS.md

host/scum.py imports this module, so the PC side always uses the same addresses and bit
positions as the gateware it talks to. Never edit the generated files by hand.

Addresses are byte addresses of 32-bit registers. The top byte of the 16-bit address picks the
block (0x00 system, 0x01 power, 0x02 clock counter, 0x03 DUT I/O); each block decodes its own
low byte. Access: rw = read/write, ro = read only, w1c = write 1 to clear, wo = write only
(reads 0).
"""
from pathlib import Path

ID_MAGIC = 0x5343554D  # "SCUM"
VERSION = 0x0001_0000  # major.minor.patch as 8.8.16 bits

FEATURES = 0  # FEATURES register bits; set each as its block lands

SYS_HZ = 100_000_000  # the system clock: the 100 MHz oscillator, no MMCM

# The DUT's control pins, in DUT_* register bit order. (name, driven by the FPGA at reset,
# reset level). Every pad is bidirectional, so the host can turn any of them around through
# DUT_OE; the reset directions are the DUT's natural ones (its inputs driven, its outputs read).
DUT_PINS = [
    ("CORE_RST", True, 1),  # idles high, matching the fitted pull-up; polarity unconfirmed
    ("BOOT_SEL", True, 0),
    ("CORE_GPIO", False, 0),
    ("CLK_DEBUG_MUX", True, 0),
    ("SCAN_CLK", True, 0),
    ("SCAN_IN", True, 0),
    ("SCAN_EN", True, 0),
    ("SCAN_SEL", True, 0),
    ("SCAN_RST", True, 0),
    ("SCAN_OUT", False, 0),
    ("TL_CLK", True, 0),
    ("TL_VAL_IN", True, 0),
    ("TL_DAT_IN", True, 0),
    ("RDY_OUT", True, 0),
    ("TL_VAL_OUT", False, 0),
    ("TL_DAT_OUT", False, 0),
    ("RDY_IN", False, 0),
    ("BASEBAND_TX", False, 0),
    ("BASEBAND_RX", False, 0),
    ("NFC_DEMOD_MCU", False, 0),
    ("NFC_MDM_DBG", False, 0),
]
N_DUT = len(DUT_PINS)
DUT_OE_RESET = sum(1 << i for i, (_, out, _) in enumerate(DUT_PINS) if out)
DUT_OUT_RESET = sum(lvl << i for i, (_, _, lvl) in enumerate(DUT_PINS))

# (name, address, access, reset, description, {field: (lsb, width, description)})
REGS = [
    # ---- system ----
    ("ID", 0x0000, "ro", ID_MAGIC, "constant 0x5343554D (\"SCUM\")", {}),
    ("VERSION", 0x0004, "ro", VERSION, "gateware version, major[31:24] minor[23:16] patch[15:0]", {}),
    ("SCRATCH", 0x0008, "rw", 0, "free for the host; a link test", {}),
    ("UPTIME_MS", 0x000C, "ro", 0, "milliseconds since configuration (wraps after 49 days)", {}),
    ("EVENTS", 0x0010, "w1c", 0, "sticky event flags; also counted in the *_COUNT registers", {
        "TRIP": (0, 1, "an INA237 ALERT dropped the DUT rails"),
        "BUTTON": (1, 1, "the DUT reset button was pressed"),
        "FRAME": (2, 1, "a host packet timed out half way or started with an unknown byte"),
    }),
    ("TRIP_COUNT", 0x0014, "ro", 0, "over-current trips since configuration", {}),
    ("BUTTON_COUNT", 0x0018, "ro", 0, "DUT reset button presses since configuration", {}),
    ("FRAME_COUNT", 0x001C, "ro", 0, "host framing errors since configuration", {}),
    ("FEATURES", 0x0020, "ro", FEATURES, "blocks built into this gateware; the host greys out the rest", {
        "I2C": (0, 1, "telemetry I2C master: INA237 monitors, TLA2528, VBAT DAC"),
        "ADC": (1, 1, "ADC3910 capture and its SPI"),
        "UART": (2, 1, "DUT UART bridge"),
    }),
    # ---- power ----
    ("RAIL_EN", 0x0100, "rw", 0, "DUT regulator enables; writes are ignored while TRIP_STATUS.TRIPPED", {
        "VIO": (0, 1, "VIO_EN: the DUT's I/O supply (sequence it first)"),
        "VBAT": (1, 1, "VBAT_EN: VBAT and VBAT_RF"),
        "VDD": (2, 1, "VDD_EN: the 1 V rails VDDD, VDDA, RF_VDD"),
    }),
    ("RAIL_SW", 0x0104, "rw", 0, "rail-source switches: 1 = board regulator, 0 = the DUT's own LDO", {
        "VDDD": (0, 1, "VDDD_SW"),
        "VDDA": (1, 1, "VDDA_SW"),
        "RF": (2, 1, "RF_VDD_SW (0 = fed from the DUT's RF LDO output, G9)"),
    }),
    ("TRIP_CTRL", 0x0108, "rw", 0, "hardware over-current trip on TELEM_ALERT", {
        "EN": (0, 1, "trip enabled; set it after the INA237 limits are programmed"),
        "ACTIVE_HIGH": (1, 1, "ALERT polarity: 0 = active low (INA237 default, open drain)"),
    }),
    ("TRIP_STATUS", 0x010C, "w1c", 0, "trip state", {
        "TRIPPED": (0, 1, "latched by a trip; rails stay off until it is cleared"),
        "ALERT": (1, 1, "ro: TELEM_ALERT is asserted right now"),
    }),
    # ---- DUT clock counter ----
    ("CLK_CTRL", 0x0200, "rw", 0, "LMK1D1204 input select; writing restarts the count", {
        "SEL": (0, 2, "0 = off (IN_SEL released, buffer outputs off), 1 = dig (IN0), 2 = ana (IN1)"),
    }),
    ("CLK_WINDOW", 0x0204, "rw", SYS_HZ // 10,
     "counting window in system clock cycles (default 100 ms); writing restarts the count", {}),
    ("CLK_COUNT", 0x0208, "ro", 0, "DUT clock edges in the last complete window", {}),
    ("CLK_SEQ", 0x020C, "ro", 0, "windows completed since the last restart", {}),
    # ---- DUT I/O ----
    ("DUT_OE", 0x0300, "rw", DUT_OE_RESET,
     "1 = the FPGA drives the pin; every pad is released while RAIL_EN.VIO is off", {}),
    ("DUT_OUT", 0x0304, "rw", DUT_OUT_RESET, "levels driven where DUT_OE is set", {}),
    ("DUT_IN", 0x0308, "ro", 0, "pad levels (synchronised; reads the driven level on outputs)", {}),
    ("DUT_PULSE", 0x030C, "rw", 0, "write: invert one output for a time, then restore it", {
        "US": (0, 24, "pulse width in microseconds, 1-16777215"),
        "PIN": (24, 5, "pin index (bit position in DUT_OUT)"),
        "BUSY": (31, 1, "ro: a pulse is in progress (a write while busy is ignored)"),
    }),
    ("BUTTON_US", 0x0310, "rw", 1000,
     "CORE_RST pulse width for the reset button, us; 0 = button only counted", {}),
]

# Host packet bytes (fpga/rtl/host_bridge.sv).
CMD_WRITE = 0x57  # 'W' a0 a1 d0 d1 d2 d3  -> 'K'
CMD_READ = 0x52   # 'R' a0 a1              -> d0 d1 d2 d3
CMD_BURST = 0x42  # 'B' a0 a1 n0 n1 n2 n3  -> n words read from one address
ACK = 0x4B        # 'K'


def _sv():
    out = ["// Generated by fpga/regmap.py -- do not edit.", "package scum_regs_pkg;", ""]
    out.append(f"  localparam int unsigned SYS_HZ = {SYS_HZ};")
    out.append(f"  localparam logic [31:0] ID_MAGIC = 32'h{ID_MAGIC:08X};")
    out.append(f"  localparam logic [31:0] VERSION = 32'h{VERSION:08X};")
    out.append(f"  localparam int unsigned N_DUT = {N_DUT};")
    out.append(f"  localparam logic [N_DUT-1:0] DUT_OE_RESET = {N_DUT}'h{DUT_OE_RESET:X};")
    out.append(f"  localparam logic [N_DUT-1:0] DUT_OUT_RESET = {N_DUT}'h{DUT_OUT_RESET:X};")
    out.append("")
    for i, (name, *_ ) in enumerate(DUT_PINS):
        out.append(f"  localparam int unsigned PIN_{name} = {i};")
    out.append("")
    for name, addr, acc, reset, desc, fields in REGS:
        out.append(f"  localparam logic [15:0] A_{name} = 16'h{addr:04X};  // {acc}: {desc}")
        out.append(f"  localparam logic [31:0] R_{name} = 32'h{reset:08X};")
        for f, (lsb, w, _) in fields.items():
            out.append(f"  localparam int unsigned {name}_{f} = {lsb};" + (f"  // width {w}" if w > 1 else ""))
    out.append("")
    for n, v in (("CMD_WRITE", CMD_WRITE), ("CMD_READ", CMD_READ), ("CMD_BURST", CMD_BURST), ("ACK", ACK)):
        out.append(f"  localparam logic [7:0] {n} = 8'h{v:02X};")
    out += ["", "endpackage", ""]
    return "\n".join(out)


def _md():
    out = ["# FPGA registers", "", "Generated by `fpga/regmap.py` -- do not edit.", "",
           "| Address | Name | Access | Reset | Description |", "|---|---|---|---|---|"]
    for name, addr, acc, reset, desc, fields in REGS:
        out.append(f"| 0x{addr:04X} | {name} | {acc} | 0x{reset:X} | {desc} |")
        for f, (lsb, w, d) in fields.items():
            bits = f"[{lsb}]" if w == 1 else f"[{lsb + w - 1}:{lsb}]"
            out.append(f"| | `{bits}` {f} | | | {d} |")
    out += ["", "## DUT pin bits", "", "| Bit | Pin | Driven at reset | Reset level |", "|---|---|---|---|"]
    for i, (n, o, lvl) in enumerate(DUT_PINS):
        out.append(f"| {i} | {n} | {'yes' if o else 'no'} | {lvl} |")
    out += ["", "## Host packets (FT2232H channel B, async 245 FIFO)", "",
            "| Send | Reply |", "|---|---|",
            "| `W` a0 a1 d0 d1 d2 d3 | `K` |",
            "| `R` a0 a1 | d0 d1 d2 d3 |",
            "| `B` a0 a1 n0 n1 n2 n3 | n words, each d0..d3, all read from address a |",
            "", "Little-endian throughout. A packet that stalls for more than 1 ms half way is dropped",
            "and counted in FRAME_COUNT, as is any byte that does not start a packet; the host",
            "resynchronises by waiting 2 ms and sending the next packet.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    (here / "rtl" / "scum_regs_pkg.sv").write_text(_sv(), newline="\n")
    (here / "REGISTERS.md").write_text(_md(), newline="\n")
    print("wrote rtl/scum_regs_pkg.sv and REGISTERS.md")

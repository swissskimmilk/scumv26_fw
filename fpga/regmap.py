"""The FPGA's register map: the one definition the RTL and the host share.

    python fpga/regmap.py        rewrite fpga/rtl/scum_regs_pkg.sv and fpga/REGISTERS.md

host/scum.py imports this module, so the PC side always uses the same addresses and bit
positions as the gateware it talks to. Never edit the generated files by hand.

Addresses are byte addresses of 32-bit registers. The top byte of the 16-bit address picks the
block (0x00 system, 0x01 power, 0x02 clock counter, 0x03 DUT I/O, 0x04 header GPIO, 0x05
telemetry I2C, 0x06 ADC, 0x07 serial TileLink, 0x08 DUT UART); each block decodes its own low byte. Access: rw = read/write, ro = read only, w1c = write 1 to
clear, wo = write only (reads 0).
"""
from pathlib import Path

ID_MAGIC = 0x5343554D  # "SCUM"
VERSION = 0x0005_0000  # major.minor.patch as 8.8.16 bits

FEATURES = 0b1111  # FEATURES register bits; set each as its block lands

SYS_HZ = 100_000_000  # the system clock: the 100 MHz oscillator, no MMCM
I2C_DIV_100K = SYS_HZ // 400_000  # SCL quarter period for 100 kHz
I2C_TIMEOUT = SYS_HZ // 100       # 10 ms of SCL held low ends a transaction
ADC_SPI_HALF = 10                 # ADC SCLK half period in system clocks: 5 MHz (20 MHz max)
CAP_DEPTH = 65536                 # capture buffer, sample pairs (2 ms at 32 MS/s)
TL_FRAME_BITS = 164               # serial TileLink frame (SCuM-V24B's; TL_FRAME sets it per chip)
TL_FIFO = 512                     # serial TileLink FIFOs, 32-bit words each way
UART_DIV_115200 = round(SYS_HZ / 115_200)
UART_RX_FIFO = 2048               # DUT UART bytes
UART_TX_FIFO = 512

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
    ("TL_CLK", False, 0),   # the DUT drives the serial TileLink clock
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
        "TL": (3, 1, "serial TileLink engine"),
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
    ("RAIL_PG", 0x0110, "ro", 0, "regulator power-good pins (open drain, 100k to SYS_1V8; 1 = good)", {
        "VIO": (0, 1, "REG_VIO_PG (K2); DUT pads stay released until it is set"),
        "VBAT": (1, 1, "REG_VBAT_PG (L3)"),
        "VDDD": (2, 1, "REG_VDDD_PG (J1)"),
        "VDDA": (3, 1, "REG_VDDA_PG (H2)"),
        "RF": (4, 1, "REG_RF_VDD_PG (H1)"),
        "SYS_1V8": (5, 1, "REG_SYS_1V8_PG (N1)"),
        "SYS_3V3": (6, 1, "REG_SYS_3V3_PG (M1)"),
        "FPGA_1V8": (7, 1, "REG_FPGA_1V8_PG (N8)"),
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
    # ---- header GPIO (J16) ----
    ("GPIO_OE", 0x0400, "rw", 0, "FPGA_GPIO[5:0] on J16: 1 = the FPGA drives the pin", {}),
    ("GPIO_OUT", 0x0404, "rw", 0, "levels driven where GPIO_OE is set", {}),
    ("GPIO_IN", 0x0408, "ro", 0, "pin levels (synchronised)", {}),
    # ---- telemetry I2C master ----
    ("I2C_DIV", 0x0500, "rw", I2C_DIV_100K,
     "SCL quarter period in system clocks (250 = 100 kHz, 63 = 397 kHz; at least 4)", {}),
    ("I2C_CMD", 0x0504, "rw", 0, "write to run one transaction (ignored while busy): START, address+W, "
     "WLEN bytes of I2C_WDATA, then if RLEN a repeated START (STOP + START with SEP), address+R, "
     "RLEN bytes into I2C_RDATA, STOP. WLEN = RLEN = 0 sends the address alone (a probe)", {
        "ADDR": (0, 7, "7-bit device address"),
        "WLEN": (8, 3, "bytes to write, 0-4, from I2C_WDATA byte 0 up"),
        "RLEN": (12, 3, "bytes to read, 0-4, into I2C_RDATA byte 0 up; each ACKed but the last"),
        "SEP": (16, 1, "STOP + START between the write and the read instead of a repeated START"),
        "RECOVER": (24, 1, "instead: clock SCL until a stuck device releases SDA (at most 9), then STOP"),
    }),
    ("I2C_WDATA", 0x0508, "rw", 0, "bytes to write; byte 0 ([7:0]) goes first", {}),
    ("I2C_RDATA", 0x050C, "ro", 0, "bytes read by the last transaction; byte 0 ([7:0]) came first", {}),
    ("I2C_STATUS", 0x0510, "ro", 0, "transaction state; the flags clear when the next one starts", {
        "BUSY": (0, 1, "a transaction is running"),
        "ANACK": (1, 1, "the address was not acknowledged: no such device"),
        "DNACK": (2, 1, "a written byte was not acknowledged"),
        "TIMEOUT": (3, 1, "a device held SCL low for over 10 ms; the lines were released"),
        "STUCK": (4, 1, "SCL or SDA was low before START; nothing was sent (try RECOVER)"),
        "SCL": (8, 1, "the SCL line now"),
        "SDA": (9, 1, "the SDA line now"),
    }),
    # ---- ADC3910 ----
    ("ADC_CTRL", 0x0600, "rw", 0, "ADC3910 control pins", {
        "RESET": (0, 1, "drives ADC_RST high (the ADC's hardware reset is a high pulse)"),
    }),
    ("ADC_SPI", 0x0604, "rw", 0, "write to send one 24-bit SPI frame (ignored while busy); "
     "SCLK is SYS_HZ / (2 * ADC_SPI_HALF)", {
        "DATA": (0, 8, "byte to write (ignored on a read)"),
        "ADDR": (8, 15, "register address"),
        "READ": (23, 1, "1 = read: SDIO is released for the data byte"),
    }),
    ("ADC_SPI_STATUS", 0x0608, "ro", 0, "SPI state", {
        "BUSY": (0, 1, "a frame is being sent"),
        "RDATA": (8, 8, "the byte the last read returned"),
    }),
    ("ADC_DCLK_KHZ", 0x060C, "ro", 0, "ADC DCLK cycles in the last millisecond (0 = no DCLK)", {}),
    ("CAP_LEN", 0x0610, "rw", 4096, f"sample pairs per capture, 1-{CAP_DEPTH}", {}),
    ("CAP_CTRL", 0x0614, "rw", 0, "capture control", {
        "START": (0, 1, "write 1: capture CAP_LEN sample pairs into the buffer (ignored while busy)"),
    }),
    ("CAP_STATUS", 0x0618, "ro", 0, "capture state", {
        "BUSY": (0, 1, "a capture is filling the buffer (stays set if DCLK is missing)"),
        "DONE": (1, 1, "the buffer holds a complete capture"),
    }),
    ("CAP_ADDR", 0x061C, "rw", 0, "the buffer word CAP_DATA reads next", {}),
    ("CAP_DATA", 0x0620, "ro", 0, "one sample pair from the buffer, then CAP_ADDR + 1 (read it "
     "with a burst)", {
        "A": (0, 10, "channel A, two's complement (DCLK rising edge)"),
        "B": (16, 10, "channel B, two's complement (DCLK falling edge)"),
    }),
    # ---- serial TileLink ----
    ("TL_CTRL", 0x0700, "rw", 0, "serial TileLink engine", {
        "EN": (0, 1, "the engine owns the TileLink pads (TL_VAL_IN, TL_DAT_IN, RDY_OUT driven; "
                     "TL_CLK, TL_VAL_OUT, TL_DAT_OUT, RDY_IN read) whatever DUT_OE says; 0 = "
                     "engine and both FIFOs held empty"),
    }),
    ("TL_FRAME", 0x0704, "rw", TL_FRAME_BITS, "frame length in bits, 1-256, both ways; change it only "
     "while TL_CTRL.EN is off", {}),
    ("TL_TX", 0x0708, "wo", 0, "queue one 32-bit word of an outgoing frame (word 0 first; each word "
     "LSB first; the bits past the frame length in its last word are dropped). Dropped when full", {}),
    ("TL_RX", 0x070C, "ro", 0, "the next word of the incoming frames, packed the same way (0 when "
     "empty; read TL_STATUS.RX_WORDS of them with a burst)", {}),
    ("TL_STATUS", 0x0710, "ro", 0, "FIFO state", {
        "RX_WORDS": (0, 12, "received words waiting in TL_RX"),
        "TX_FREE": (16, 12, "free words in the TL_TX FIFO"),
        "TX_BUSY": (31, 1, "words still queued, or a frame part sent"),
    }),
    ("TL_CLK_KHZ", 0x0714, "ro", 0, "TL_CLK cycles in the last millisecond (0 = the DUT is not "
     "clocking the link)", {}),
    # ---- DUT UART ----
    ("UART_DIV", 0x0800, "rw", UART_DIV_115200, "system clocks per bit, 16-65535 (868 = 115200 baud); "
     "8N1 both ways", {}),
    ("UART_TX", 0x0804, "wo", 0, "queue one byte ([7:0]) for FPGA_UART_TX; dropped when full", {}),
    ("UART_RX", 0x0808, "ro", 0, "the next byte from FPGA_UART_RX (read them with a burst)", {
        "DATA": (0, 8, "the byte"),
        "VALID": (8, 1, "0 = nothing was waiting (DATA is 0)"),
    }),
    ("UART_STATUS", 0x080C, "ro", 0, "FIFO state", {
        "RX_COUNT": (0, 12, "received bytes waiting"),
        "TX_COUNT": (16, 12, "bytes still to send"),
    }),
    ("UART_EVENTS", 0x0810, "w1c", 0, "sticky receive errors", {
        "OVERRUN": (0, 1, "a byte arrived with the receive FIFO full and was lost"),
        "FRAMING": (1, 1, "a byte's stop bit was low (wrong baud, or a break)"),
    }),
]

# Host packet bytes (fpga/rtl/host_bridge.sv).
CMD_WRITE = 0x57  # 'W' a0 a1 d0 d1 d2 d3  -> 'K'
CMD_READ = 0x52   # 'R' a0 a1              -> d0 d1 d2 d3
CMD_BURST = 0x42  # 'B' a0 a1 n0 n1 n2 n3  -> n words read from one address
ACK = 0x4B        # 'K'


def _sv():
    out = ["// Generated by fpga/regmap.py -- do not edit.", "package scum_regs_pkg;", ""]
    out.append(f"  localparam int unsigned SYS_HZ = {SYS_HZ};")
    out.append(f"  localparam int unsigned I2C_TIMEOUT = {I2C_TIMEOUT};")
    out.append(f"  localparam int unsigned ADC_SPI_HALF = {ADC_SPI_HALF};")
    out.append(f"  localparam int unsigned CAP_DEPTH = {CAP_DEPTH};")
    out.append(f"  localparam int unsigned TL_FIFO = {TL_FIFO};")
    out.append(f"  localparam int unsigned UART_RX_FIFO = {UART_RX_FIFO};")
    out.append(f"  localparam int unsigned UART_TX_FIFO = {UART_TX_FIFO};")
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

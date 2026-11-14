"""Host side of the SCuMV26 bring-up board.

    python scum.py                 interactive prompt (type `help`)
    python scum.py power up        run one command and print the reply
    python scum.py --sim freq      talk to the simulated board instead (sim.py)
    python scum.py --direct ...    open the board even if the app is running
    SCUM_APP_PORT=<n>              the app's localhost command port (both sides; default 29563)

While the desktop app is connected it holds the board (channel B opens in one process only),
so the command line sends its commands through the app instead: they show in its console and
its panels follow. With no app connected, the command line opens the board itself.

As a library:

    from scum import Board
    b = Board()                    # the first FT2232H whose channel B is in 245 FIFO mode
    b.cmd("power up")
    b.clk(sel="dig"); b.freq()
    b.pin("BOOT_SEL", 1); b.pulse("CORE_RST", 1000)

The board is an FPGA (fpga/) with a register map behind the FT2232H's channel B; channel A is
Vivado's JTAG cable. The PC reads and writes registers and does the sequencing itself. `cmd()`
is a small text-command interpreter over the registers (`help` lists the commands); the GUI's
console, the CLI and scripts all use it, and the parsed methods below for structured results.

Blocks a gateware build does not have (FEATURES register) answer `ERR not in the gateware
yet`. The telemetry devices (INA237s, TLA2528, VBAT DAC) are driven from here, one I2C
transaction per register access, and so are the DUT's serial TileLink messages: the FPGA only
shifts frames (fpga/rtl/tl_serial.sv), and this file packs and unpacks them.

Addresses, bit positions and packet bytes come from fpga/regmap.py, the file that also
generates the RTL package, so the two cannot drift apart.

Needs the FTDI D2XX driver (Windows: installed with Vivado's cable drivers or FTDI's CDM
package; macOS: FTDI's D2XX package) and `pip install ftd2xx`.
"""
import argparse
import os
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fpga"))
import regmap as rm  # noqa: E402

REG = {name: addr for name, addr, *_ in rm.REGS}
FIELDS = {name: fields for name, _, _, _, _, fields in rm.REGS}
PINS = [name for name, *_ in rm.DUT_PINS]
PIN = {name: i for i, name in enumerate(PINS)}
CLK_SEL = {"off": 0, "dig": 1, "ana": 2}

RAILS = ("vio", "vbat", "vbat_rf", "vddd", "vdda", "rf_vdd")  # the six INA237 monitors
RAIL_EN = {"vio": "VIO", "vbat": "VBAT", "1v": "VDD"}         # command name -> RAIL_EN field
RAIL_SW = {"vddd": "VDDD", "vdda": "VDDA", "rf": "RF"}        # command name -> RAIL_SW field
RAIL_PG = {"vio": ("VIO",), "vbat": ("VBAT",), "1v": ("VDDD", "VDDA", "RF")}  # enable -> its PGs
N_GPIO = 6  # FPGA_GPIO0-5 on J16
SEQ_SETTLE_S = 0.020  # regulator soft-start margin between power-up steps
FEATURE_BLOCK = {"I2C": "the I2C master", "ADC": "the ADC capture block", "UART": "the UART bridge",
                 "TL": "the serial TileLink engine"}
# localhost: a running, connected app takes commands here (app/share.py). Below 32768, where
# Windows does not hand out or reserve ports (47000-48000 would not bind on the dev PC).
APP_PORT = int(os.environ.get("SCUM_APP_PORT", 29563))

# ---- ADC3910 (U24), on its own SPI; registers from the datasheet (SBASAD1A)
ADC_FS = 32e6       # sample rate: the 32.000 MHz oscillator (Y2) on the ADC's CLK pin
ADC_RESET, ADC_CFG_ALERT, ADC_SPARE, ADC_TEST_PAT_CFG = 0x000, 0x038, 0x039, 0x091
ADC_TEST_PAT_B, ADC_TEST_PAT_A = 0x092, 0x094  # 14-bit patterns, low byte first
ADC_TEST_PAT_EN = {"A": 1 << 4, "B": 1 << 5}
ADC_CAL_S = 200_000 / ADC_FS  # power-up calibration after a reset, ~6.3 ms
CAP_BURST = 8192    # words per burst read: 32 KB, well inside the D2XX read timeout

# Serial TileLink frames, MSB first: SCuM-V24B's generated serdes (scum-v-bringup,
# GenericSerializer.sv). TL_FRAME in the FPGA is their total width. A chip with another layout
# needs only this table changed.
TL_FIELDS = (("chan", 3), ("opcode", 3), ("param", 3), ("size", 8), ("source", 8),
             ("address", 64), ("data", 64), ("corrupt", 1), ("union", 9), ("last", 1))
TL_BITS = sum(w for _, w in TL_FIELDS)
TL_WORDS = (TL_BITS + 31) // 32
TL_A, TL_D = 0, 3                          # channels
TL_PUT_FULL, TL_PUT_PARTIAL, TL_GET = 0, 1, 4  # A opcodes
TL_ACK, TL_ACK_DATA = 0, 1                 # D opcodes
TL_BEAT = 8         # data field bytes: byte lanes and masks follow address % 8 (unconfirmed on V26)
TL_WINDOW = 1       # requests in flight, each with its own source id; raise once V26 is known
TL_TIMEOUT_S = 0.5
UART_BAUD_TOL = 0.02

# ---- telemetry bus (TELEM_SCL/SDA): addresses and parts from the schematic (Telemetry.SchDoc)
INA = {  # rail: (INA237 address, shunt ohms). All shunts are 0.1 % except VBAT's R14 (1 %).
    "vio": (0x40, 2.0),        # U13, R23
    "vbat": (0x41, 0.82),      # U15, R14
    "vbat_rf": (0x45, 1.6),    # U14, R17
    "vddd": (0x43, 1.6),       # U17, R30
    "vdda": (0x44, 5.6),       # U18, R37
    "rf_vdd": (0x4C, 1.6),     # U16, R12 (A1 on SCL, so not 0x47: that is the DAC's broadcast address)
}
INA_CONFIG, INA_ADC_CONFIG, INA_VSHUNT, INA_VBUS, INA_DIAG_ALRT, INA_SOVL, INA_MFR_ID = (
    0x00, 0x01, 0x04, 0x05, 0x0B, 0x0C, 0x3E)
INA_MFR_TI = 0x5449
# Continuous shunt + bus, 1052 us conversions, 16x averaging: a fresh reading every ~34 ms.
INA_ADC_CONFIG_VALUE = (0xB << 12) | (5 << 9) | (5 << 6) | (5 << 3) | 2
INA_ALATCH, INA_SHNTOL = 1 << 15, 1 << 6  # DIAG_ALRT: latch the alert; shunt over limit
INA_VBUS_LSB, INA_VSH_LSB = 3.125e-3, 5e-6  # V per code (ADCRANGE = 0: +-163.84 mV)
INA_SOVL_OFF = 0x7FFF

TLA_ADDRS = range(0x10, 0x18)  # U20 TLA2528; R71 and R72 not fitted give 0x10, but probe them all
TLA_READ, TLA_WRITE, TLA_GENERAL_CFG, TLA_CHANNEL_SEL = 0x10, 0x08, 0x01, 0x11
TLA_VREF = 3.3  # AVDD = SYS_3V3 is the reference
AUX = ("VNFC", "SPARE_3", "SPARE_4", "PWR_I_REF", "VIO", "PWR_VDD_RF", "SPARE_1", "SPARE_2")

# U28 DAC53401 (A0 = GND) trims VBAT: its output drives 36.4k (R19 + R20, filtered by C28) into
# the VBAT regulator's FB node (TPS7A90, 0.8 V), which has R18 10k from VBAT and R21 13.7k to GND.
# The output starts powered down (high impedance), which leaves VBAT at 0.8 * (1 + 10/13.7).
DAC_ADDR = 0x48
DAC_STATUS, DAC_GENERAL_CONFIG, DAC_DATA = 0xD0, 0xD1, 0x21
DAC_CONFIG_ON, DAC_CONFIG_OFF = 0x01E0, 0x01F0  # no slew, VDD reference; DAC_PDN 00 vs 10 (Hi-Z)
DAC_VDD = 1.8  # SYS_1V8 is the reference
VFB, R_TOP, R_BOT, R_DAC = 0.8, 10e3, 13.7e3, 36.4e3


def vbat_from_dac(vdac):
    """VBAT for a DAC output voltage (None = DAC powered down)."""
    i = VFB / R_BOT + (0 if vdac is None else (VFB - vdac) / R_DAC)
    return VFB + R_TOP * i


VBAT_MAX, VBAT_MIN = vbat_from_dac(0.0), vbat_from_dac(DAC_VDD * 1023 / 1024)


class BoardError(RuntimeError):
    pass


def bit(reg, field):
    return 1 << FIELDS[reg][field][0]


def list_devices():
    """[(serial, description)] for every FT2232H channel B, and an error string if the D2XX
    driver could not be loaded (then the list is empty)."""
    try:
        import ftd2xx

        serials = [s.decode() for s in (ftd2xx.listDevices() or [])]
    except Exception as e:  # noqa: BLE001 - no driver, no library, no devices: all "none found"
        return [], "FTDI D2XX driver not available (%s)" % e
    # D2XX names the channels "<serial>A" and "<serial>B"; B is the register link.
    return [(s, "%s (SCuMV26 FPGA board)" % s[:-1]) for s in serials if s.endswith("B")], ""


class RegLink:
    """Register reads and writes over the FT2232H channel B FIFO, or over any object with the
    D2XX device's write/read/purge/close methods (`transport`, e.g. sim.SimFpga)."""

    def __init__(self, serial=None, transport=None):
        self.dev = transport if transport is not None else self._open(serial)

    @staticmethod
    def _open(serial):
        import ftd2xx

        if serial is None:
            devs, err = list_devices()
            if not devs:
                raise BoardError(err or "no SCuMV26 board found (no FT2232H channel B)")
            serial = devs[0][0]
        dev = ftd2xx.openEx(serial.encode())
        dev.setTimeouts(1000, 1000)
        dev.setLatencyTimer(2)
        dev.setUSBParameters(65536, 65536)
        dev.purge()
        return dev

    def close(self):
        self.dev.close()

    def _xfer(self, packet, nreply):
        self.dev.write(bytes(packet))
        reply = self.dev.read(nreply) if nreply else b""
        if len(reply) != nreply:
            raise BoardError("short reply from the board: %d of %d bytes" % (len(reply), nreply))
        return reply

    def write(self, addr, value):
        if self._xfer(struct.pack("<BHI", rm.CMD_WRITE, addr, value & 0xFFFFFFFF), 1) != bytes([rm.ACK]):
            raise BoardError("write to 0x%04X not acknowledged" % addr)

    def read(self, addr):
        return struct.unpack("<I", self._xfer(struct.pack("<BH", rm.CMD_READ, addr), 4))[0]

    def write_many(self, writes):
        """[(addr, value)] as one USB transfer: the packets go out together and the
        acknowledgements come back together."""
        if not writes:
            return
        pkt = b"".join(struct.pack("<BHI", rm.CMD_WRITE, a, v & 0xFFFFFFFF) for a, v in writes)
        if self._xfer(pkt, len(writes)) != bytes([rm.ACK]) * len(writes):
            raise BoardError("a batched write was not acknowledged")

    def burst(self, addr, n):
        """n reads of one address (a data port), as a list of words."""
        data = self._xfer(struct.pack("<BHI", rm.CMD_BURST, addr, n), 4 * n)
        return list(struct.unpack("<%dI" % n, data))

    def resync(self):
        """Recover from a half-sent packet: the bridge drops it after 1 ms of silence."""
        time.sleep(0.002)
        self.dev.purge()


HELP = """\
help                                 list commands
id                                   gateware version and the blocks it has
status [peek]                        event counters (trips, button resets, link framing errors)
                                     and uptime; zeroed after reading unless `peek`
power up | down                      sequenced: VIO -> VBAT -> 1V rails (reverse for down);
                                     power up clears a latched trip once ALERT has gone
rail [vio|vbat|1v on|off]            single regulator enable
pg                                   regulator power-good pins (1 = in regulation)
sw [vddd|vdda|rf board|dut]          rail-source switches
trip [on|off|clear]                  hardware over-current trip on TELEM_ALERT
clk [sel dig|ana|off] [win ms]       DUT clock counter: LMK1D input and counting window
freq                                 DUT clock frequency from the next complete window
pin [name [0|1|in|out]]              read a pin, drive it, or turn it around
pulse <name> <us>                    invert an output for <us> microseconds (timed in the FPGA)
gpio [n [0|1|in|out]]                J16 header GPIO 0-5: read, drive, or turn around
reg <addr> [value]                   raw register read or write
tele                                 the six INA237 rail monitors: bus V, current, shunt mV
ilim [rail mA]                       INA237 over-current limit per rail (0 = off); with the
                                     trip on, crossing it switches every DUT rail off
aux                                  the TLA2528's eight DUT analog inputs, volts
vbat [V | off]                       VBAT set point through the DAC (open loop); off = DAC
                                     released, VBAT at its divider's 1.384 V
trim <V>                             VBAT to <V> closed loop on its INA237 (VBAT must be on)
i2c scan | recover                   list responding addresses / free a stuck bus
i2c <addr> [w b0 b1 ..] [r n] [sep]  raw transaction (hex bytes, up to 4 each way)
adc                                  ADC3910: DCLK rate, capture state
adc init                             hardware reset, wait out its calibration, check SPI
adc rd <addr> | wr <addr> <val>      ADC3910 register (hex)
adc pat <a> <b> | pat off            constant test codes on channels A and B (10-bit, hex)
cap [pairs]                          capture both channels (default 4096 pairs, at most 65536)
                                     and print per-channel statistics
tl                                   serial TileLink: TL_CLK rate, engine state, FIFOs
tl on | off                          the engine takes over / releases the TileLink pins
tl rd <addr> [n]                     n 32-bit words from the DUT's address space (hex)
tl wr <addr> <word> [word ..]        write 32-bit words (hex), each a PutFullData
tl load <file> <addr>                write a binary file into the DUT's memory, then verify it
uart                                 DUT UART: baud, bytes waiting, errors
uart baud <n>                        8N1 at n baud (default 115200)
uart tx <text>                       send text and CR LF
uart rx                              what the DUT has sent since the last read"""


class Board:
    """The board's commands, on registers."""

    def __init__(self, port=None, transport=None):
        """Open the board with FT2232H serial `port` (channel B, "...B"; the first one found if
        None), or on `transport` (e.g. sim.SimFpga)."""
        self.link = RegLink(port, transport)
        self.serial = port or ("simulator" if transport is not None else "")
        ident = self.link.read(REG["ID"])
        if ident != rm.ID_MAGIC:
            self.link.close()
            raise BoardError("ID reads 0x%08X, not SCUM: wrong channel, or the FPGA is not configured"
                             % ident)
        f = self.link.read(REG["FEATURES"])
        self.features = {name for name in FIELDS["FEATURES"] if f & bit("FEATURES", name)}
        self.events = []         # events seen while no on_event callback is set
        self.on_event = None     # f(text) for each event (trip, button, framing error)
        self.on_line = None      # f(direction, text, quiet) per command: ">" sent, "<" reply
        self.quiet = False       # passed to on_line: marks background polling traffic
        self.last_capture_ms = None
        self._i2c_wdata = None   # I2C_WDATA as last written, to skip rewriting it
        self._ina_ok = None      # rails whose INA237 answered and was configured
        self._tla = 0            # TLA2528 address once found
        self._dac = None         # VBAT DAC code this session wrote
        self._zero = {}          # status() counter baselines: the FPGA's counters only count up

    def close(self):
        self.link.close()

    def r(self, name):
        return self.link.read(REG[name])

    def w(self, name, value):
        self.link.write(REG[name], value)

    def has(self, feature):
        return feature in self.features

    def _need(self, feature):
        if feature not in self.features:
            raise BoardError("not in the gateware yet (needs %s)" % FEATURE_BLOCK[feature])

    # ---- events -------------------------------------------------------------------------

    def _event(self, text):
        if self.on_event:
            self.on_event(text)
        else:
            self.events.append(text)

    def poll(self):
        """Read and clear the event flags, and report each as an event."""
        ev = self.r("EVENTS")
        if not ev:
            return
        self.w("EVENTS", ev)
        if ev & bit("EVENTS", "TRIP"):
            rails = []
            if self.has("I2C"):
                try:
                    rails = self.alert_rails()
                except BoardError:
                    pass
            self._event("ALERT over-current trip: all DUT rails switched off"
                        + (": " + " ".join(rails) if rails else ""))
        if ev & bit("EVENTS", "BUTTON"):
            self._event("BUTTON DUT reset button pressed")
        if ev & bit("EVENTS", "FRAME"):
            self._event("LINK framing error: a host packet was dropped")

    # ---- text commands ------------------------------------------------------------------

    def _trace(self, direction, text):
        if self.on_line:
            self.on_line(direction, text, self.quiet)

    def cmd(self, line):
        """Run one text command; return its reply lines. Raises BoardError on ERR."""
        line = line.strip()
        self._trace(">", line)
        words = line.split()
        try:
            if not words:
                raise BoardError("empty command")
            handler = getattr(self, "_cmd_" + words[0], None)
            if handler is None:
                raise BoardError("unknown command %r (help lists them)" % words[0])
            try:
                out = handler(words[1:])
            except (ValueError, IndexError, KeyError) as e:
                raise BoardError("%s: bad arguments (%s); see help" % (words[0], e)) from None
        except BoardError as e:
            self._trace("<", "ERR %s" % e)
            raise
        for text in out:
            self._trace("<", text)
        return out

    def _cmd_help(self, a):
        return HELP.splitlines()

    def _cmd_id(self, a):
        return [self.ident()]

    def _cmd_status(self, a):
        return ["%s %s" % kv for kv in self.status(peek=a[:1] == ["peek"]).items()]

    def _cmd_power(self, a):
        {"up": self.power_up, "down": self.power_down}[a[0]]()
        return self._cmd_rail([])

    def _cmd_rail(self, a):
        if a:
            if a[1] not in ("on", "off"):
                raise BoardError("rail: on or off")
            self.set_rail(a[0], a[1] == "on")
        return ["%s %s" % (k, "on" if v else "off") for k, v in self.rails().items()]

    def _cmd_pg(self, a):
        return ["%s %d" % kv for kv in self.power_good().items()]

    def _cmd_sw(self, a):
        if a:
            if a[1] not in ("board", "dut"):
                raise BoardError("sw: board or dut")
            self.set_switch(a[0], a[1])
        return ["%s %s" % kv for kv in self.switches().items()]

    def _cmd_trip(self, a):
        if a[:1] == ["clear"]:
            self.clear_trip()
        elif a:
            self.trip_enable({"on": True, "off": False}[a[0]])
        c, s = self.r("TRIP_CTRL"), self.r("TRIP_STATUS")
        return ["trip %s, %s, ALERT %s" % ("on" if c & bit("TRIP_CTRL", "EN") else "off",
                                          "TRIPPED" if s & bit("TRIP_STATUS", "TRIPPED") else "not tripped",
                                          "asserted" if s & bit("TRIP_STATUS", "ALERT") else "idle")]

    def _cmd_clk(self, a):
        kw = dict(zip(a[0::2], a[1::2]))
        if set(kw) - {"sel", "win"} or len(a) % 2:
            raise BoardError("usage: clk [sel dig|ana|off] [win ms]")
        if "sel" in kw and kw["sel"] not in CLK_SEL:
            raise BoardError("sel is dig, ana or off")
        c = self.clk(sel=kw.get("sel"), win=float(kw["win"]) if "win" in kw else None)
        return ["sel %s win %g ms resolution %g Hz" % (c["sel"], c["win"], c["resolution"])]

    def _cmd_freq(self, a):
        return ["%.1f Hz" % self.freq()]

    def _cmd_pin(self, a):
        if not a:
            return ["%-14s %-3s %d" % (n, "out" if o else "in", lvl) for n, (o, lvl) in self.pins().items()]
        name = a[0].upper()
        if name not in PIN:
            raise BoardError("no pin %r" % a[0])
        if len(a) > 1:
            if a[1] in ("in", "out"):
                self.direction(name, a[1] == "out")
            else:
                self.pin(name, int(a[1]))
        o, lvl = self.pins()[name]
        return ["%s %s %d" % (name, "out" if o else "in", lvl)]

    def _cmd_gpio(self, a):
        if not a:
            return ["GPIO%d %-3s %d" % (i, "out" if o else "in", lvl) for i, (o, lvl) in enumerate(self.gpios())]
        i = int(a[0])
        if len(a) > 1:
            if a[1] in ("in", "out"):
                self.gpio_direction(i, a[1] == "out")
            else:
                self.gpio(i, int(a[1]))
        o, lvl = self.gpios()[i]
        return ["GPIO%d %s %d" % (i, "out" if o else "in", lvl)]

    def _cmd_pulse(self, a):
        self.pulse(a[0], int(a[1]))
        return []

    def _cmd_reg(self, a):
        addr = int(a[0], 0)
        if len(a) > 1:
            self.link.write(addr, int(a[1], 0))
        return ["0x%04X = 0x%08X" % (addr, self.link.read(addr))]

    def _cmd_tele(self, a):
        out = []
        for rail, m in self.tele().items():
            out.append("%-8s no reply" % rail if m is None else
                       "%-8s %7.4f V %9.3f mA %8.3f mV" % (rail, m["v"], m["ma"], m["mv_shunt"]))
        return out

    def _cmd_ilim(self, a):
        if a:
            self.set_ilim(a[0], float(a[1]))
        return ["%-8s %s" % (r, "%.2f mA" % ma if ma else "off") for r, ma in self.ilim().items()]

    def _cmd_aux(self, a):
        return ["AIN%d %-11s %s" % (ch, name, "failed" if v is None else "%.4f V" % v)
                for ch, name, v in self.aux()]

    def _vbat_line(self, v):
        dac = "off (Hi-Z)" if not v["on"] else "%d (%.4f V)" % (v["dac"], v["vdac"])
        line = "dac %s -> set point %.4f V" % (dac, v["setpoint"])
        return [line + ("  measured %.4f V" % v["measured"] if "measured" in v else "")]

    def _cmd_vbat(self, a):
        return self._vbat_line(self.vbat(None if not a else "off" if a[0] == "off" else float(a[0])))

    def _cmd_trim(self, a):
        return self._vbat_line(self.trim(float(a[0])))

    def _cmd_i2c(self, a):
        if a == ["scan"]:
            return ["0x%02X" % x for x in self.i2c_scan()] or ["no devices"]
        if a == ["recover"]:
            self.i2c_recover()
            return []
        addr, w, r, sep, i = int(a[0], 16), b"", 0, False, 1
        while i < len(a):
            if a[i] == "w":
                j = i + 1
                while j < len(a) and a[j] not in ("r", "sep"):
                    j += 1
                w, i = bytes(int(x, 16) for x in a[i + 1:j]), j
            elif a[i] == "r":
                r, i = int(a[i + 1]), i + 2
            elif a[i] == "sep":
                sep, i = True, i + 1
            else:
                raise BoardError("i2c: expected w, r or sep, not %r" % a[i])
        got = self.i2c(addr, w, r, sep)
        return [" ".join("%02X" % x for x in got)] if r else []

    def _cmd_adc(self, a):
        if not a:
            st = self.r("CAP_STATUS")
            return ["dclk %d kHz" % self.r("ADC_DCLK_KHZ"),
                    "capture %s" % ("busy" if st & bit("CAP_STATUS", "BUSY") else
                                    "done" if st & bit("CAP_STATUS", "DONE") else "none")]
        if a[0] == "init":
            return ["ADC3910 ready, DCLK %d kHz" % self.adc_init()]
        if a[0] == "rd":
            addr = int(a[1], 16)
            return ["0x%03X = 0x%02X" % (addr, self.adc_rd(addr))]
        if a[0] == "wr":
            self.adc_wr(int(a[1], 16), int(a[2], 16))
            return []
        if a[0] == "pat":
            self.adc_pattern(*([None] if a[1:] == ["off"] else [int(x, 16) for x in a[1:3]]))
            return []
        raise BoardError("adc: init, rd, wr or pat")

    def _cmd_cap(self, a):
        import numpy as np

        ch = unpack(self.capture(int(a[0]) if a else 4096))
        return ["%s  min %4d  max %4d  mean %8.2f  rms %7.2f" % (
            name, s.min(), s.max(), s.mean(), np.std(s)) for name, s in ch.items()]

    def _cmd_tl(self, a):
        if not a:
            s = self.tl_status()
            return ["TL_CLK %d kHz%s" % (s["clk_khz"], "" if s["clk_khz"] else " (the DUT is not clocking the link)"),
                    "engine %s, frame %d bits, rx %d words, tx free %d%s" % (
                        "on" if s["on"] else "off", s["frame"], s["rx_words"], s["tx_free"],
                        ", busy" if s["busy"] else "")]
        if a[0] in ("on", "off"):
            self.tl_enable(a[0] == "on")
            return []
        if a[0] == "rd":
            addr = int(a[1], 16)
            words = self.tl_read(addr, int(a[2]) if len(a) > 2 else 1)
            return ["0x%08X: 0x%08X" % (addr + 4 * i, w) for i, w in enumerate(words)]
        if a[0] == "wr":
            self.tl_write(int(a[1], 16), [int(x, 16) for x in a[2:]])
            return []
        if a[0] == "load":
            n, secs = self.tl_load(a[1], int(a[2], 16))
            return ["loaded and verified %d bytes at 0x%08X in %.1f s" % (n, int(a[2], 16), secs)]
        raise BoardError("tl: on, off, rd, wr or load")

    def _cmd_uart(self, a):
        if not a:
            return ["baud %d, %d bytes waiting, %d to send" % self.uart_status()]
        if a[0] == "baud":
            return ["baud %d" % self.uart_baud(int(a[1]))]
        if a[0] == "tx":
            self.uart_tx((" ".join(a[1:]) + "\r\n").encode())
            return []
        if a[0] == "rx":
            # bytes that are not UTF-8 show as \xNN, which any console can print
            text = self.uart_rx().decode("utf-8", errors="backslashreplace")
            return text.splitlines() or ["(nothing)"]
        raise BoardError("uart: baud, tx or rx")

    # ---- parsed replies -----------------------------------------------------------------

    def ident(self):
        v = self.r("VERSION")
        return "SCuMV26 gateware %d.%d.%d, board %s, blocks: %s" % (
            v >> 24, (v >> 16) & 0xFF, v & 0xFFFF, self.serial,
            ", ".join(sorted(self.features)) or "core only")

    STATUS = (("ocp_trips", "TRIP_COUNT"), ("button_resets", "BUTTON_COUNT"),
              ("frame_errors", "FRAME_COUNT"))

    def status(self, peek=False):
        """Event counters as a dict of strings; zeroed (here: re-based) unless `peek`."""
        res = {}
        for key, reg in self.STATUS:
            n = self.r(reg)
            res[key] = str(n - self._zero.get(key, 0))
            if not peek:
                self._zero[key] = n
        res["uptime_s"] = str(self.r("UPTIME_MS") // 1000)
        return res

    # power

    def rails(self):
        """{"vio"|"vbat"|"1v": bool} regulator enables."""
        v = self.r("RAIL_EN")
        return {k: bool(v & bit("RAIL_EN", f)) for k, f in RAIL_EN.items()}

    def set_rail(self, name, on):
        v = self.r("RAIL_EN")
        m = bit("RAIL_EN", RAIL_EN[name])
        self.w("RAIL_EN", (v | m) if on else (v & ~m))
        if on and self.tripped():
            raise BoardError("rails are held off by an over-current trip: `trip clear` or power up")

    def power_good(self):
        """{"vio"|"vbat"|"vddd"|...: bool} the regulators' PG pins."""
        v = self.r("RAIL_PG")
        return {f.lower(): bool(v & bit("RAIL_PG", f)) for f in FIELDS["RAIL_PG"]}

    def switches(self):
        """{"vddd"|"vdda"|"rf": "board"|"dut"}."""
        v = self.r("RAIL_SW")
        return {k: "board" if v & bit("RAIL_SW", f) else "dut" for k, f in RAIL_SW.items()}

    def set_switch(self, name, src):
        v = self.r("RAIL_SW")
        m = bit("RAIL_SW", RAIL_SW[name])
        self.w("RAIL_SW", (v | m) if src == "board" else (v & ~m))

    def power_up(self):
        """VIO -> VBAT -> 1 V rails, each step waiting for its regulators' PG. A latched trip
        is cleared first, unless ALERT is still asserted. A rail whose PG is not up after
        SEQ_SETTLE_S powers everything back down."""
        if self.tripped():
            alert = self.r("TRIP_STATUS") & bit("TRIP_STATUS", "ALERT")
            if alert and self.has("I2C"):
                self.alert_rails()  # the INA237s latch ALERT until their flags are read
                alert = self.r("TRIP_STATUS") & bit("TRIP_STATUS", "ALERT")
            if alert:
                raise BoardError("over-current ALERT is still asserted; not powering up")
            self.clear_trip()
        for rail in ("vio", "vbat", "1v"):
            self.set_rail(rail, True)
            time.sleep(SEQ_SETTLE_S)
            pg = self.power_good()
            bad = [f.lower() for f in RAIL_PG[rail] if not pg[f.lower()]]
            if bad:
                self.power_down()
                raise BoardError("no power good from %s after %d ms; rails switched off"
                                 % (", ".join(bad), SEQ_SETTLE_S * 1000))

    def power_down(self):
        for rail in ("1v", "vbat", "vio"):
            self.set_rail(rail, False)
            time.sleep(SEQ_SETTLE_S)

    def trip_enable(self, on=True, active_high=False):
        self.w("TRIP_CTRL", (bit("TRIP_CTRL", "EN") if on else 0)
               | (bit("TRIP_CTRL", "ACTIVE_HIGH") if active_high else 0))

    def tripped(self):
        return bool(self.r("TRIP_STATUS") & bit("TRIP_STATUS", "TRIPPED"))

    def trip_state(self):
        """{"enabled", "tripped" (latched), "alert" (TELEM_ALERT asserted now)}: bools."""
        c, s = self.r("TRIP_CTRL"), self.r("TRIP_STATUS")
        return {"enabled": bool(c & bit("TRIP_CTRL", "EN")),
                "tripped": bool(s & bit("TRIP_STATUS", "TRIPPED")),
                "alert": bool(s & bit("TRIP_STATUS", "ALERT"))}

    def clear_trip(self):
        self.w("TRIP_STATUS", bit("TRIP_STATUS", "TRIPPED"))

    # DUT clock

    def clk(self, sel=None, win=None):
        """Set (if given) and return {"sel", "win" (ms), "resolution" (Hz)}."""
        if sel is not None:
            self.w("CLK_CTRL", CLK_SEL[sel])
        if win is not None:
            if not 0 < win <= 40000:
                raise BoardError("window is 0-40000 ms")
            self.w("CLK_WINDOW", max(2, round(win * rm.SYS_HZ / 1000)))
        code, cycles = self.r("CLK_CTRL") & 3, self.r("CLK_WINDOW")
        return {"sel": {v: k for k, v in CLK_SEL.items()}.get(code, "?"),
                "win": cycles * 1000 / rm.SYS_HZ, "resolution": rm.SYS_HZ / cycles}

    def freq(self, timeout=None):
        """DUT clock frequency in Hz, from the next complete window."""
        win = self.r("CLK_WINDOW")
        timeout = timeout or 2.5 * win / rm.SYS_HZ + 0.2
        seq = self.r("CLK_SEQ")
        t0 = time.monotonic()
        while self.r("CLK_SEQ") == seq:
            if time.monotonic() - t0 > timeout:
                raise BoardError("no counting window completed")
            time.sleep(min(0.01, win / rm.SYS_HZ / 4))
        return self.r("CLK_COUNT") * rm.SYS_HZ / win

    # DUT pins

    def pins(self):
        """{name: (is_output, level)} for every DUT control pin."""
        oe, lvl = self.r("DUT_OE"), self.r("DUT_IN")
        return {n: (bool(oe >> i & 1), bool(lvl >> i & 1)) for n, i in PIN.items()}

    def pin(self, name, level=None):
        i = PIN[name.upper()]
        if level is None:
            return bool(self.r("DUT_IN") >> i & 1)
        if not self.r("DUT_OE") >> i & 1:
            raise BoardError("%s is an input: `pin %s out` first" % (name, name))
        out = self.r("DUT_OUT")
        self.w("DUT_OUT", (out | 1 << i) if level else (out & ~(1 << i)))

    def direction(self, name, output):
        i = PIN[name.upper()]
        oe = self.r("DUT_OE")
        self.w("DUT_OE", (oe | 1 << i) if output else (oe & ~(1 << i)))

    # header GPIO

    def gpios(self):
        """[(is_output, level)] for FPGA_GPIO0-5."""
        oe, lvl = self.r("GPIO_OE"), self.r("GPIO_IN")
        return [(bool(oe >> i & 1), bool(lvl >> i & 1)) for i in range(N_GPIO)]

    def gpio(self, i, level=None):
        if not 0 <= i < N_GPIO:
            raise BoardError("GPIO is 0-%d" % (N_GPIO - 1))
        if level is None:
            return bool(self.r("GPIO_IN") >> i & 1)
        if not self.r("GPIO_OE") >> i & 1:
            raise BoardError("GPIO%d is an input: `gpio %d out` first" % (i, i))
        out = self.r("GPIO_OUT")
        self.w("GPIO_OUT", (out | 1 << i) if level else (out & ~(1 << i)))

    def gpio_direction(self, i, output):
        if not 0 <= i < N_GPIO:
            raise BoardError("GPIO is 0-%d" % (N_GPIO - 1))
        oe = self.r("GPIO_OE")
        self.w("GPIO_OE", (oe | 1 << i) if output else (oe & ~(1 << i)))

    def pulse(self, name, us):
        """Invert an output for `us` microseconds, then restore it (timed in the FPGA)."""
        name = name.upper()
        if not 1 <= us < 1 << 24:
            raise BoardError("pulse width is 1-16777215 us")
        if not self.r("DUT_OE") >> PIN[name] & 1:
            raise BoardError("%s is an input" % name)
        self.w("DUT_PULSE", PIN[name] << 24 | us)
        time.sleep(us / 1e6)
        t0 = time.monotonic()
        while self.r("DUT_PULSE") >> 31:
            if time.monotonic() - t0 > 1:
                raise BoardError("pulse did not finish")
            time.sleep(0.001)

    # telemetry I2C

    def i2c(self, addr, wdata=b"", rlen=0, sep=False):
        """One transaction on the telemetry bus: write `wdata` (0-4 bytes), then read `rlen`
        (0-4) after a repeated START (STOP + START with sep). Returns the bytes read."""
        self._need("I2C")
        if len(wdata) > 4 or not 0 <= rlen <= 4:
            raise BoardError("i2c: at most 4 bytes each way")
        word = int.from_bytes(bytes(wdata).ljust(4, b"\0"), "little")
        if word != self._i2c_wdata:
            self.w("I2C_WDATA", word)
            self._i2c_wdata = word
        self.w("I2C_CMD", (addr & 0x7F) | len(wdata) << FIELDS["I2C_CMD"]["WLEN"][0]
               | rlen << FIELDS["I2C_CMD"]["RLEN"][0] | (bit("I2C_CMD", "SEP") if sep else 0))
        s = self._i2c_wait()
        for flag, why in (("ANACK", "no device answers at 0x%02X" % addr),
                          ("DNACK", "0x%02X did not acknowledge a written byte" % addr),
                          ("TIMEOUT", "0x%02X held SCL low for over 10 ms" % addr),
                          ("STUCK", "the bus is held low (SCL %d, SDA %d): try `i2c recover`"
                           % (s >> FIELDS["I2C_STATUS"]["SCL"][0] & 1,
                              s >> FIELDS["I2C_STATUS"]["SDA"][0] & 1))):
            if s & bit("I2C_STATUS", flag):
                raise BoardError("i2c: " + why)
        return self.r("I2C_RDATA").to_bytes(4, "little")[:rlen] if rlen else b""

    def _i2c_wait(self):
        t0 = time.monotonic()
        while True:
            s = self.r("I2C_STATUS")
            if not s & bit("I2C_STATUS", "BUSY"):
                return s
            if time.monotonic() - t0 > 0.1:
                raise BoardError("i2c: the transaction did not finish")

    def i2c_scan(self):
        """Addresses 0x08-0x77 that acknowledge."""
        found = []
        for a in range(0x08, 0x78):
            try:
                self.i2c(a)
                found.append(a)
            except BoardError as e:
                if "no device" not in str(e):
                    raise
        return found

    def i2c_recover(self):
        self._need("I2C")
        self.w("I2C_CMD", bit("I2C_CMD", "RECOVER"))
        s = self._i2c_wait()
        if not s & bit("I2C_STATUS", "SDA") or not s & bit("I2C_STATUS", "SCL"):
            raise BoardError("i2c: the bus is still held low after recovery")

    # INA237 rail monitors

    def _ina_read(self, rail, reg):
        return int.from_bytes(self.i2c(INA[rail][0], bytes([reg]), 2), "big")

    def _ina_write(self, rail, reg, value):
        self.i2c(INA[rail][0], bytes([reg]) + (value & 0xFFFF).to_bytes(2, "big"))

    def _ina_setup(self):
        """Configure each INA237 once per connection: ADC range +-163.84 mV, continuous 16x
        averaging, latched ALERT (so a trip stays readable). The limits (SOVL) are left alone."""
        if self._ina_ok is not None:
            return
        self._ina_ok = set()
        for rail in INA:
            try:
                if self._ina_read(rail, INA_MFR_ID) != INA_MFR_TI:
                    continue
                self._ina_write(rail, INA_CONFIG, 0)
                self._ina_write(rail, INA_ADC_CONFIG, INA_ADC_CONFIG_VALUE)
                self._ina_write(rail, INA_DIAG_ALRT, INA_ALATCH)
                self._ina_ok.add(rail)
            except BoardError:
                pass

    def tele(self):
        """{rail: {"v", "ma", "mv_shunt"} or None (no reply)}."""
        self._need("I2C")
        self._ina_setup()
        res = {}
        for rail, (_, ohms) in INA.items():
            try:
                if rail not in self._ina_ok:
                    raise BoardError("no INA237")
                vb = self._ina_read(rail, INA_VBUS) * INA_VBUS_LSB
                vs = int.from_bytes(self._ina_read(rail, INA_VSHUNT).to_bytes(2, "big"), "big",
                                    signed=True) * INA_VSH_LSB
                res[rail] = {"v": vb, "ma": vs / ohms * 1e3, "mv_shunt": vs * 1e3}
            except BoardError:
                res[rail] = None
        return res

    def ilim(self):
        """{rail: mA} over-current limits from the INA237s (0 = off)."""
        self._need("I2C")
        self._ina_setup()
        res = {}
        for rail, (_, ohms) in INA.items():
            code = self._ina_read(rail, INA_SOVL) if rail in self._ina_ok else INA_SOVL_OFF
            res[rail] = 0.0 if code >= INA_SOVL_OFF else code * INA_VSH_LSB / ohms * 1e3
        return res

    def set_ilim(self, rail, ma):
        """Set a rail's over-current limit in mA (0 = off). Compared with every conversion
        before averaging, so a trip fires within about 2 ms."""
        if rail not in INA:
            raise BoardError("ilim: rail is one of %s" % ", ".join(INA))
        if ma < 0:
            raise BoardError("ilim: the limit is 0 (off) or more mA")
        self._need("I2C")
        self._ina_setup()
        if rail not in self._ina_ok:
            raise BoardError("ilim: the %s INA237 does not answer" % rail)
        code = INA_SOVL_OFF if ma == 0 else min(INA_SOVL_OFF - 1, max(1, round(ma * 1e-3 * INA[rail][1] / INA_VSH_LSB)))
        self._ina_write(rail, INA_SOVL, code)

    def alert_rails(self):
        """Rails whose INA237 latched a shunt over-limit; reading clears the latch (and ALERT)."""
        self._ina_setup()
        hit = []
        for rail in sorted(self._ina_ok, key=list(INA).index):
            try:
                if self._ina_read(rail, INA_DIAG_ALRT) & INA_SHNTOL:
                    hit.append(rail)
            except BoardError:
                pass
        return hit

    # TLA2528 DUT analog inputs

    def _tla_reg(self, reg, value=None):
        if value is None:
            return self.i2c(self._tla, bytes([TLA_READ, reg]), 1, sep=True)[0]
        self.i2c(self._tla, bytes([TLA_WRITE, reg, value]))

    def _tla_setup(self):
        if self._tla:
            return
        for a in TLA_ADDRS:
            try:
                self.i2c(a)
            except BoardError:
                continue
            self._tla = a
            self._tla_reg(TLA_GENERAL_CFG, 0x02)  # offset calibration; pins default to analog
            t0 = time.monotonic()
            while self._tla_reg(TLA_GENERAL_CFG) & 0x02 and time.monotonic() - t0 < 0.05:
                pass
            return
        raise BoardError("aux: no TLA2528 at 0x10-0x17")

    def aux(self):
        """[(channel, name, volts or None)] for the TLA2528's eight inputs, read in manual mode
        (the first conversion after each channel change is thrown away)."""
        self._need("I2C")
        self._tla_setup()
        res = []
        for ch, name in enumerate(AUX):
            try:
                self._tla_reg(TLA_CHANNEL_SEL, ch)
                self.i2c(self._tla, rlen=2)
                b = self.i2c(self._tla, rlen=2)
                res.append((ch, name, (int.from_bytes(b, "big") >> 4) * TLA_VREF / 4096))
            except BoardError:
                res.append((ch, name, None))
        return res

    # VBAT trim DAC

    def _dac_reg(self, reg, value=None):
        if value is None:
            return int.from_bytes(self.i2c(DAC_ADDR, bytes([reg]), 2), "big")
        self.i2c(DAC_ADDR, bytes([reg]) + (value & 0xFFFF).to_bytes(2, "big"))

    def vbat(self, volts=None):
        """Set VBAT open loop (volts, or "off" to release the DAC) and return {"on", "dac",
        "vdac", "setpoint"}. The set point is what the resistor network gives for the DAC
        output, with SYS_1V8 taken as exactly 1.8 V; `trim` closes the loop."""
        self._need("I2C")
        if volts == "off":
            self._dac_reg(DAC_GENERAL_CONFIG, DAC_CONFIG_OFF)
        elif volts is not None:
            if not VBAT_MIN - 1e-4 <= volts <= VBAT_MAX + 1e-4:
                raise BoardError("vbat: %.3f-%.3f V" % (VBAT_MIN, VBAT_MAX))
            vdac = VFB - (volts - VFB - R_TOP * VFB / R_BOT) * R_DAC / R_TOP
            self._dac_code(round(vdac / DAC_VDD * 1024))
        on = (self._dac_reg(DAC_GENERAL_CONFIG) >> 3 & 3) == 0
        # DAC_DATA reads back on most parts, but the datasheet marks it write-only: prefer the
        # code this session wrote.
        code = self._dac if self._dac is not None else self._dac_reg(DAC_DATA) >> 2 & 0x3FF
        vdac = code * DAC_VDD / 1024
        return {"on": on, "dac": code, "vdac": vdac, "setpoint": vbat_from_dac(vdac if on else None)}

    def _dac_code(self, code):
        code = min(1023, max(0, code))
        self._dac_reg(DAC_DATA, code << 2)
        self._dac_reg(DAC_GENERAL_CONFIG, DAC_CONFIG_ON)
        self._dac = code

    def trim(self, volts, tol=0.0005):
        """VBAT to `volts` closed loop on its INA237, one DAC step (~0.48 mV) at a time once
        close. VBAT must be on."""
        if not self.rails()["vbat"]:
            raise BoardError("trim: VBAT is off")
        v = self.vbat(volts)
        step = R_TOP / R_DAC * DAC_VDD / 1024  # VBAT falls this much per DAC code
        code = v["dac"]
        for _ in range(12):
            time.sleep(0.08)  # two INA237 updates (16x averaging)
            meas = self.tele()["vbat"]
            if meas is None:
                raise BoardError("trim: the VBAT monitor does not answer")
            err = meas["v"] - volts
            if abs(err) <= tol:
                break
            move = round(err / step) or (1 if err > 0 else -1)
            new = min(1023, max(0, code + move))
            if new == code:
                break  # at the end of the range
            code = new
            self._dac_code(code)
        v = self.vbat()
        v["measured"] = meas["v"]
        return v

    # DUT UART

    def uart_baud(self, baud=None):
        """Set the DUT UART's baud rate (8N1); returns the rate the divider gives."""
        self._need("UART")
        if baud is not None:
            div = round(rm.SYS_HZ / baud)
            if not 16 <= div <= 0xFFFF or abs(rm.SYS_HZ / div - baud) > baud * UART_BAUD_TOL:
                raise BoardError("uart: %d baud is out of reach (%d-%d)" % (
                    baud, rm.SYS_HZ // 0xFFFF + 1, rm.SYS_HZ // 16))
            self.w("UART_DIV", div)
        return round(rm.SYS_HZ / self.r("UART_DIV"))

    def uart_status(self):
        """(baud, bytes received and waiting, bytes still to send)"""
        self._need("UART")
        s = self.r("UART_STATUS")
        return (round(rm.SYS_HZ / self.r("UART_DIV")), s & 0xFFF, s >> FIELDS["UART_STATUS"]["TX_COUNT"][0] & 0xFFF)

    def uart_tx(self, data):
        """Queue bytes for the DUT, waiting for room when there is more than the FIFO holds."""
        self._need("UART")
        data = bytes(data)
        t0 = time.monotonic()
        while data:
            room = rm.UART_TX_FIFO - (self.r("UART_STATUS") >> FIELDS["UART_STATUS"]["TX_COUNT"][0] & 0xFFF)
            if room:
                chunk, data = data[:room], data[room:]
                self.link.write_many([(REG["UART_TX"], b) for b in chunk])
                t0 = time.monotonic()
            elif time.monotonic() - t0 > 1.0:
                raise BoardError("uart: the transmitter is not draining")

    def uart_rx(self):
        """Every byte the DUT has sent since the last call. Receive errors become events."""
        self._need("UART")
        out = bytearray()
        while True:
            n = self.r("UART_STATUS") & 0xFFF
            if not n:
                break
            out += bytes(w & 0xFF for w in self.link.burst(REG["UART_RX"], n) if w & bit("UART_RX", "VALID"))
        ev = self.r("UART_EVENTS")
        if ev:
            self.w("UART_EVENTS", ev)
            if ev & bit("UART_EVENTS", "OVERRUN"):
                self._event("UART receive overrun: bytes from the DUT were lost")
            if ev & bit("UART_EVENTS", "FRAMING"):
                self._event("UART framing error: check the baud rate")
        return bytes(out)

    # serial TileLink

    def tl_enable(self, on=True):
        """Hand the TileLink pins to the engine (frames of TL_BITS), or back to DUT_* GPIO."""
        self._need("TL")
        self.w("TL_CTRL", 0)
        if on:
            self.w("TL_FRAME", TL_BITS)
            self.w("TL_CTRL", bit("TL_CTRL", "EN"))

    def tl_status(self):
        self._need("TL")
        s = self.r("TL_STATUS")
        return {"clk_khz": self.r("TL_CLK_KHZ"), "on": bool(self.r("TL_CTRL") & bit("TL_CTRL", "EN")),
                "frame": self.r("TL_FRAME"), "rx_words": s & 0xFFF,
                "tx_free": s >> FIELDS["TL_STATUS"]["TX_FREE"][0] & 0xFFF,
                "busy": bool(s & bit("TL_STATUS", "TX_BUSY"))}

    def tl_xfer(self, requests):
        """Send A-channel messages (dicts of TL_FIELDS; missing fields 0, last 1) and return
        the D-channel answers in the same order, at most TL_WINDOW in flight at a time."""
        self._need("TL")
        if not self.r("TL_CTRL") & bit("TL_CTRL", "EN"):
            raise BoardError("tl: the engine is off (tl on)")
        out = []
        for i in range(0, len(requests), TL_WINDOW):
            batch = requests[i:i + TL_WINDOW]
            writes = []
            for src, req in enumerate(batch):
                f = dict(req, source=src, last=1)
                writes += [(REG["TL_TX"], w) for w in tl_words(tl_pack(**f))]
            self.link.write_many(writes)
            want = TL_WORDS * len(batch)
            t0 = time.monotonic()
            while self.r("TL_STATUS") & 0xFFF < want:
                if time.monotonic() - t0 > TL_TIMEOUT_S:
                    khz = self.r("TL_CLK_KHZ")
                    raise BoardError("tl: no answer from the DUT%s" % (
                        " (no TL_CLK: is it powered and running?)" if not khz else
                        " (TL_CLK %d kHz)" % khz))
            words = self.link.burst(REG["TL_RX"], want)
            got = [tl_unpack(tl_join(words[j:j + TL_WORDS])) for j in range(0, want, TL_WORDS)]
            got.sort(key=lambda d: d["source"])
            out += got
        return out

    @staticmethod
    def _lane(addr, size):
        lane = addr % TL_BEAT
        n = 1 << size
        if lane % n:
            raise BoardError("tl: 0x%X is not aligned to %d bytes" % (addr, n))
        return lane, ((1 << n) - 1) << lane

    def _check_d(self, d, opcode, addr):
        if d["chan"] != TL_D or d["opcode"] != opcode or d["corrupt"]:
            raise BoardError("tl: unexpected answer at 0x%X (chan %d, opcode %d, corrupt %d)"
                             % (addr, d["chan"], d["opcode"], d["corrupt"]))

    def tl_read(self, addr, n=1, size=2):
        """n values of 2**size bytes from consecutive addresses (Get)."""
        reqs, lanes = [], []
        for i in range(n):
            a = addr + (i << size)
            lane, mask = self._lane(a, size)
            reqs.append({"chan": TL_A, "opcode": TL_GET, "size": size, "address": a, "union": mask})
            lanes.append(lane)
        out = []
        for i, (d, lane) in enumerate(zip(self.tl_xfer(reqs), lanes)):
            self._check_d(d, TL_ACK_DATA, addr + (i << size))
            out.append(d["data"] >> (8 * lane) & ((1 << (8 << size)) - 1))
        return out

    def tl_write(self, addr, values, size=2):
        """Write values of 2**size bytes to consecutive addresses (PutFullData)."""
        reqs = []
        for i, v in enumerate(values):
            a = addr + (i << size)
            lane, mask = self._lane(a, size)
            reqs.append({"chan": TL_A, "opcode": TL_PUT_FULL, "size": size, "address": a,
                         "data": (v & ((1 << (8 << size)) - 1)) << (8 * lane), "union": mask})
        for i, d in enumerate(self.tl_xfer(reqs)):
            self._check_d(d, TL_ACK, addr + (i << size))

    def tl_load(self, path, addr, verify=True):
        """Write a binary file into the DUT's memory at addr (padded to whole words) and read
        it back. Returns (bytes, seconds)."""
        data = Path(path).read_bytes()
        data += b"\0" * (-len(data) % 4)
        words = list(struct.unpack("<%dI" % (len(data) // 4), data))
        t0 = time.monotonic()
        self.tl_write(addr, words)
        if verify:
            back = self.tl_read(addr, len(words))
            bad = next((i for i, (x, y) in enumerate(zip(words, back)) if x != y), None)
            if bad is not None:
                raise BoardError("tl load: 0x%08X reads 0x%08X, wrote 0x%08X"
                                 % (addr + 4 * bad, back[bad], words[bad]))
        return len(data), time.monotonic() - t0

    # ADC3910

    def adc_wr(self, addr, value):
        self._need("ADC")
        self.w("ADC_SPI", (addr & 0x7FFF) << FIELDS["ADC_SPI"]["ADDR"][0] | (value & 0xFF))
        self._adc_wait()

    def adc_rd(self, addr):
        self._need("ADC")
        self.w("ADC_SPI", bit("ADC_SPI", "READ") | (addr & 0x7FFF) << FIELDS["ADC_SPI"]["ADDR"][0])
        return self._adc_wait() >> FIELDS["ADC_SPI_STATUS"]["RDATA"][0] & 0xFF

    def _adc_wait(self):
        t0 = time.monotonic()
        while True:
            s = self.r("ADC_SPI_STATUS")
            if not s & bit("ADC_SPI_STATUS", "BUSY"):
                return s
            if time.monotonic() - t0 > 0.1:
                raise BoardError("adc: the SPI frame did not finish")

    def adc_init(self):
        """Hardware-reset the ADC3910, wait out its power-up calibration (it needs its sample
        clock for that), and check the SPI by writing and reading back SPARE_REG. Leaves the
        ADC in its default: 10-bit DDR, two's complement. Returns the DCLK rate in kHz."""
        self._need("ADC")
        self.w("ADC_CTRL", bit("ADC_CTRL", "RESET"))
        time.sleep(0.001)
        self.w("ADC_CTRL", 0)
        time.sleep(ADC_CAL_S + 0.004)
        t0 = time.monotonic()
        while not self.adc_rd(ADC_CFG_ALERT) & 1:
            if time.monotonic() - t0 > 0.1:
                khz = self.r("ADC_DCLK_KHZ")
                raise BoardError("adc: CFG_ALERT never came up (DCLK %d kHz)%s" % (
                    khz, "; is the ADC powered and clocked?" if not khz else "; check the SPI"))
        for v in (0xA5, 0x5A):
            self.adc_wr(ADC_SPARE, v)
            got = self.adc_rd(ADC_SPARE)
            if got != v:
                raise BoardError("adc: SPARE_REG reads 0x%02X after writing 0x%02X" % (got, v))
        return self.r("ADC_DCLK_KHZ")

    def adc_pattern(self, a=None, b=None):
        """Replace the samples with constant codes (10-bit, as they arrive: A then B), or None
        for the real inputs. The ADC's pattern registers are 14 bits; the 10-bit output is
        taken to be their top 10, as the datasheet says of its 16-bit digital pattern."""
        cfg = self.adc_rd(ADC_TEST_PAT_CFG) & ~(ADC_TEST_PAT_EN["A"] | ADC_TEST_PAT_EN["B"] | 1 << 6)
        if a is not None:
            for reg, code in ((ADC_TEST_PAT_A, a), (ADC_TEST_PAT_B, b)):
                v = (code & 0x3FF) << 4
                self.adc_wr(reg, v & 0xFF)
                self.adc_wr(reg + 1, v >> 8)
            cfg |= ADC_TEST_PAT_EN["A"] | ADC_TEST_PAT_EN["B"]
        self.adc_wr(ADC_TEST_PAT_CFG, cfg)

    def capture(self, pairs=4096):
        """Capture `pairs` samples of both channels at once and read them back: a numpy uint32
        array, one word per sample (A in bits 9:0, B in 25:16; `unpack` splits it). Sets
        last_capture_ms to the time the whole capture and read-back took."""
        import numpy as np

        self._need("ADC")
        if not 1 <= pairs <= rm.CAP_DEPTH:
            raise BoardError("cap: 1-%d pairs" % rm.CAP_DEPTH)
        t0 = time.monotonic()
        self.w("CAP_LEN", pairs)
        self.w("CAP_CTRL", bit("CAP_CTRL", "START"))
        limit = pairs / ADC_FS + 0.2
        while not self.r("CAP_STATUS") & bit("CAP_STATUS", "DONE"):
            if time.monotonic() - t0 > limit:
                khz = self.r("ADC_DCLK_KHZ")
                raise BoardError("cap: the capture did not finish (ADC DCLK %d kHz%s)"
                                 % (khz, ": no clock from the ADC" if not khz else ""))
            time.sleep(0.001)
        self.w("CAP_ADDR", 0)
        words = []
        for i in range(0, pairs, CAP_BURST):
            words += self.link.burst(REG["CAP_DATA"], min(CAP_BURST, pairs - i))
        self.last_capture_ms = (time.monotonic() - t0) * 1000
        return np.array(words, dtype=np.uint32)


def tl_pack(**fields):
    """One serial TileLink frame as an integer, bit 0 first on the wire."""
    v = 0
    for name, w in TL_FIELDS:
        v = v << w | (fields.get(name, 0) & ((1 << w) - 1))
    return v


def tl_unpack(v):
    out = {}
    for name, w in reversed(TL_FIELDS):
        out[name] = v & ((1 << w) - 1)
        v >>= w
    return out


def tl_words(v):
    """A frame as the TL_TX words that carry it."""
    return [v >> (32 * i) & 0xFFFFFFFF for i in range(TL_WORDS)]


def tl_join(words):
    return sum(w << (32 * i) for i, w in enumerate(words)) & ((1 << TL_BITS) - 1)


def unpack(raw):
    """Split capture words into the two channels' signed 10-bit samples: {"A": ..., "B": ...}
    (int16 arrays; the ADC's default output is two's complement)."""
    import numpy as np

    raw = np.asarray(raw, dtype=np.uint32)
    out = {}
    for name, shift in (("A", 0), ("B", 16)):
        s = ((raw >> shift) & 0x3FF).astype(np.int16)
        out[name] = np.where(s >= 512, s - 1024, s).astype(np.int16)
    return out


def open_board(port=None, sim=False):
    if sim:
        from sim import SimFpga

        return Board(transport=SimFpga())
    return Board(port)


class AppBoard:
    """The board as the running desktop app holds it: text commands over localhost
    (app/share.py). Events stay in the app."""

    def __init__(self, port=APP_PORT):
        self.port = port
        self.on_event = None
        self.cmd("id")  # raises ConnectionRefusedError when no connected app is listening

    def cmd(self, line):
        import json
        import socket

        with socket.create_connection(("127.0.0.1", self.port), timeout=35) as s:
            s.sendall((json.dumps({"cmd": line}) + "\n").encode())
            reply = json.loads(s.makefile().readline())
        if not reply["ok"]:
            raise BoardError(reply["error"])
        return reply["lines"]

    def ident(self):
        return self.cmd("id")[0] + " (through the app)"

    def poll(self):
        pass

    def close(self):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="FT2232H serial of channel B (ends in B)")
    ap.add_argument("--sim", action="store_true", help="use the simulated board")
    ap.add_argument("--direct", action="store_true", help="open the board even if the app is running")
    ap.add_argument("command", nargs="*")
    a = ap.parse_args()
    b, via = None, ""
    if not (a.port or a.sim or a.direct):
        try:
            b = AppBoard()
        except OSError:
            via = ", and no connected app on port %d" % APP_PORT  # open the board here
        except BoardError as e:
            sys.exit("ERR " + str(e))
    try:
        b = b or open_board(a.port, a.sim)
    except BoardError as e:
        sys.exit("ERR " + str(e) + via)
    b.on_event = lambda text: print("!", text, file=sys.stderr)

    if a.command:
        try:
            print("\n".join(b.cmd(" ".join(a.command))))
        except BoardError as e:
            sys.exit("ERR " + str(e))
        return

    print(b.ident())
    print("`help` lists commands, Ctrl-C quits")
    while True:
        try:
            line = input("scum> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line.strip():
            continue
        try:
            b.poll()
            for r in b.cmd(line):
                print(r)
        except BoardError as e:
            print("ERR", e)


if __name__ == "__main__":
    main()

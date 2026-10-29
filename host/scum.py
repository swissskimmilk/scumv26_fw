"""Host side of the SCuMV26 bring-up board.

    python scum.py                 interactive prompt (type `help`)
    python scum.py power up        run one command and print the reply
    python scum.py --sim freq      talk to the simulated board instead (sim.py)

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

Blocks the gateware does not have yet (FEATURES register) answer `ERR not in the gateware
yet`: telemetry, ilim, aux and VBAT need the I2C master, cap and adc the ADC block, uart the
UART bridge.

Addresses, bit positions and packet bytes come from fpga/regmap.py, the file that also
generates the RTL package, so the two cannot drift apart.

Needs the FTDI D2XX driver (Windows: installed with Vivado's cable drivers or FTDI's CDM
package; macOS: FTDI's D2XX package) and `pip install ftd2xx`.
"""
import argparse
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
CAP_MAX_WORDS = 96 * 1024
SEQ_SETTLE_S = 0.020  # regulator soft-start margin between power-up steps
FEATURE_BLOCK = {"I2C": "the I2C master", "ADC": "the ADC capture block", "UART": "the UART bridge"}


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
sw [vddd|vdda|rf board|dut]          rail-source switches
trip [on|off|clear]                  hardware over-current trip on TELEM_ALERT
clk [sel dig|ana|off] [win ms]       DUT clock counter: LMK1D input and counting window
freq                                 DUT clock frequency from the next complete window
pin [name [0|1|in|out]]              read a pin, drive it, or turn it around
pulse <name> <us>                    invert an output for <us> microseconds (timed in the FPGA)
reg <addr> [value]                   raw register read or write
tele | ilim | aux | vbat | trim      need the I2C master (not in the gateware yet)
adc ... | cap <words>                need the ADC block (not in the gateware yet)
uart baud | tx | rx                  need the UART bridge (not in the gateware yet)"""


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
            self._event("ALERT over-current trip: all DUT rails switched off")
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

    def _cmd_pulse(self, a):
        self.pulse(a[0], int(a[1]))
        return []

    def _cmd_reg(self, a):
        addr = int(a[0], 0)
        if len(a) > 1:
            self.link.write(addr, int(a[1], 0))
        return ["0x%04X = 0x%08X" % (addr, self.link.read(addr))]

    def _cmd_tele(self, a):
        self._need("I2C")

    _cmd_ilim = _cmd_aux = _cmd_vbat = _cmd_trim = _cmd_dac = _cmd_i2c = _cmd_tele

    def _cmd_adc(self, a):
        self._need("ADC")

    _cmd_cap = _cmd_adc

    def _cmd_uart(self, a):
        self._need("UART")

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

    def switches(self):
        """{"vddd"|"vdda"|"rf": "board"|"dut"}."""
        v = self.r("RAIL_SW")
        return {k: "board" if v & bit("RAIL_SW", f) else "dut" for k, f in RAIL_SW.items()}

    def set_switch(self, name, src):
        v = self.r("RAIL_SW")
        m = bit("RAIL_SW", RAIL_SW[name])
        self.w("RAIL_SW", (v | m) if src == "board" else (v & ~m))

    def power_up(self):
        """VIO -> VBAT -> 1 V rails. A latched trip is cleared first, unless ALERT is still
        asserted. (The INA237 check that VIO came up returns with the I2C block.)"""
        if self.tripped():
            if self.r("TRIP_STATUS") & bit("TRIP_STATUS", "ALERT"):
                raise BoardError("over-current ALERT is still asserted; not powering up")
            self.clear_trip()
        for rail in ("vio", "vbat", "1v"):
            self.set_rail(rail, True)
            time.sleep(SEQ_SETTLE_S)

    def power_down(self):
        for rail in ("1v", "vbat", "vio"):
            self.set_rail(rail, False)
            time.sleep(SEQ_SETTLE_S)

    def trip_enable(self, on=True, active_high=False):
        self.w("TRIP_CTRL", (bit("TRIP_CTRL", "EN") if on else 0)
               | (bit("TRIP_CTRL", "ACTIVE_HIGH") if active_high else 0))

    def tripped(self):
        return bool(self.r("TRIP_STATUS") & bit("TRIP_STATUS", "TRIPPED"))

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

    # blocks still to come

    def tele(self):
        """{rail: {"v", "ma", "mv_shunt"} or None}."""
        self._need("I2C")

    def ilim(self):
        self._need("I2C")

    def aux(self):
        self._need("I2C")

    def vbat(self, volts=None):
        self._need("I2C")

    def uart_rx(self):
        self._need("UART")

    def capture(self, words):
        self._need("ADC")


def unpack(raw, mode):
    """Split capture words into signed 10-bit samples: bits 9:0 first, bits 25:16 second. The
    mode's last letter (ddr-a, sdr-b, ...) names the channel. (Kept for the ADC block.)"""
    import numpy as np

    s = np.empty(raw.size * 2, dtype=np.int16)
    s[0::2] = raw & 0x3FF
    s[1::2] = (raw >> 16) & 0x3FF
    s = np.where(s >= 512, s - 1024, s)  # ADC default output is two's complement
    return {mode[-1].upper(): s}


def open_board(port=None, sim=False):
    if sim:
        from sim import SimFpga

        return Board(transport=SimFpga())
    return Board(port)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="FT2232H serial of channel B (ends in B)")
    ap.add_argument("--sim", action="store_true", help="use the simulated board")
    ap.add_argument("command", nargs="*")
    a = ap.parse_args()
    try:
        b = open_board(a.port, a.sim)
    except BoardError as e:
        sys.exit("ERR " + str(e))
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

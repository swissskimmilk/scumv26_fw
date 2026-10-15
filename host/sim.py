"""A simulated bring-up board, for developing the host tools without hardware.

SimSerial stands in for the pyserial port: it answers the firmware's text protocol with
replies formatted exactly as the firmware formats them (see App/*.c), from a crude model of
the board: rails that follow their enables, currents with noise, INA237 over-current trips,
a DUT clock, an ADC that sees two tones (or the test pattern), and a DUT that prints on its
UART while powered.

    from scum import Board
    from sim import SimSerial
    b = Board(transport=SimSerial())
"""
import math
import random
import time
import zlib

import numpy as np

FW_ID = "scumv26_fw 0.1.0-sim Oct 14 2026 00:00:00 uid 00000000SIMULATED0000"

VFB, R_TOP, R_BOT, R_DAC, DAC_VREF, DAC_MAX = 0.8, 10e3, 13.7e3, 36.4e3, 1.8, 4095
DAC_LO, DAC_HI = int(0.2 / DAC_VREF * DAC_MAX), int((DAC_VREF - 0.2) / DAC_VREF * DAC_MAX)

MONS = [("vio", 0x40, 2.0), ("vbat", 0x41, 0.82), ("vbat_rf", 0x45, 1.8),
        ("vddd", 0x43, 1.8), ("vdda", 0x44, 5.6), ("rf_vdd", 0x47, 1.8)]
LOAD_MA = {"vio": 0.4, "vbat": 2.5, "vbat_rf": 0.9, "vddd": 7.5, "vdda": 1.8, "rf_vdd": 3.2}

PINS = [("CORE_RST", True), ("BOOT_SEL", True), ("CORE_GPIO", False), ("CLK_DEBUG_MUX", True),
        ("SCAN_CLK", True), ("SCAN_IN", True), ("SCAN_EN", True), ("SCAN_SEL", True),
        ("SCAN_RST", True), ("SCAN_OUT", False), ("TL_CLK", True), ("TL_VAL_IN", True),
        ("TL_DAT_IN", True), ("RDY_OUT", True), ("TL_VAL_OUT", False), ("TL_DAT_OUT", False),
        ("RDY_IN", False), ("BASEBAND_TX", False), ("BASEBAND_RX", False),
        ("NFC_DEMOD_MCU", False), ("NFC_MDM_DBG", False)]

AUX = ["VNFC", "PWR_BG", "PWR_TEST_0", "PWR_I_REF", "VIO", "PWR_VDD_RF", "SPARE_1", "SPARE_2"]
MODES = ["ddr-a", "ddr-b", "sdr-a", "sdr-b"]
FS = 32e6


class CmdError(Exception):
    pass


def vbat_from_dac(code):
    vdac = code * DAC_VREF / DAC_MAX
    return VFB + R_TOP * (VFB / R_BOT - (vdac - VFB) / R_DAC)


def dac_from_vbat(v):
    return round((VFB + R_DAC * (VFB / R_BOT - (v - VFB) / R_TOP)) / DAC_VREF * DAC_MAX)


class SimSerial:
    def __init__(self):
        self._out = bytearray()
        self._in = b""
        self._t0 = time.monotonic()
        self.en = {"vio": False, "vbat": False, "1v": False}
        self.sw = {"vddd": True, "vdda": True, "rf": True}  # True = board
        self.dac = round(VFB / DAC_VREF * DAC_MAX)
        self.ilim = {m[0]: 0.0 for m in MONS}
        self.counters = dict.fromkeys(
            ["ocp_trips", "capture_errors", "uart_dropped", "host_dropped", "button_resets"], 0)
        self.ocp_rails = []
        self.clk = {"sel": "dig", "psc": 4, "win": 100}
        self.pins = {n: (out, n == "CORE_RST") for n, out in PINS}
        self.adc_mode = 0
        self.pattern = None
        self.regs = {0x39: 0x00}
        self.uart_buf = bytearray()
        self._uart_next = time.monotonic()
        self._boots = 0

    # ---- pyserial surface ---------------------------------------------------------------

    @property
    def in_waiting(self):
        self._tick()
        return len(self._out)

    def reset_input_buffer(self):
        self._out.clear()

    def close(self):
        pass

    def write(self, data):
        self._in += data
        while b"\n" in self._in:
            line, self._in = self._in.split(b"\n", 1)
            self._execute(line.decode(errors="replace"))
        return len(data)

    def readline(self):
        self._tick()
        i = self._out.find(b"\n")
        n = len(self._out) if i < 0 else i + 1
        data = bytes(self._out[:n])
        del self._out[:n]
        return data

    def read(self, n):
        data = bytes(self._out[:n])
        del self._out[:n]
        return data

    # ---- model ------------------------------------------------------------------------

    def _emit(self, text):
        self._out += text.encode()

    def _powered(self):
        return self.en["vio"] and self.en["vbat"] and self.en["1v"]

    def _mon(self, name):
        """(vbus, mA) at a monitor; None never: every simulated monitor answers."""
        vbat = vbat_from_dac(self.dac)
        noise = random.gauss(0, 0.004)
        if name == "vio":
            return (1.8, LOAD_MA[name]) if self.en["vio"] else (0.0, 0.0)
        if name in ("vbat", "vbat_rf"):
            return (vbat - 0.001, LOAD_MA[name] + noise) if self.en["vbat"] else (0.0, 0.0)
        sw = {"vddd": "vddd", "vdda": "vdda", "rf_vdd": "rf"}[name]
        if self.sw[sw]:
            if not self.en["1v"]:
                return (0.0, 0.0)
            load = LOAD_MA[name] * (1.6 if self._powered() else 0.2)
            return (1.0, load + noise)
        # DUT's own LDO drives the rail: the board regulator sees nothing.
        return (0.95 if self.en["vbat"] else 0.0, 0.0)

    def _tick(self):
        now = time.monotonic()
        tripped = [n for n, *_ in MONS if self.ilim[n] > 0 and self._mon(n)[1] > self.ilim[n]]
        if tripped:
            self.en = dict.fromkeys(self.en, False)
            self.counters["ocp_trips"] += 1
            self.ocp_rails = sorted(set(self.ocp_rails) | set(tripped))
            self._emit("! ALERT: over-current trip, all DUT rails off: %s\n" % " ".join(tripped))
        if self._powered() and now >= self._uart_next:
            self._uart_next = now + 2.0
            self._boots += 1
            self.uart_buf += b"SCuM-V26 sim: heartbeat %d\r\n" % self._boots

    # ---- commands ---------------------------------------------------------------------

    def _execute(self, line):
        argv = line.split()[:32]
        if not argv:
            return
        fn = getattr(self, "cmd_" + argv[0], None)
        if fn is None:
            self._emit("ERR unknown command '%s' (try help)\n" % argv[0])
            return
        try:
            if fn(argv) is not False:
                self._emit("OK\n")
        except CmdError as e:
            self._emit("ERR %s\n" % e)
        except (ValueError, IndexError):
            self._emit("ERR bad arguments\n")

    def cmd_help(self, argv):
        for name in sorted(n[4:] for n in dir(self) if n.startswith("cmd_")):
            self._emit("  %s\n" % name)

    def cmd_id(self, argv):
        self._emit(FW_ID + "\n")

    def cmd_status(self, argv):
        peek = len(argv) == 2 and argv[1] == "peek"
        self._emit("uptime_s %d\n" % (time.monotonic() - self._t0))
        self._emit("mcu_reset pin\n")
        self._emit("rails vio=%d vbat=%d 1v=%d\n" % (self.en["vio"], self.en["vbat"], self.en["1v"]))
        self._emit("ocp_trips %d%s\n" % (self.counters["ocp_trips"],
                                         "".join(" " + r for r in self.ocp_rails)))
        for k in ("capture_errors", "uart_dropped", "host_dropped", "button_resets"):
            self._emit("%s %d\n" % (k, self.counters[k]))
        if not peek:
            self.counters = dict.fromkeys(self.counters, 0)
            self.ocp_rails = []

    def _report(self, name):
        v, ma = self._mon(name)
        self._emit("  %-8s %.3f V  %.3f mA\n" % (name, v, ma))

    def cmd_power(self, argv):
        if argv[1:] == ["up"]:
            self.en["vio"] = True
            self._report("vio")
            self.en["vbat"] = True
            self._report("vbat")
            self._report("vbat_rf")
            self.en["1v"] = True
            for n in ("vddd", "vdda", "rf_vdd"):
                self._report(n)
            time.sleep(0.06)
        elif argv[1:] == ["down"]:
            self.en = dict.fromkeys(self.en, False)
            time.sleep(0.06)
        else:
            raise CmdError("usage: power up|down")

    def cmd_rail(self, argv):
        if len(argv) == 1:
            self._emit("vio  %s\nvbat %s\n1v   %s\n" % tuple(
                "on" if self.en[k] else "off" for k in ("vio", "vbat", "1v")))
            return
        if len(argv) != 3 or argv[2] not in ("on", "off", "1", "0"):
            raise CmdError("usage: rail vio|vbat|1v on|off")
        if argv[1] not in self.en:
            raise CmdError("unknown rail '%s'" % argv[1])
        self.en[argv[1]] = argv[2] in ("on", "1")

    def cmd_sw(self, argv):
        if len(argv) == 1:
            self._emit("vddd %s\nvdda %s\nrf   %s\n" % tuple(
                "board" if self.sw[k] else "dut" for k in ("vddd", "vdda", "rf")))
            return
        if len(argv) != 3 or argv[2] not in ("board", "dut"):
            raise CmdError("usage: sw vddd|vdda|rf board|dut")
        if argv[1] not in self.sw:
            raise CmdError("unknown switch '%s'" % argv[1])
        self.sw[argv[1]] = argv[2] == "board"

    def _dac_line(self):
        self._emit("dac %d (%.4f V) -> VBAT set point %.4f V\n" % (
            self.dac, self.dac * DAC_VREF / DAC_MAX, vbat_from_dac(self.dac)))

    def cmd_vbat(self, argv):
        if len(argv) == 2:
            code = dac_from_vbat(float(argv[1]))
            if not DAC_LO <= code <= DAC_HI:
                raise CmdError("%.3f V is outside the trim range %.3f-%.3f V" % (
                    float(argv[1]), vbat_from_dac(DAC_HI), vbat_from_dac(DAC_LO)))
            self.dac = code
        self._dac_line()

    def cmd_dac(self, argv):
        if len(argv) == 2:
            code = int(argv[1], 0)
            if not 0 <= code <= DAC_MAX:
                raise CmdError("usage: dac [0-4095]")
            self.dac = code
        self._dac_line()

    def cmd_trim(self, argv):
        target = float(argv[1])
        if not self.en["vbat"]:
            raise CmdError("VBAT is off")
        code = dac_from_vbat(target)
        if not DAC_LO <= code <= DAC_HI:
            raise CmdError("%.3f V is outside the trim range" % target)
        self.dac = code + 9  # the open-loop guess is off by the resistor tolerances
        for i in range(8):
            time.sleep(0.08)
            v = vbat_from_dac(self.dac) - 0.001
            err = target - v
            self._emit("  iter %d: dac %d  VBAT %.4f V  err %+.1f mV\n" % (i, self.dac, v, err * 1e3))
            if abs(err) < 1e-3:
                return
            self.dac += round(err / (-R_TOP / R_DAC) / DAC_VREF * DAC_MAX)
        raise CmdError("did not converge to 1 mV")

    def cmd_tele(self, argv):
        self._emit("rail      addr  shunt     Vbus      I         Vshunt\n")
        for name, addr, shunt in MONS:
            v, ma = self._mon(name)
            self._emit("%-9s 0x%02X  %5.2f ohm  %7.4f V  %8.3f mA  %8.3f mV\n" % (
                name, addr, shunt, v, ma, ma * shunt))

    def cmd_ilim(self, argv):
        if len(argv) == 1:
            for name, *_ in MONS:
                lim = self.ilim[name]
                self._emit("%-9s %s\n" % (name, "%.2f mA" % lim if lim > 0 else "off"))
            return
        if len(argv) != 3 or argv[1] not in self.ilim or float(argv[2]) < 0:
            raise CmdError("usage: ilim <vio|vbat|vbat_rf|vddd|vdda|rf_vdd> <mA>")
        self.ilim[argv[1]] = float(argv[2])

    def cmd_aux(self, argv):
        p = self._powered()
        vals = [0.42 if p else 0, 0.6 if p else 0, 0.31 if p else 0, 0.25 if p else 0,
                1.8 if self.en["vio"] else 0, 1.0 if p else 0, 0.0, 0.0]
        for ch, (name, v) in enumerate(zip(AUX, vals)):
            self._emit("AIN%d %-11s %6.4f V\n" % (ch, name, max(0.0, v + random.gauss(0, 0.0008))))

    def cmd_i2c(self, argv):
        for a in sorted([0x10] + [m[1] for m in MONS]):
            self._emit("0x%02X\n" % a)

    def _clk_line(self):
        c = self.clk
        self._emit("sel %s  psc %d  win %d ms  resolution %.1f Hz\n" % (
            c["sel"], c["psc"], c["win"], c["psc"] * 1000.0 / c["win"]))

    def cmd_clk(self, argv):
        if len(argv) % 2 == 0:
            raise CmdError("usage: clk [sel dig|ana|off] [psc 1|2|4|8] [win ms]")
        for k, v in zip(argv[1::2], argv[2::2]):
            if k == "sel" and v in ("dig", "ana", "off"):
                self.clk["sel"] = v
            elif k == "psc" and v in ("1", "2", "4", "8"):
                self.clk["psc"] = int(v)
            elif k == "win" and 1 <= int(v) <= 6000:
                self.clk["win"] = int(v)
            else:
                raise CmdError("bad option '%s %s'" % (k, v))
        self._clk_line()

    def cmd_freq(self, argv):
        c = self.clk
        hz = 0.0
        if self._powered() and c["sel"] != "off":
            hz = (20e6 if c["sel"] == "dig" else 2.4e6) * (1 + random.gauss(0, 2e-6))
        counts = int(hz * c["win"] / 1000 / c["psc"])
        hz = counts * c["psc"] * 1000.0 / c["win"]
        time.sleep(min(c["win"], 300) / 1000)
        self._emit("%.1f Hz  (%d counts x%d in %d ms)\n" % (hz, counts, c["psc"], c["win"]))

    def cmd_adc(self, argv):
        sub = argv[1] if len(argv) > 1 else ""
        if sub in ("init", "reset"):
            self.pattern = None
            if sub == "init":
                self._emit("ADC3910 ready, mode %s\n" % MODES[self.adc_mode])
        elif sub == "rd":
            a = int(argv[2], 0)
            self._emit("0x%03X = 0x%02X\n" % (a, self.regs.get(a, 0)))
        elif sub == "wr":
            self.regs[int(argv[2], 0)] = int(argv[3], 0) & 0xFF
        elif sub == "mode" and len(argv) == 3:
            if argv[2] not in MODES:
                raise CmdError("modes: ddr-a, ddr-b, sdr-a, sdr-b")
            self.adc_mode = MODES.index(argv[2])
        elif sub == "pat" and argv[2:] == ["off"]:
            self.pattern = None
        elif sub == "pat" and len(argv) == 4:
            self.pattern = (int(argv[2], 0) & 0x3FF, int(argv[3], 0) & 0x3FF)
        else:
            raise CmdError("bad adc command")

    def cmd_cap(self, argv):
        words = int(argv[1]) if len(argv) == 2 else 0
        if not 1 <= words <= 96 * 1024:
            raise CmdError("usage: cap <1-%d words>" % (96 * 1024))
        chan = MODES[self.adc_mode][-1]
        n = words * 2
        if self.pattern is not None:
            s = np.full(n, self.pattern[0 if chan == "a" else 1], dtype=np.int64)
        else:
            t = np.arange(n) / FS + random.random()
            f = 1.25e6 if chan == "a" else 2.1e6
            s = 380 * np.sin(2 * math.pi * f * t) + 40 * np.sin(2 * math.pi * 3 * f * t + 0.3)
            s = np.round(s + np.random.normal(0, 2.0, n)).astype(np.int64) & 0x3FF
        data = ((s[0::2] & 0x3FF) | ((s[1::2] & 0x3FF) << 16)).astype("<u4").tobytes()
        ms = max(1, round(n / FS * 1e3))
        self._emit("BIN %d %s %d %08X\n" % (len(data), MODES[self.adc_mode], ms, zlib.crc32(data)))
        self._out += data

    def cmd_uart(self, argv):
        sub = argv[1] if len(argv) > 1 else ""
        if sub == "baud" and len(argv) == 3:
            return
        if sub == "tx" and len(argv) >= 3:
            if self._powered():
                self.uart_buf += b"> " + " ".join(argv[2:]).encode() + b"\r\n"
            return
        if sub == "rx" and len(argv) == 2:
            data, self.uart_buf = bytes(self.uart_buf), bytearray()
            self._emit("".join(chr(c) if 0x20 <= c < 0x7F and c != 0x5C else "\\x%02X" % c
                               for c in data) + "\n")
            return
        raise CmdError("usage: uart baud <n> | uart tx <text> | uart rx")

    def _pin(self, name):
        for n in self.pins:
            if n.lower() == name.lower():
                return n
        raise CmdError("unknown pin '%s' (pin lists them)" % name)

    def _level(self, n):
        out, lvl = self.pins[n]
        if not out:
            if not self._powered():
                return False
            if n == "CORE_GPIO":
                return int(time.monotonic()) % 2 == 1
        return lvl

    def cmd_pin(self, argv):
        if len(argv) == 1:
            for n, (out, _) in self.pins.items():
                self._emit("%-14s %s %d\n" % (n, "out" if out else "in ", self._level(n)))
            return
        n = self._pin(argv[1])
        if len(argv) == 2:
            self._emit("%d\n" % self._level(n))
            return
        if not self.pins[n][0]:
            raise CmdError("%s is driven by the DUT" % n)
        self.pins[n] = (True, argv[2] in ("1", "on"))

    def cmd_pulse(self, argv):
        n = self._pin(argv[1]) if len(argv) == 3 else None
        if n is None or not self.pins[n][0] or not 1 <= int(argv[2]) <= 1000000:
            raise CmdError("usage: pulse <output pin> <1-1000000 us>")
        time.sleep(int(argv[2]) / 1e6)
        if n == "CORE_RST" and self._powered():
            self.uart_buf += b"\r\nSCuM-V26 sim: reset, bootloader waiting\r\n"

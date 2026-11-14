"""A simulated FPGA board, for developing the host tools without hardware.

SimFpga stands in for the D2XX device on the FT2232H's channel B: it takes the host packets
(fpga/regmap.py) and answers them from a model of the gateware's registers, behaving as the
RTL does: rail enables that a trip clears and holds off, w1c event flags, cumulative counters,
a DUT clock counter whose windows complete in real time, pads released until VIO is enabled
and power good, timed pulses, and the J16 GPIO.

    from scum import Board
    from sim import SimFpga
    b = Board(transport=SimFpga())

Set `alert = True` on the SimFpga to pull TELEM_ALERT by hand, and add RAIL_PG field names to
`pg_fail` (e.g. {"VDDA"}) for a regulator that never reports power good. `gpio_in` is what the
header drives onto pins the FPGA does not (default 0: the pull-downs).

The telemetry I2C bus carries models of the board's devices, answering at their addresses: six
INA237s (`ina[rail]`), whose bus voltage and current follow the rail enables, `load_ma[rail]`
and, for VBAT, the DAC; the TLA2528 (`tla.volts[ch]`); and the VBAT DAC53401. An INA237 whose
shunt reading crosses its SOVL limit pulls ALERT, latched if its ALATCH bit is set, as on the
board. Add an address to `i2c_dead` for a device that does not answer.

The ADC3910 answers its SPI from a register file (`adc.reg`; CFG_ALERT comes up after a reset).
A capture fills the buffer at once with an I/Q tone on the DUT's 2 MHz IF (`adc.tone_hz`,
`adc.amplitude` in codes; A the cosine, B the sine) plus a little noise, or with the test-pattern
codes while they are enabled. `adc.dclk = False` stops the clock: captures then never finish.

While the DUT is powered it clocks its serial TileLink port (`stl.clock`), and frames queued in
TL_TX reach `stl`, a word memory (`stl.mem`, keyed by 8-byte-aligned address) that answers Get,
PutFullData and PutPartialData as the chip would. The DUT UART echoes what it is sent at
`uart_dut_baud` (`uart_echo`), and `uart_say(data)` sends bytes from the DUT; at any other baud
rate a byte arrives as a framing error.
"""
import random
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fpga"))
import regmap as rm  # noqa: E402
from scum import (INA, TL_A, TL_ACK, TL_ACK_DATA, TL_D, TL_GET, TL_PUT_FULL,  # noqa: E402
                  TL_PUT_PARTIAL, tl_pack, tl_unpack, vbat_from_dac as VBAT)

A = {name: addr for name, addr, *_ in rm.REGS}
NAME = {addr: name for name, addr in A.items()}
F = {name: {f: 1 << lsb for f, (lsb, _, _) in fields.items()} for name, _, _, _, _, fields in rm.REGS}
PIN = {name: i for i, (name, *_) in enumerate(rm.DUT_PINS)}
CLK_HZ = {1: 20e6, 2: 2.4e6}  # what the DUT puts out on dig_out / ana_out while it is powered
PKT_LEN = {rm.CMD_WRITE: 7, rm.CMD_READ: 3, rm.CMD_BURST: 7}


class SimFpga:
    def __init__(self, features=rm.FEATURES):
        self._rx = bytearray()   # host -> board
        self._tx = bytearray()   # board -> host
        self._t0 = time.monotonic()
        self.features = features
        self.alert = False
        self.pg_fail = set()
        self.gpio_in = 0
        self.reg = {name: reset for name, _, _, reset, _, _ in rm.REGS}
        self.counts = {"TRIP_COUNT": 0, "BUTTON_COUNT": 0, "FRAME_COUNT": 0}
        self.tripped = False
        self.pulse_end = 0.0
        self.pulse_mask = 0
        self._clk_restart()
        # telemetry bus
        self.load_ma = {"vio": 3.0, "vbat": 0.6, "vbat_rf": 0.3, "vddd": 4.0, "vdda": 1.5, "rf_vdd": 1.0}
        self.ina = {rail: SimIna237(self, rail, ohms) for rail, (_, ohms) in INA.items()}
        self.tla = SimTla2528()
        self.dac = SimDac53401()
        self.i2c_dead = set()
        self._i2c_dev = {a: self.ina[r] for r, (a, _) in INA.items()}
        self._i2c_dev[0x10] = self.tla
        self._i2c_dev[0x48] = self.dac
        self._i2c_status = 0
        # ADC3910 and the capture buffer
        self.adc = SimAdc3910()
        self._buf = None
        self._cap_busy = False
        self._cap_addr = 0
        self.adc_rdata = 0
        # serial TileLink and the DUT UART
        self.stl = SimStlChip()
        self._tl_tx = []         # words waiting in TL_TX
        self._tl_rx = []         # words waiting in TL_RX
        self.uart_dut_baud = 115200
        self.uart_echo = True
        self._uart_rx = bytearray()

    # ---- D2XX device interface ----------------------------------------------------------

    def write(self, data):
        self._rx += data
        while self._rx:
            n = PKT_LEN.get(self._rx[0])
            if n is None:  # not a packet start: the bridge drops it and counts it
                self._event("FRAME")
                del self._rx[0]
                continue
            if len(self._rx) < n:
                break
            pkt, self._rx = bytes(self._rx[:n]), self._rx[n:]
            self._packet(pkt)
        return len(data)

    def read(self, n):
        out, self._tx = bytes(self._tx[:n]), self._tx[n:]
        return out

    def purge(self, *_):
        self._rx.clear()
        self._tx.clear()

    def close(self):
        pass

    def setTimeouts(self, *_):
        pass

    def setLatencyTimer(self, *_):
        pass

    def setUSBParameters(self, *_):
        pass

    # ---- model --------------------------------------------------------------------------

    def _now(self):
        return time.monotonic() - self._t0

    def rail_v(self, rail):
        """The voltage a monitor sees: its regulator's output while enabled, else 0."""
        en, sw = self.reg["RAIL_EN"], self.reg["RAIL_SW"]
        if rail == "vio":
            return 1.8 if en & F["RAIL_EN"]["VIO"] else 0.0
        if rail in ("vbat", "vbat_rf"):
            return VBAT(self.dac.vout()) if en & F["RAIL_EN"]["VBAT"] else 0.0
        if not en & F["RAIL_EN"]["VDD"]:
            return 0.0
        field = {"vddd": "VDDD", "vdda": "VDDA", "rf_vdd": "RF"}[rail]
        if not sw & F["RAIL_SW"][field]:
            return 0.0  # fed by the DUT's own LDO: the board's monitor sees nothing
        return 0.865 if rail == "vddd" else 1.015

    def rail_ma(self, rail):
        return self.load_ma[rail] * (1 + random.gauss(0, 1e-3)) if self.rail_v(rail) else 0.0

    def _alert(self):
        """TELEM_ALERT: by hand, or any INA237 over its limit (or latched)."""
        return self.alert or any(ina.alert() for ina in self.ina.values())

    def _capture(self):
        if not self.adc.dclk:
            self._cap_busy = True  # never finishes, as on the board without DCLK
            return
        self._buf = self.adc.samples(self.reg["CAP_LEN"])
        self._cap_busy = False

    def _tl_run(self):
        """Frames go out while the engine is on and the DUT clocks the link."""
        if not (self.reg["TL_CTRL"] & 1 and self._powered() and self.stl.clock):
            return
        bits = self.reg["TL_FRAME"]
        n = (bits + 31) // 32
        while len(self._tl_tx) >= n and len(self._tl_rx) + n <= rm.TL_FIFO:
            words, self._tl_tx = self._tl_tx[:n], self._tl_tx[n:]
            frame = sum(w << 32 * i for i, w in enumerate(words)) & ((1 << bits) - 1)
            ans = self.stl.handle(frame)
            if ans is not None:
                self._tl_rx += [ans >> 32 * i & 0xFFFFFFFF for i in range(n)]

    def _uart_baud_ok(self):
        return abs(rm.SYS_HZ / self.reg["UART_DIV"] - self.uart_dut_baud) < self.uart_dut_baud * 0.02

    def uart_say(self, data):
        """Bytes from the DUT's UART, if it is powered."""
        if not self._pg() & F["RAIL_PG"]["VIO"]:
            return
        for b in bytes(data):
            if not self._uart_baud_ok():
                self.reg["UART_EVENTS"] |= F["UART_EVENTS"]["FRAMING"]
                b = random.randrange(256)
            if len(self._uart_rx) >= rm.UART_RX_FIFO:
                self.reg["UART_EVENTS"] |= F["UART_EVENTS"]["OVERRUN"]
            else:
                self._uart_rx.append(b)

    def _i2c(self, cmd):
        """Run one I2C_CMD at once (the sim has no bus timing): set the status flags and data."""
        st = F["I2C_STATUS"]
        self._i2c_status = st["SCL"] | st["SDA"]
        if cmd & F["I2C_CMD"]["RECOVER"]:
            return
        addr, wlen, rlen = cmd & 0x7F, min(4, cmd >> 8 & 7), min(4, cmd >> 12 & 7)
        dev = None if addr in self.i2c_dead else self._i2c_dev.get(addr)
        if dev is None:
            self._i2c_status |= st["ANACK"]
            return
        w = self.reg["I2C_WDATA"].to_bytes(4, "little")[:wlen]
        r = dev.xfer(w, rlen)
        self.reg["I2C_RDATA"] = int.from_bytes(r.ljust(4, b"\0"), "little")

    def _event(self, name):
        self.reg["EVENTS"] |= F["EVENTS"][name]
        self.counts[name + "_COUNT"] += 1

    def _clk_restart(self):
        self.clk_t0 = self._now()

    def _update(self):
        """Advance what the hardware does on its own: the trip and the pulse timer."""
        # `alert` is the asserted state, so TRIP_CTRL.ACTIVE_HIGH does not matter here
        if self.reg["TRIP_CTRL"] & F["TRIP_CTRL"]["EN"] and self._alert() and not self.tripped:
            self.tripped = True
            self.reg["RAIL_EN"] = 0
            self._event("TRIP")
        if self.pulse_mask and self._now() >= self.pulse_end:
            self.pulse_mask = 0
        self._tl_run()

    def _powered(self):
        en = self.reg["RAIL_EN"]
        return all(en & F["RAIL_EN"][r] for r in ("VIO", "VBAT", "VDD"))

    def _pg(self):
        """RAIL_PG: each regulator is good as soon as it is enabled (the sim has no soft start);
        the system rails always are."""
        en, good = self.reg["RAIL_EN"], {"SYS_1V8", "SYS_3V3", "FPGA_1V8"}
        for rail, pgs in (("VIO", ("VIO",)), ("VBAT", ("VBAT",)), ("VDD", ("VDDD", "VDDA", "RF"))):
            if en & F["RAIL_EN"][rail]:
                good.update(pgs)
        return sum(F["RAIL_PG"][f] for f in good - self.pg_fail)

    def _pads(self):
        """DUT_IN: driven pins read back what is driven; the DUT's outputs are modelled crudely
        (SCAN_OUT follows SCAN_IN, CORE_GPIO toggles at 1 Hz) while it is powered."""
        if not self._pg() & F["RAIL_PG"]["VIO"]:
            return 0
        oe, out = self.reg["DUT_OE"], self.reg["DUT_OUT"] ^ self.pulse_mask
        dut = 0
        if self._powered():
            dut |= (out >> PIN["SCAN_IN"] & 1) << PIN["SCAN_OUT"]
            dut |= (int(self._now()) & 1) << PIN["CORE_GPIO"]
        return (out & oe) | (dut & ~oe)

    def _clk(self, name):
        win = self.reg["CLK_WINDOW"] / rm.SYS_HZ
        n = int((self._now() - self.clk_t0) / win)
        if name == "CLK_SEQ":
            return max(0, n - 1)  # the first window after a restart is discarded
        hz = CLK_HZ.get(self.reg["CLK_CTRL"] & 3, 0) if self._powered() else 0
        return round(hz * (1 + random.gauss(0, 2e-6)) * win) if n >= 2 else 0

    def _read(self, addr):
        self._update()
        name = NAME.get(addr)
        if name is None:
            return 0
        if name == "UPTIME_MS":
            return int(self._now() * 1000) & 0xFFFFFFFF
        if name in self.counts:
            return self.counts[name]
        if name == "FEATURES":
            return self.features
        if name == "TRIP_STATUS":
            return (F["TRIP_STATUS"]["TRIPPED"] if self.tripped else 0) | \
                   (F["TRIP_STATUS"]["ALERT"] if self._alert() else 0)
        if name in ("CLK_COUNT", "CLK_SEQ"):
            return self._clk(name)
        if name == "DUT_IN":
            return self._pads()
        if name == "RAIL_PG":
            return self._pg()
        if name == "GPIO_IN":
            oe = self.reg["GPIO_OE"]
            return (self.reg["GPIO_OUT"] & oe) | (self.gpio_in & ~oe & 0x3F)
        if name == "DUT_PULSE":
            return self.reg[name] | (1 << 31 if self.pulse_mask else 0)
        if name == "I2C_STATUS":
            return self._i2c_status
        if name == "ADC_SPI_STATUS":
            return self.adc_rdata << 8
        if name == "ADC_DCLK_KHZ":
            return 32000 if self.adc.dclk else 0
        if name == "CAP_STATUS":
            if self._cap_busy:
                return F["CAP_STATUS"]["BUSY"]
            return F["CAP_STATUS"]["DONE"] if self._buf is not None else 0
        if name == "CAP_ADDR":
            return self._cap_addr
        if name == "TL_STATUS":
            return (len(self._tl_rx) | (rm.TL_FIFO - len(self._tl_tx)) << 16
                    | (1 << 31 if self._tl_tx else 0))
        if name == "TL_RX":
            return self._tl_rx.pop(0) if self._tl_rx else 0
        if name == "TL_CLK_KHZ":
            return 10000 if self._powered() and self.stl.clock else 0
        if name == "TL_TX" or name == "UART_TX":
            return 0
        if name == "UART_RX":
            if not self._uart_rx:
                return 0
            b = self._uart_rx.pop(0)
            return 0x100 | b
        if name == "UART_STATUS":
            return len(self._uart_rx)  # the sim sends at once: nothing waits to go
        if name == "CAP_DATA":
            word = int(self._buf[self._cap_addr]) if self._buf is not None and self._cap_addr < self._buf.size else 0
            self._cap_addr = (self._cap_addr + 1) % rm.CAP_DEPTH
            return word
        return self.reg[name]

    def _write(self, addr, value):
        self._update()
        name = NAME.get(addr)
        if name is None:
            return
        if name == "EVENTS":
            self.reg[name] &= ~value
        elif name == "TRIP_STATUS":
            if value & F["TRIP_STATUS"]["TRIPPED"]:
                self.tripped = False
        elif name == "RAIL_EN":
            if not self.tripped:
                self.reg[name] = value & 7
        elif name in ("CLK_CTRL", "CLK_WINDOW"):
            self.reg[name] = value & 3 if name == "CLK_CTRL" else max(2, value)
            self._clk_restart()
        elif name == "DUT_PULSE":
            if not self.pulse_mask and value & 0xFFFFFF:
                self.reg[name] = value & 0x1FFFFFFF
                self.pulse_mask = 1 << (value >> 24 & 0x1F)
                self.pulse_end = self._now() + (value & 0xFFFFFF) / 1e6
        elif name in ("GPIO_OE", "GPIO_OUT"):
            self.reg[name] = value & 0x3F
        elif name == "ADC_CTRL":
            if self.reg[name] & 1 and not value & 1:  # RESET released
                self.adc.reset()
            self.reg[name] = value & 1
        elif name == "ADC_SPI":
            self.reg[name] = value & 0xFFFFFF
            self.adc_rdata = self.adc.spi(value)
        elif name == "CAP_LEN":
            self.reg[name] = min(rm.CAP_DEPTH, max(1, value))
        elif name == "CAP_CTRL":
            if value & 1 and not self._cap_busy and self.features & F["FEATURES"]["ADC"]:
                self._buf = None
                self._capture()
        elif name == "CAP_ADDR":
            self._cap_addr = value % rm.CAP_DEPTH
        elif name == "TL_CTRL" and self.features & F["FEATURES"]["TL"]:
            self.reg[name] = value & 1
            if not value & 1:
                self._tl_tx.clear()
                self._tl_rx.clear()
        elif name == "TL_FRAME":
            if not self.reg["TL_CTRL"] & 1:
                self.reg[name] = min(256, max(1, value))
        elif name == "TL_TX":
            if self.reg["TL_CTRL"] & 1 and len(self._tl_tx) < rm.TL_FIFO:
                self._tl_tx.append(value & 0xFFFFFFFF)
        elif name == "UART_DIV":
            self.reg[name] = max(16, value & 0xFFFF)
        elif name == "UART_EVENTS":
            self.reg[name] &= ~value
        elif name == "UART_TX" and self.features & F["FEATURES"]["UART"]:
            if self.uart_echo:
                self.uart_say(bytes([value & 0xFF]))
        elif name == "I2C_DIV":
            self.reg[name] = max(4, value & 0xFFFF)
        elif name == "I2C_WDATA":
            self.reg[name] = value
        elif name == "I2C_CMD" and self.features & F["FEATURES"]["I2C"]:
            self.reg[name] = value
            self._i2c(value)
        elif name in ("RAIL_SW", "TRIP_CTRL", "SCRATCH", "DUT_OE", "DUT_OUT", "BUTTON_US"):
            self.reg[name] = value
        self._update()

    def _packet(self, pkt):
        if pkt[0] == rm.CMD_WRITE:
            _, addr, value = struct.unpack("<BHI", pkt)
            self._write(addr, value)
            self._tx.append(rm.ACK)
        elif pkt[0] == rm.CMD_READ:
            _, addr = struct.unpack("<BH", pkt)
            self._tx += struct.pack("<I", self._read(addr))
        else:
            _, addr, n = struct.unpack("<BHI", pkt)
            for _ in range(n):
                self._tx += struct.pack("<I", self._read(addr))

    def press_button(self):
        """The DUT reset button: counted, and CORE_RST pulsed for BUTTON_US."""
        self._event("BUTTON")
        us = self.reg["BUTTON_US"]
        if us and not self.pulse_mask:
            self.pulse_mask = 1 << PIN["CORE_RST"]
            self.pulse_end = self._now() + us / 1e6


class SimIna237:
    """An INA237 as the host sees it: a register pointer and 16-bit registers, with VBUS and
    VSHUNT computed from the rail it monitors."""

    def __init__(self, board, rail, ohms):
        self.board, self.rail, self.ohms = board, rail, ohms
        self.reg = {0x00: 0, 0x01: 0xFB68, 0x0B: 0x0001, 0x0C: 0x7FFF, 0x3E: 0x5449}
        self.ptr = 0
        self.latched = False

    def _shunt_code(self):
        return round(self.board.rail_ma(self.rail) * 1e-3 * self.ohms / 5e-6)

    def over(self):
        code = self._shunt_code()
        if code > (self.reg[0x0C] if self.reg[0x0C] < 0x8000 else self.reg[0x0C] - 0x10000):
            if self.reg[0x0B] & 0x8000:
                self.latched = True
            return True
        return False

    def alert(self):
        return self.over() or self.latched

    def _get(self, r):
        if r == 0x04:
            return self._shunt_code() & 0xFFFF
        if r == 0x05:
            return round(self.board.rail_v(self.rail) / 3.125e-3)
        if r == 0x0B:
            flags = self.reg[r] | (1 << 6 if self.alert() else 0)
            self.latched = False  # reading DIAG_ALRT clears a latched alert
            return flags
        return self.reg.get(r, 0)

    def xfer(self, w, rlen):
        if w:
            self.ptr = w[0]
            if len(w) >= 3:
                self.reg[self.ptr] = (w[1] << 8 | w[2]) & (0x8000 if self.ptr == 0x0B else 0xFFFF)
        v = self._get(self.ptr) if rlen else 0
        return bytes([v >> 8, v & 0xFF, v >> 8, v & 0xFF])[:rlen]


class SimTla2528:
    """The TLA2528 in manual mode: opcode-framed register access; a read frame with no opcode
    returns a 12-bit conversion of CHANNEL_SEL, MSB first, left aligned."""

    def __init__(self):
        self.reg = {0x01: 0, 0x11: 0}
        self.volts = [0.25, 0.0, 0.0, 0.5, 1.8, 1.0, 0.0, 0.0]
        self.pending = None

    def xfer(self, w, rlen):
        if w:
            if w[0] == 0x08 and len(w) >= 3:
                self.reg[w[1]] = w[2] & ~0x02 if w[1] == 0x01 else w[2]  # CAL self-clears
            elif w[0] == 0x10 and len(w) >= 2:
                self.pending = w[1]
        if not rlen:
            return b""
        if self.pending is not None:
            v, self.pending = self.reg.get(self.pending, 0), None
            return bytes([v] * rlen)
        code = min(4095, max(0, round(self.volts[self.reg[0x11] & 7] / 3.3 * 4096)))
        return bytes([code >> 4, (code << 4) & 0xFF] * 2)[:rlen]


class SimDac53401:
    """The VBAT trim DAC: pointer + 16-bit registers; powered down (Hi-Z) at reset."""

    def __init__(self):
        self.reg = {0xD0: 0x000C, 0xD1: 0x01F0, 0x21: 0}
        self.ptr = 0

    def vout(self):
        """The output voltage, or None while powered down."""
        if self.reg[0xD1] >> 3 & 3:
            return None
        return (self.reg[0x21] >> 2 & 0x3FF) * 1.8 / 1024

    def xfer(self, w, rlen):
        if w:
            self.ptr = w[0]
            if len(w) >= 3 and self.ptr in self.reg and self.ptr != 0xD0:
                self.reg[self.ptr] = w[1] << 8 | w[2]
        v = self.reg.get(self.ptr, 0)
        return bytes([v >> 8, v & 0xFF] * 2)[:rlen]


class SimAdc3910:
    """The ADC3910 as the FPGA sees it: SPI registers, and samples for the capture buffer."""

    def __init__(self):
        self.reg = {}
        self.dclk = True
        self.tone_hz = 2e6
        self.amplitude = 300.0
        self.n = 0  # samples since power-up, so successive captures continue the waveform

    def reset(self):
        # CFG_ALERT comes up once the power-up calibration has run, which needs the sample clock
        # (the sim takes no time over it)
        self.reg = {0x038: 0x01} if self.dclk else {}

    def spi(self, cmd):
        """One 24-bit frame; returns the byte a read clocks out."""
        addr, data = cmd >> 8 & 0x7FFF, cmd & 0xFF
        if cmd >> 23 & 1:
            return self.reg.get(addr, 0)
        if addr == 0x000 and data & 0x80:
            self.reset()
        else:
            self.reg[addr] = data
        return 0

    def samples(self, n):
        """n capture words: A in bits 9:0, B in 25:16, 10-bit two's complement."""
        import numpy as np

        pat = self.reg.get(0x091, 0)
        k = np.arange(self.n, self.n + n)
        self.n += n
        ph = 2 * np.pi * self.tone_hz / 32e6 * k
        out = []
        for ch, lo, re in (("A", 0x094, np.cos), ("B", 0x092, np.sin)):
            if pat & (1 << 4 if ch == "A" else 1 << 5):
                code = (self.reg.get(lo, 0) | self.reg.get(lo + 1, 0) << 8) >> 4 & 0x3FF
                v = np.full(n, code, dtype=np.int64)
            else:
                x = self.amplitude * re(ph) + np.random.normal(0, 0.7, n)
                v = np.clip(np.round(x), -512, 511).astype(np.int64) & 0x3FF
            out.append(v)
        return (out[0] | out[1] << 16).astype(np.uint32)


class SimStlChip:
    """The DUT's end of serial TileLink: a memory that answers A-channel messages."""

    def __init__(self):
        self.mem = {}
        self.clock = True
        self.frames = 0

    def handle(self, frame):
        """One received frame (an int); returns the answer frame, or None."""
        d = tl_unpack(frame)
        self.frames += 1
        a = d["address"] & ~7
        if d["chan"] != TL_A:
            return None
        if d["opcode"] == TL_GET:
            return tl_pack(chan=TL_D, opcode=TL_ACK_DATA, size=d["size"], source=d["source"],
                           data=self.mem.get(a, 0), last=1)
        if d["opcode"] in (TL_PUT_FULL, TL_PUT_PARTIAL):
            w = self.mem.get(a, 0)
            for i in range(8):
                if d["union"] >> i & 1:
                    w = w & ~(0xFF << 8 * i) | d["data"] & (0xFF << 8 * i)
            self.mem[a] = w
            return tl_pack(chan=TL_D, opcode=TL_ACK, size=d["size"], source=d["source"], last=1)
        return None

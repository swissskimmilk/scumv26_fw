"""A simulated FPGA board, for developing the host tools without hardware.

SimFpga stands in for the D2XX device on the FT2232H's channel B: it takes the host packets
(fpga/regmap.py) and answers them from a model of the gateware's registers, behaving as the
RTL does: rail enables that a trip clears and holds off, w1c event flags, cumulative counters,
a DUT clock counter whose windows complete in real time, pads released while VIO is off, and
timed pulses.

    from scum import Board
    from sim import SimFpga
    b = Board(transport=SimFpga())

Set `alert = True` on the SimFpga to pull TELEM_ALERT, as an INA237 over its limit would.
"""
import random
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fpga"))
import regmap as rm  # noqa: E402

A = {name: addr for name, addr, *_ in rm.REGS}
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
        self.reg = {name: reset for name, _, _, reset, _, _ in rm.REGS}
        self.counts = {"TRIP_COUNT": 0, "BUTTON_COUNT": 0, "FRAME_COUNT": 0}
        self.tripped = False
        self.pulse_end = 0.0
        self.pulse_mask = 0
        self._clk_restart()

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

    def _event(self, name):
        self.reg["EVENTS"] |= F["EVENTS"][name]
        self.counts[name + "_COUNT"] += 1

    def _clk_restart(self):
        self.clk_t0 = self._now()

    def _update(self):
        """Advance what the hardware does on its own: the trip and the pulse timer."""
        # `alert` is the asserted state, so TRIP_CTRL.ACTIVE_HIGH does not matter here
        if self.reg["TRIP_CTRL"] & F["TRIP_CTRL"]["EN"] and self.alert and not self.tripped:
            self.tripped = True
            self.reg["RAIL_EN"] = 0
            self._event("TRIP")
        if self.pulse_mask and self._now() >= self.pulse_end:
            self.pulse_mask = 0

    def _powered(self):
        en = self.reg["RAIL_EN"]
        return all(en & F["RAIL_EN"][r] for r in ("VIO", "VBAT", "VDD"))

    def _pads(self):
        """DUT_IN: driven pins read back what is driven; the DUT's outputs are modelled crudely
        (SCAN_OUT follows SCAN_IN, CORE_GPIO toggles at 1 Hz) while it is powered."""
        if not self.reg["RAIL_EN"] & F["RAIL_EN"]["VIO"]:
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
        name = next((k for k, v in A.items() if v == addr), None)
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
                   (F["TRIP_STATUS"]["ALERT"] if self.alert else 0)
        if name in ("CLK_COUNT", "CLK_SEQ"):
            return self._clk(name)
        if name == "DUT_IN":
            return self._pads()
        if name == "DUT_PULSE":
            return self.reg[name] | (1 << 31 if self.pulse_mask else 0)
        return self.reg[name]

    def _write(self, addr, value):
        self._update()
        name = next((k for k, v in A.items() if v == addr), None)
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

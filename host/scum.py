"""Host side of the SCuMV26 bring-up board.

    python scum.py                 interactive prompt (type `help`)
    python scum.py tele            run one command and print the reply
    python scum.py cap 65536 -o x  capture 65536 words to x.npz
    python scum.py --sim tele      talk to the simulated board instead (sim.py)

As a library:

    from scum import Board
    b = Board()
    b.cmd("power up")
    raw, mode = b.capture(65536)
    b.tele()["vbat"]["ma"]

Lines the board sends starting with "!" are asynchronous events (over-current trip, reset
button). They can arrive in the middle of any reply; Board takes them out of the reply and
hands each to `on_event`.
"""
import argparse
import re
import sys
import zlib

import serial
import serial.tools.list_ports

VID, PID = 0x1209, 0x0001

RAILS = ("vio", "vbat", "vbat_rf", "vddd", "vdda", "rf_vdd")  # the six INA237 monitors
CAP_MAX_WORDS = 96 * 1024


class BoardError(RuntimeError):
    pass


class Board:
    def __init__(self, port=None, timeout=5.0, transport=None):
        """Open the board on `port` (found by USB ID if None), or on any object with the
        pyserial read/write/readline/in_waiting interface (`transport`, e.g. sim.SimSerial)."""
        if transport is None:
            port = port or self.find_port()
            # Opening the port asserts DTR, which is what the firmware treats as "connected".
            transport = serial.Serial(port, timeout=timeout)
        self.ser = transport
        self.ser.reset_input_buffer()
        self.events = []         # events seen while no on_event callback is set
        self.on_event = None     # f(text) for each "!" line, without the "! "
        self.on_line = None      # f(direction, text, quiet) per line: ">" sent, "<" received
        self.quiet = False       # passed to on_line: marks background polling traffic
        self.last_capture_ms = None

    @staticmethod
    def find_port():
        for p in serial.tools.list_ports.comports():
            if p.vid == VID and p.pid == PID:
                return p.device
        raise BoardError("board not found (USB %04X:%04X); pass --port" % (VID, PID))

    def close(self):
        self.ser.close()

    # ---- line protocol ------------------------------------------------------------------

    def _trace(self, direction, text):
        if self.on_line:
            self.on_line(direction, text, self.quiet)

    def _event(self, line):
        text = line[1:].strip()
        if self.on_event:
            self.on_event(text)
        else:
            self.events.append(text)

    def _readline(self):
        while True:
            line = self.ser.readline()
            if not line.endswith(b"\n"):
                raise BoardError("timeout waiting for the board")
            text = line.decode(errors="replace").rstrip("\r\n")
            if text.startswith("!"):
                self._event(text)
                continue
            self._trace("<", text)
            return text

    def _send(self, line):
        self._trace(">", line)
        self.ser.write((line + "\n").encode())

    def poll(self):
        """Collect events that arrived while no command was running."""
        while self.ser.in_waiting:
            line = self.ser.readline()
            text = line.decode(errors="replace").rstrip("\r\n")
            if text.startswith("!"):
                self._event(text)
            elif text:
                self._trace("<", text)  # stray output: show it rather than drop it

    def cmd(self, line):
        """Send one command; return its reply lines. Raises BoardError on ERR."""
        self._send(line.strip())
        out = []
        while True:
            reply = self._readline()
            if reply == "OK":
                return out
            if reply.startswith("ERR"):
                raise BoardError(reply[4:])
            out.append(reply)

    def capture(self, words):
        """Capture `words` 32-bit DCMI words. Returns (uint32 array, mode string)."""
        import numpy as np

        self._send("cap %d" % words)
        while True:
            reply = self._readline()
            if reply.startswith("ERR"):
                raise BoardError(reply[4:])
            if reply.startswith("BIN "):
                break
        _, nbytes, mode, ms, crc = reply.split()
        nbytes = int(nbytes)
        data = self.ser.read(nbytes)
        if len(data) != nbytes:
            raise BoardError("short binary block: %d of %d bytes" % (len(data), nbytes))
        if zlib.crc32(data) != int(crc, 16):
            raise BoardError("capture CRC mismatch: data corrupted in transfer")
        if self._readline() != "OK":
            raise BoardError("capture did not end with OK")
        self.last_capture_ms = int(ms)
        return np.frombuffer(data, dtype="<u4"), mode

    # ---- parsed replies -----------------------------------------------------------------

    def ident(self):
        return self.cmd("id")[0]

    def status(self, peek=False):
        """Event counters as a dict of strings; cleared on the board unless `peek`."""
        return dict(line.split(" ", 1) for line in self.cmd("status peek" if peek else "status"))

    def tele(self):
        """{rail: {"v", "ma", "mv_shunt"} or None if its monitor did not answer}."""
        res = {}
        for line in self.cmd("tele")[1:]:  # first line is the header
            f = line.split()
            res[f[0]] = None if "no response" in line else {
                "v": float(f[4]), "ma": float(f[6]), "mv_shunt": float(f[8])}
        return res

    def rails(self):
        """{"vio"|"vbat"|"1v": bool} regulator enables."""
        return {f[0]: f[1] == "on" for f in (l.split() for l in self.cmd("rail"))}

    def switches(self):
        """{"vddd"|"vdda"|"rf": "board"|"dut"}."""
        return {f[0]: f[1] for f in (l.split() for l in self.cmd("sw"))}

    _VBAT_RE = re.compile(r"dac (\d+) \(([\d.]+) V\) -> VBAT set point ([\d.]+) V")

    def vbat(self, volts=None):
        """Set (if given) and return the open-loop VBAT trim: {"dac", "vdac", "setpoint"}."""
        line = self.cmd("vbat" if volts is None else "vbat %.4f" % volts)[0]
        m = self._VBAT_RE.search(line)
        return {"dac": int(m[1]), "vdac": float(m[2]), "setpoint": float(m[3])}

    def ilim(self):
        """{rail: trip limit in mA, 0 = off}."""
        res = {}
        for line in self.cmd("ilim"):
            f = line.split()
            res[f[0]] = 0.0 if f[1] == "off" else float(f[1])
        return res

    def aux(self):
        """[(channel, name, volts or None)] for the TLA2528's eight inputs."""
        res = []
        for line in self.cmd("aux"):
            f = line.split()
            res.append((int(f[0][3:]), f[1], None if "failed" in line else float(f[2])))
        return res

    def clk(self, sel=None, psc=None, win=None):
        args = "".join(" %s %s" % (k, v) for k, v in (("sel", sel), ("psc", psc), ("win", win))
                       if v is not None)
        f = self.cmd("clk" + args)[0].split()
        return {"sel": f[1], "psc": int(f[3]), "win": int(f[5]), "resolution": float(f[8])}

    def freq(self):
        """DUT clock frequency in Hz, from the next counting window."""
        return float(self.cmd("freq")[0].split()[0])

    def pins(self):
        """{name: (is_output, level)} for every DUT control pin."""
        return {f[0]: (f[1] == "out", f[2] == "1") for f in (l.split() for l in self.cmd("pin"))}

    def uart_rx(self):
        """Bytes the DUT has sent since the last call."""
        line = self.cmd("uart rx")[0]
        return re.sub(r"\\x([0-9A-Fa-f]{2})", lambda m: chr(int(m[1], 16)), line).encode("latin-1")


def unpack(raw, mode):
    """Split DCMI words into signed 10-bit samples.

    Each word holds two consecutive samples of one channel: bits 9:0 first, bits 25:16
    second. The mode's last letter (ddr-a, sdr-b, ...) names the channel.
    """
    import numpy as np

    s = np.empty(raw.size * 2, dtype=np.int16)
    s[0::2] = raw & 0x3FF
    s[1::2] = (raw >> 16) & 0x3FF
    s = np.where(s >= 512, s - 1024, s)  # ADC default output is two's complement
    return {mode[-1].upper(): s}


def open_board(port=None, sim=False):
    if sim:
        from sim import SimSerial

        return Board(transport=SimSerial())
    return Board(port)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--sim", action="store_true", help="use the simulated board")
    ap.add_argument("-o", "--out", help="for cap: save samples to this .npz")
    ap.add_argument("command", nargs="*")
    a = ap.parse_args()
    b = open_board(a.port, a.sim)
    b.on_event = lambda text: print("!", text, file=sys.stderr)

    if a.command and a.command[0] == "cap":
        import numpy as np

        raw, mode = b.capture(int(a.command[1]))
        ch = unpack(raw, mode)
        for k, v in ch.items():
            print("%s: %d samples, min %d max %d mean %.2f" % (k, v.size, v.min(), v.max(), v.mean()))
        if a.out:
            np.savez(a.out, raw=raw, mode=mode, **ch)
            print("saved", a.out if a.out.endswith(".npz") else a.out + ".npz")
        return

    if a.command:
        try:
            print("\n".join(b.cmd(" ".join(a.command))))
        except BoardError as e:
            sys.exit("ERR " + str(e))
        return

    print("connected; `help` lists commands, Ctrl-C quits")
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
            words = line.split()
            if words[0] == "cap":  # binary reply: summarise instead of printing it
                raw, mode = b.capture(int(words[1]))
                for k, v in unpack(raw, mode).items():
                    print("%s: %d samples, min %d max %d mean %.2f" % (k, v.size, v.min(), v.max(), v.mean()))
                continue
            for r in b.cmd(line):
                print(r)
        except (BoardError, IndexError, ValueError) as e:
            print("ERR", e)


if __name__ == "__main__":
    main()

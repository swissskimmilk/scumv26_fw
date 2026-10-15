"""Host side of the SCuMV26 bring-up board.

    python scum.py                 interactive prompt (type `help`)
    python scum.py tele            run one command and print the reply
    python scum.py cap 65536 -o x  capture 65536 words to x.npz

As a library:

    from scum import Board
    b = Board()
    b.cmd("power up")
    raw, mode = b.capture(65536)
"""
import argparse
import sys
import zlib

import serial
import serial.tools.list_ports

VID, PID = 0x1209, 0x0001


class BoardError(RuntimeError):
    pass


class Board:
    def __init__(self, port=None, timeout=5.0):
        port = port or self.find_port()
        # Opening the port asserts DTR, which is what the firmware treats as "connected".
        self.ser = serial.Serial(port, timeout=timeout)
        self.ser.reset_input_buffer()

    @staticmethod
    def find_port():
        for p in serial.tools.list_ports.comports():
            if p.vid == VID and p.pid == PID:
                return p.device
        raise BoardError("board not found (USB %04X:%04X); pass --port" % (VID, PID))

    def _readline(self):
        line = self.ser.readline()
        if not line.endswith(b"\n"):
            raise BoardError("timeout waiting for the board")
        return line.decode(errors="replace").rstrip("\r\n")

    def cmd(self, line):
        """Send one command; return its reply lines. Raises BoardError on ERR."""
        self.ser.write((line.strip() + "\n").encode())
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

        self.ser.write(b"cap %d\n" % words)
        while True:
            reply = self._readline()
            if reply.startswith("ERR"):
                raise BoardError(reply[4:])
            if reply.startswith("BIN "):
                break
        _, nbytes, mode, _ms, crc = reply.split()
        nbytes = int(nbytes)
        data = self.ser.read(nbytes)
        if len(data) != nbytes:
            raise BoardError("short binary block: %d of %d bytes" % (len(data), nbytes))
        if zlib.crc32(data) != int(crc, 16):
            raise BoardError("capture CRC mismatch: data corrupted in transfer")
        if self._readline() != "OK":
            raise BoardError("capture did not end with OK")
        return np.frombuffer(data, dtype="<u4"), mode

    def status(self):
        """Read and clear the board's event counters, as a dict."""
        return dict(line.split(" ", 1) for line in self.cmd("status"))


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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("-o", "--out", help="for cap: save samples to this .npz")
    ap.add_argument("command", nargs="*")
    a = ap.parse_args()
    b = Board(a.port)

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

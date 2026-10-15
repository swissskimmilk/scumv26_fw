"""The one thread that talks to the board.

The serial port is a single half-duplex conversation, so every board call goes through a
queue to one worker thread that owns the Board. The GUI submits a function of the board
with `call(fn, on_done)`; the result comes back on the GUI thread through a queued signal.
Widgets must be read on the GUI thread, before `call`, never inside fn.
While the queue is idle the worker polls for "!" events.
"""
import itertools
import queue
import threading

import serial
from PySide6.QtCore import QObject, Signal

from scum import Board, BoardError

SIM = "Simulator"


class Link(QObject):
    connected = Signal(str)             # firmware id line
    disconnected = Signal(str)          # reason ("" = closed by us)
    event = Signal(str)                 # a "!" line from the board
    traffic = Signal(str, str, bool)    # direction (">" or "<"), line, from background polling
    error = Signal(str)                 # a call failed and had no on_error
    busy = Signal(bool)
    _done = Signal(int, object, object)

    def __init__(self):
        super().__init__()
        self._q = queue.Queue()
        self._callbacks = {}
        self._ids = itertools.count(1)
        self._pending = 0
        self._thread = None
        self.is_open = False
        self._done.connect(self._finish)

    # ---- GUI thread -------------------------------------------------------------------

    def open(self, port):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, args=(port,), daemon=True)
        self._thread.start()

    def close(self):
        if self._thread is not None:
            self.is_open = False  # refuse new calls; queued ones fail as "disconnected"
            self._q.put(None)

    def call(self, fn, on_done=None, on_error=None, quiet=False):
        """Run fn(board) on the worker. quiet=True keeps it out of the traffic log."""
        if not self.is_open:
            return None
        jid = next(self._ids)
        self._callbacks[jid] = (on_done, on_error)
        self._pending += 1
        if self._pending == 1:
            self.busy.emit(True)
        self._q.put((jid, fn, quiet))
        return jid

    def cmd(self, line, on_done=None, on_error=None):
        return self.call(lambda b: b.cmd(line), on_done, on_error)

    def _finish(self, jid, result, exc):
        on_done, on_error = self._callbacks.pop(jid, (None, None))
        self._pending -= 1
        if self._pending == 0:
            self.busy.emit(False)
        if exc is None:
            if on_done:
                on_done(result)
        elif on_error:
            on_error(exc)
        else:
            self.error.emit(str(exc))

    # ---- worker thread ----------------------------------------------------------------

    def _run(self, port):
        try:
            if port == SIM:
                from sim import SimSerial

                board = Board(transport=SimSerial())
            else:
                board = Board(port)
            board.on_event = self.event.emit
            board.on_line = self.traffic.emit
            ident = board.ident()
        except Exception as e:  # noqa: BLE001 - any failure to open is reported, not raised
            self._thread = None
            self.disconnected.emit(str(e))
            return
        self.is_open = True
        self.connected.emit(ident)
        reason = ""
        while True:
            try:
                job = self._q.get(timeout=0.05)
            except queue.Empty:
                try:
                    board.poll()
                except (serial.SerialException, OSError) as e:
                    reason = str(e)
                    break
                continue
            if job is None:
                break
            jid, fn, quiet = job
            board.quiet = quiet
            try:
                self._done.emit(jid, fn(board), None)
            except BoardError as e:
                self._done.emit(jid, None, e)
            except (serial.SerialException, OSError) as e:
                self._done.emit(jid, None, e)
                reason = str(e)
                break
            except Exception as e:  # noqa: BLE001 - a parse bug must not kill the link
                self._done.emit(jid, None, e)
            finally:
                board.quiet = False
        self.is_open = False
        # Fail whatever was still queued so its callbacks run.
        while True:
            try:
                job = self._q.get_nowait()
            except queue.Empty:
                break
            if job is not None:
                self._done.emit(job[0], None, BoardError("disconnected"))
        try:
            board.close()
        except Exception:  # noqa: BLE001
            pass
        self._thread = None
        self.disconnected.emit(reason)

"""Shares the app's board link with the command line.

The FT2232H's channel B can be open in one process only, so while the app is connected it
listens on localhost (scum.APP_PORT) and `scum.py` sends its commands here instead of opening
the board. Each command runs through the app's own Link like one typed in the Console, so it
shows in the console log and the panels re-read what it may have changed.

One JSON object per line, one request per connection:
    -> {"cmd": "power up"}
    <- {"ok": true, "lines": ["vio on", ...]}  or  {"ok": false, "error": "..."}
"""
import json
import socketserver
import threading

from PySide6.QtCore import QObject, Signal

from scum import APP_PORT

TIMEOUT_S = 30  # a capture is the longest command


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        share = self.server.share
        try:
            line = json.loads(self.rfile.readline())["cmd"]
            fut = share.link.submit(lambda b: b.cmd(line))
            if fut is None:
                raise RuntimeError("the app is not connected to a board")
            reply = {"ok": True, "lines": fut.result(TIMEOUT_S)}
            share.ran.emit()
        except Exception as e:  # noqa: BLE001 - every failure goes back to the caller as ERR text
            reply = {"ok": False, "error": str(e) or type(e).__name__}
        self.wfile.write((json.dumps(reply) + "\n").encode())


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False  # a second app must fail to bind, not share the port


class Share(QObject):
    ran = Signal()  # a command from the command line finished: it may have changed anything

    def __init__(self, link):
        super().__init__()
        self.link = link
        self._server = None

    def start(self):
        """Listen; returns an error string if another app already holds the port."""
        if self._server is not None:
            return ""
        try:
            self._server = _Server(("127.0.0.1", APP_PORT), _Handler)
        except OSError as e:
            return "command-line sharing is off: port %d is in use (%s)" % (APP_PORT, e)
        self._server.share = self
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return ""

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

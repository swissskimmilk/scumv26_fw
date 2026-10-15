"""UART tab (the DUT's serial port, bridged through the board) and the raw command console."""
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from .widgets import mono_font

MAX_BLOCKS = 20000


class HistoryLine(QLineEdit):
    """A line edit with Up/Down command history."""

    def __init__(self):
        super().__init__()
        self.history, self.pos = [], 0

    def take(self):
        text = self.text().strip()
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
        self.pos = len(self.history)
        self.clear()
        return text

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.history:
            self.pos = max(0, min(len(self.history), self.pos + (-1 if e.key() == Qt.Key.Key_Up else 1)))
            self.setText(self.history[self.pos] if self.pos < len(self.history) else "")
            return
        super().keyPressEvent(e)


def make_view():
    v = QPlainTextEdit()
    v.setReadOnly(True)
    v.setFont(mono_font())
    v.setMaximumBlockCount(MAX_BLOCKS)
    v.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    return v


class UartTab(QWidget):
    def __init__(self, link):
        super().__init__()
        self.link = link
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Baud"))
        self.baud = QComboBox()
        self.baud.setEditable(True)
        self.baud.addItems(["9600", "115200", "230400", "460800", "921600", "1000000", "2000000"])
        self.baud.setCurrentText("115200")
        row.addWidget(self.baud)
        setb = QPushButton("Set")
        setb.clicked.connect(lambda: self.link.cmd("uart baud " + self.baud.currentText()))
        row.addWidget(setb)
        row.addSpacing(16)
        self.poll = QCheckBox("Receive (poll every 0.2 s)")
        self.poll.toggled.connect(lambda on: self.timer.start(200) if on else self.timer.stop())
        row.addWidget(self.poll)
        self.stamp = QCheckBox("Timestamps")
        row.addWidget(self.stamp)
        row.addStretch(1)
        clear = QPushButton("Clear")
        row.addWidget(clear)
        lay.addLayout(row)
        self.view = make_view()
        clear.clicked.connect(self.view.clear)
        lay.addWidget(self.view, 1)
        send = QHBoxLayout()
        self.line = HistoryLine()
        self.line.setPlaceholderText("text to send to the DUT (CR LF appended; runs of spaces collapse)")
        self.line.returnPressed.connect(self.send)
        b = QPushButton("Send")
        b.clicked.connect(self.send)
        send.addWidget(self.line, 1)
        send.addWidget(b)
        lay.addLayout(send)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.receive)
        self._receiving = False
        self._at_line_start = True

    def send(self):
        text = self.line.take()
        if text:
            self.link.cmd("uart tx " + text)

    def receive(self):
        if self._receiving:
            return
        self._receiving = True

        def got(data):
            self._receiving = False
            if data:
                self._append(data.decode("utf-8", errors="replace"))

        def failed(e):
            self._receiving = False
            self.poll.setChecked(False)
            self.link.error.emit("uart rx: %s" % e)

        self.link.call(lambda b: b.uart_rx(), got, failed, quiet=True)

    def _append(self, text):
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if self.stamp.isChecked():
            stamp = time.strftime("[%H:%M:%S] ")
            out = []
            for ch in text:
                if self._at_line_start:
                    out.append(stamp)
                out.append(ch)
                self._at_line_start = ch == "\n"
            text = "".join(out)
        c = self.view.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        c.insertText(text)
        self.view.setTextCursor(c)
        self.view.ensureCursorVisible()


class ConsoleTab(QWidget):
    """Every line to and from the board, and a prompt for raw commands."""

    COLORS = {">": "#4e79a7", "<": None, "!": "#d9363e", "err": "#d9363e", "info": "#8a8f98"}
    ran = Signal()  # a typed command finished: it may have changed anything

    def __init__(self, link):
        super().__init__()
        self.link = link
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.show_quiet = QCheckBox("Include background polling")
        self.show_quiet.setToolTip("Polling calls (telemetry, status, live clock) are hidden by default")
        row.addWidget(self.show_quiet)
        row.addStretch(1)
        clear = QPushButton("Clear")
        row.addWidget(clear)
        lay.addLayout(row)
        self.view = make_view()
        clear.clicked.connect(self.view.clear)
        lay.addWidget(self.view, 1)
        self.line = HistoryLine()
        self.line.setPlaceholderText("command (help lists them); Up/Down for history")
        self.line.returnPressed.connect(self.run)
        lay.addWidget(self.line)

    def log(self, kind, text):
        fmt = QTextCharFormat()
        color = self.COLORS.get(kind)
        if color:
            fmt.setForeground(QColor(color))
        prefix = {">": "> ", "<": "  ", "!": "! ", "err": "ERR ", "info": "# "}.get(kind, "")
        c = self.view.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        if not self.view.document().isEmpty():
            c.insertText("\n")
        c.insertText(time.strftime("%H:%M:%S ") + prefix + text, fmt)
        self.view.setTextCursor(c)
        self.view.ensureCursorVisible()

    def run(self):
        line = self.line.take()
        if not line:
            return
        if line.split()[0] == "cap":
            self.log("info", "cap returns binary data: use the Capture tab")
            return
        self.link.cmd(line, lambda _: self.ran.emit(),
                      lambda e: None)  # the ERR line is already in the log

"""DUT I/O tab: every DUT control pin by name, plus reset/boot shortcuts. Every pad is
bidirectional in the FPGA, so each row's direction button turns the pin around."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .widgets import Led

GROUPS = (("Core", ("CORE_RST", "BOOT_SEL", "CORE_GPIO", "CLK_DEBUG_MUX")),
          ("Scan chain", ("SCAN_CLK", "SCAN_IN", "SCAN_EN", "SCAN_SEL", "SCAN_RST", "SCAN_OUT")),
          ("TileLink", ("TL_CLK", "TL_VAL_IN", "TL_DAT_IN", "RDY_OUT", "TL_VAL_OUT", "TL_DAT_OUT",
                        "RDY_IN")),
          ("Radio / NFC", ("BASEBAND_TX", "BASEBAND_RX", "NFC_DEMOD_MCU", "NFC_MDM_DBG")))


class DutIoTab(QWidget):
    def __init__(self, link):
        super().__init__()
        self.link = link
        lay = QVBoxLayout(self)

        quick = QGroupBox("DUT")
        ql = QHBoxLayout(quick)
        reset = QPushButton("Reset DUT")
        reset.setToolTip("Pulse CORE_RST away from its idle level (same as the board button)")
        self.reset_us = QSpinBox()
        self.reset_us.setRange(1, 1000000)
        self.reset_us.setValue(1000)
        self.reset_us.setSuffix(" us")
        reset.clicked.connect(lambda: self._then_refresh("pulse CORE_RST %d" % self.reset_us.value()))
        ql.addWidget(reset)
        ql.addWidget(self.reset_us)
        ql.addSpacing(20)
        self.boot = QCheckBox("BOOT_SEL high")
        self.boot.clicked.connect(lambda on: self._then_refresh("pin BOOT_SEL %d" % on))
        ql.addWidget(self.boot)
        ql.addStretch(1)
        lay.addWidget(quick)

        row = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        self.auto = QCheckBox("Auto (0.5 s)")
        self.auto.toggled.connect(lambda on: self.timer.start(500) if on else self.timer.stop())
        row.addWidget(refresh)
        row.addWidget(self.auto)
        row.addStretch(1)
        row.addWidget(QLabel("Pulse width"))
        self.pulse_us = QSpinBox()
        self.pulse_us.setRange(1, 1000000)
        self.pulse_us.setValue(10)
        self.pulse_us.setSuffix(" us")
        row.addWidget(self.pulse_us)
        lay.addLayout(row)

        names = [n for _, ns in GROUPS for n in ns]
        self.table = QTableWidget(len(names), 5)
        self.table.setHorizontalHeaderLabels(["Group", "Pin", "Dir", "Level", ""])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.rows = {}
        r = 0
        for group, ns in GROUPS:
            for n in ns:
                self.table.setItem(r, 0, QTableWidgetItem(group))
                self.table.setItem(r, 1, QTableWidgetItem(n))
                d = QPushButton("?")
                d.setFixedWidth(48)
                d.setToolTip("FPGA drives the pin (out) or reads it (in); click to turn it around")
                d.clicked.connect(lambda _=False, n=n: self._then_refresh(
                    "pin %s %s" % (n, "in" if self.dirs.get(n) else "out")))
                self.table.setCellWidget(r, 2, d)
                cell = QWidget()
                cl = QHBoxLayout(cell)
                cl.setContentsMargins(6, 0, 6, 0)
                led = Led()
                lvl = QLabel("?")
                cl.addWidget(led)
                cl.addWidget(lvl)
                cl.addStretch(1)
                self.table.setCellWidget(r, 3, cell)
                self.rows[n] = (r, led, lvl, d, self._actions(n, r))
                r += 1
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.refresh(quiet=True))
        self.dirs = {}  # pin -> is_output, as last read
        self._refreshing = False

    def _then_refresh(self, line):
        self.link.cmd(line, lambda _: self.refresh())

    def _actions(self, n, row):
        """Drive 0 / 1 / pulse buttons for one pin; enabled while it is an output."""
        cell = QWidget()
        cl = QHBoxLayout(cell)
        cl.setContentsMargins(4, 0, 4, 0)
        for label, line in (("0", "pin %s 0"), ("1", "pin %s 1")):
            b = QPushButton(label)
            b.setFixedWidth(32)
            b.clicked.connect(lambda _=False, l=line % n: self._then_refresh(l))
            cl.addWidget(b)
        p = QPushButton("Pulse")
        p.clicked.connect(lambda _=False, n=n: self._then_refresh(
            "pulse %s %d" % (n, self.pulse_us.value())))
        cl.addWidget(p)
        cl.addStretch(1)
        cell.setEnabled(False)
        self.table.setCellWidget(row, 4, cell)
        return cell

    def refresh(self, quiet=False):
        if self._refreshing:
            return
        self._refreshing = True

        def show(pins):
            self._refreshing = False
            for n, (is_out, level) in pins.items():
                if n not in self.rows:
                    continue
                r, led, lvl, d, actions = self.rows[n]
                self.dirs[n] = is_out
                d.setText("out" if is_out else "in")
                actions.setEnabled(is_out)
                led.on(level)
                lvl.setText("1" if level else "0")
            if "BOOT_SEL" in pins:
                self.boot.setChecked(pins["BOOT_SEL"][1])

        def failed(e):
            self._refreshing = False
            self.link.error.emit(str(e))

        self.link.call(lambda b: b.pins(), show, failed, quiet=quiet)

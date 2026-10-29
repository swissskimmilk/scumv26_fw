"""Telemetry tab: the six INA237 rail monitors (live table, trip limits, strip chart) and
the TLA2528 auxiliary channels."""
import time
from collections import deque

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from scum import RAILS

from .theme import style_plot
from .widgets import RED, mono_font

COLORS = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#b07aa1"]
HISTORY = 1200  # samples per rail (10 minutes at the 0.5 s poll)
# Which regulator enable feeds each monitor, for the "source" column.
FEED = {"vio": "vio", "vbat": "vbat", "vbat_rf": "vbat", "vddd": "1v", "vdda": "1v", "rf_vdd": "1v"}
SW = {"vddd": "vddd", "vdda": "vdda", "rf_vdd": "rf"}


class TelemetryTab(QWidget):
    COLS = ("Rail", "Bus V", "Current mA", "Shunt mV", "Trip limit mA", "State")

    def __init__(self, link):
        super().__init__()
        self.link = link
        self.t0 = time.monotonic()
        self.hist = {r: (deque(maxlen=HISTORY), deque(maxlen=HISTORY), deque(maxlen=HISTORY))
                     for r in RAILS}
        self.tripped = set()

        split = QSplitter(Qt.Orientation.Vertical)
        lay = QVBoxLayout(self)
        lay.addWidget(split)

        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(len(RAILS), len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setFont(mono_font())
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.limits = {}
        for r, rail in enumerate(RAILS):
            name = QTableWidgetItem(rail)
            name.setForeground(pg.mkColor(COLORS[r]))
            self.table.setItem(r, 0, name)
            for c in range(1, 4):
                item = QTableWidgetItem("--")
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)
            spin = QDoubleSpinBox()
            spin.setRange(0, 80)
            spin.setDecimals(2)
            spin.setSpecialValueText("off")
            spin.setToolTip("Hardware over-current trip on this rail's INA237 (0 = off).\n"
                            "Any trip drops every DUT rail within ~2 ms.")
            spin.editingFinished.connect(lambda r=rail, s=spin: self._set_limit(r, s.value()))
            self.table.setCellWidget(r, 4, spin)
            self.limits[rail] = spin
            self.table.setItem(r, 5, QTableWidgetItem(""))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(True)
        tl.addWidget(self.table, 3)

        aux = QGroupBox("DUT analog (TLA2528)")
        al = QVBoxLayout(aux)
        self.aux = QLabel("--")
        self.aux.setFont(mono_font())
        self.aux.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        al.addWidget(self.aux)
        read = QPushButton("Read")
        read.clicked.connect(self.read_aux)
        al.addWidget(read)
        al.addStretch(1)
        tl.addWidget(aux, 1)
        split.addWidget(top)

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Plot"))
        self.quantity = QComboBox()
        self.quantity.addItems(["Current (mA)", "Bus voltage (V)"])
        self.quantity.currentIndexChanged.connect(self._redraw)
        row.addWidget(self.quantity)
        clear = QPushButton("Clear history")
        clear.clicked.connect(self.clear_history)
        row.addWidget(clear)
        row.addStretch(1)
        bl.addLayout(row)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "time", "s")
        self.plot.addLegend(offset=(10, 10))
        self.curves = {r: self.plot.plot([], [], name=r, pen=pg.mkPen(COLORS[i], width=1.5))
                       for i, r in enumerate(RAILS)}
        style_plot(self.plot)
        bl.addWidget(self.plot)
        split.addWidget(bottom)
        split.setSizes([260, 400])

    def _set_limit(self, rail, ma):
        self.link.cmd("ilim %s %.2f" % (rail, ma))

    def set_limits(self, lim):
        for rail, ma in lim.items():
            if rail in self.limits:
                self.limits[rail].setValue(ma)

    def mark_tripped(self, rails):
        self.tripped |= set(rails)

    def update(self, tele, rails, switches):
        now = time.monotonic() - self.t0
        for r, rail in enumerate(RAILS):
            m = tele.get(rail)
            src = FEED[rail]
            if rail in SW and switches.get(SW[rail]) == "dut":
                note = "DUT LDO"
            else:
                note = "on" if rails.get(src) else "off"
            if rails.get(src):
                self.tripped.discard(rail)  # powered again since the trip
            if rail in self.tripped:
                note = "TRIPPED  " + note
            item = self.table.item(r, 5)
            item.setText(note)
            item.setForeground(pg.mkColor(RED) if rail in self.tripped else self.palette().text().color())
            if m is None:
                for c in range(1, 4):
                    self.table.item(r, c).setText("no reply")
                continue
            self.table.item(r, 1).setText("%.4f" % m["v"])
            self.table.item(r, 2).setText("%.3f" % m["ma"])
            self.table.item(r, 3).setText("%.3f" % m["mv_shunt"])
            t, v, i = self.hist[rail]
            t.append(now)
            v.append(m["v"])
            i.append(m["ma"])
        self._redraw()

    def _redraw(self):
        which = 2 if self.quantity.currentIndex() == 0 else 1
        self.plot.setLabel("left", "current" if which == 2 else "bus voltage",
                           "mA" if which == 2 else "V")
        for rail, curve in self.curves.items():
            h = self.hist[rail]
            curve.setData(list(h[0]), list(h[which]))

    def clear_history(self):
        for h in self.hist.values():
            for d in h:
                d.clear()
        self.tripped.clear()
        self._redraw()

    def read_aux(self):
        def show(rows):
            self.aux.setText("\n".join(
                "AIN%d %-11s %s" % (ch, name, "failed" if v is None else "%.4f V" % v)
                for ch, name, v in rows))

        self.link.call(lambda b: b.aux(), show)

"""Left-hand column: DUT power, the DUT clock counter, and the board's event counters."""
import math

from PySide6.QtCore import QSignalBlocker, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout)

from .widgets import AMBER, GREY, RED, Led, big_label

VBAT_MIN, VBAT_MAX = 1.164, 1.549  # buffered-DAC trim range (README, open item 7)


class PowerPanel(QGroupBox):
    changed = Signal()  # something was switched; the window refreshes the state

    def __init__(self, link):
        super().__init__("DUT power")
        self.link = link
        lay = QVBoxLayout(self)

        row = QHBoxLayout()
        self.up = QPushButton("Power up")
        self.down = QPushButton("Power down")
        self.up.setToolTip("Sequenced: VIO, check it reached 1.6 V, VBAT, then the 1 V rails")
        self.down.setToolTip("Reverse sequence: 1 V rails, VBAT, VIO")
        self.up.clicked.connect(lambda: self._send("power up"))
        self.down.clicked.connect(lambda: self._send("power down"))
        row.addWidget(self.up)
        row.addWidget(self.down)
        lay.addLayout(row)

        grid = QGridLayout()
        self.rail_led, self.rail_btn = {}, {}
        for r, (key, label) in enumerate((("vio", "VIO"), ("vbat", "VBAT"), ("1v", "1 V rails"))):
            led, btn = Led(), QPushButton("Turn on")
            btn.clicked.connect(lambda _=False, k=key: self._toggle_rail(k))
            grid.addWidget(led, r, 0)
            grid.addWidget(QLabel(label), r, 1)
            grid.addWidget(btn, r, 2)
            self.rail_led[key], self.rail_btn[key] = led, btn
        self.rail_state = {}

        self.sw = {}
        for i, (key, label) in enumerate((("vddd", "VDDD from"), ("vdda", "VDDA from"),
                                          ("rf", "RF_VDD from"))):
            combo = QComboBox()
            combo.addItems(["board", "dut"])
            combo.setToolTip("board = the board's 1 V regulator; dut = the DUT's own LDO")
            combo.currentTextChanged.connect(lambda v, k=key: self._send("sw %s %s" % (k, v)))
            grid.addWidget(QLabel(label), 3 + i, 1)
            grid.addWidget(combo, 3 + i, 2)
            self.sw[key] = combo
        lay.addLayout(grid)

        vb = QHBoxLayout()
        self.vbat = QDoubleSpinBox()
        self.vbat.setRange(VBAT_MIN, VBAT_MAX)
        self.vbat.setDecimals(3)
        self.vbat.setSingleStep(0.005)
        self.vbat.setSuffix(" V")
        self.vbat.setValue(1.384)
        set_btn, trim_btn = QPushButton("Set"), QPushButton("Trim")
        set_btn.setToolTip("Open loop: compute the DAC code from the resistor network")
        trim_btn.setToolTip("Closed loop on the VBAT monitor reading, to 1 mV (VBAT must be on)")
        set_btn.clicked.connect(lambda: self._send("vbat %.4f" % self.vbat.value()))
        trim_btn.clicked.connect(lambda: self._send("trim %.4f" % self.vbat.value()))
        vb.addWidget(QLabel("VBAT"))
        vb.addWidget(self.vbat, 1)
        vb.addWidget(set_btn)
        vb.addWidget(trim_btn)
        lay.addLayout(vb)
        self.vbat_info = QLabel("")
        self.vbat_info.setStyleSheet("color:%s" % GREY)
        lay.addWidget(self.vbat_info)

    def _send(self, line):
        self.link.cmd(line, on_done=lambda _: self.changed.emit(),
                      on_error=lambda e: (self.link.error.emit(str(e)), self.changed.emit()))

    def _toggle_rail(self, key):
        self._send("rail %s %s" % (key, "off" if self.rail_state.get(key) else "on"))

    def set_rails(self, rails):
        self.rail_state = rails
        for k, on in rails.items():
            self.rail_led[k].on(on)
            self.rail_btn[k].setText("Turn off" if on else "Turn on")

    def set_switches(self, sw):
        for k, v in sw.items():
            with QSignalBlocker(self.sw[k]):
                self.sw[k].setCurrentText(v)

    def set_vbat(self, v):
        self.vbat_info.setText("DAC %d (%.4f V) -> set point %.4f V" % (v["dac"], v["vdac"], v["setpoint"]))


class ClockPanel(QGroupBox):
    def __init__(self, link):
        super().__init__("DUT clock")
        self.link = link
        lay = QGridLayout(self)
        self.sel = QComboBox()
        self.sel.addItems(["dig", "ana", "off"])
        self.sel.setToolTip("LMK1D input: dig = DUT dig_out, ana = DUT ana_out, off = outputs disabled")
        self.psc = QComboBox()
        self.psc.addItems(["1", "2", "4", "8"])
        self.psc.setCurrentText("4")
        self.psc.setToolTip("ETR prescaler: input / psc must stay under 40 MHz")
        self.win = QSpinBox()
        self.win.setRange(1, 6000)
        self.win.setValue(100)
        self.win.setSuffix(" ms")
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.apply)
        lay.addWidget(QLabel("Input"), 0, 0)
        lay.addWidget(self.sel, 0, 1)
        lay.addWidget(QLabel("Prescale"), 1, 0)
        lay.addWidget(self.psc, 1, 1)
        lay.addWidget(QLabel("Window"), 2, 0)
        lay.addWidget(self.win, 2, 1)
        lay.addWidget(apply_btn, 2, 2)
        self.freq = big_label("--")
        lay.addWidget(self.freq, 3, 0, 1, 3)
        self.info = QLabel("")
        self.info.setStyleSheet("color:%s" % GREY)
        lay.addWidget(self.info, 4, 0, 1, 2)
        self.live = QCheckBox("Live")
        self.live.toggled.connect(lambda on: self.timer.start(300) if on else self.timer.stop())
        lay.addWidget(self.live, 4, 2)
        measure = QPushButton("Measure")
        measure.clicked.connect(self.measure)
        lay.addWidget(measure, 3, 2)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.measure)
        self._measuring = False
        self.psc_now, self.win_now = 4, 100  # as last reported by the board

    def apply(self):
        sel, psc, win = self.sel.currentText(), self.psc.currentText(), self.win.value()
        self.link.call(lambda b: b.clk(sel=sel, psc=psc, win=win), self.set_clk)

    def set_clk(self, c):
        for w, v in ((self.sel, c["sel"]), (self.psc, str(c["psc"]))):
            with QSignalBlocker(w):
                w.setCurrentText(v)
        with QSignalBlocker(self.win):
            self.win.setValue(c["win"])
        self.psc_now, self.win_now = c["psc"], c["win"]
        self.info.setText("resolution %g Hz" % c["resolution"])

    def measure(self):
        if self._measuring:
            return
        self._measuring = True

        def done(hz):
            self._measuring = False
            # Show digits down to the counter's resolution (psc / window), in MHz.
            res_mhz = self.psc_now / self.win_now / 1e3
            self.freq.setText("%.*f MHz" % (max(0, math.ceil(-math.log10(res_mhz))), hz / 1e6))

        def failed(e):
            self._measuring = False
            self.freq.setText("--")
            self.info.setText(str(e))

        self.link.call(lambda b: b.freq(), done, failed, quiet=self.live.isChecked())


class StatusPanel(QGroupBox):
    """The board's event counters. Polled with `status peek`; Clear reads and zeroes them."""

    FIELDS = (("ocp_trips", "Over-current trips"), ("capture_errors", "Capture errors"),
              ("uart_dropped", "UART bytes dropped"), ("host_dropped", "Host bytes dropped"),
              ("button_resets", "Button resets"), ("mcu_reset", "MCU reset cause"),
              ("uptime_s", "Uptime"))

    def __init__(self, link):
        super().__init__("Board status")
        self.link = link
        lay = QGridLayout(self)
        self.vals = {}
        for r, (key, label) in enumerate(self.FIELDS):
            lay.addWidget(QLabel(label), r, 0)
            v = QLabel("--")
            lay.addWidget(v, r, 1)
            self.vals[key] = v
        clear = QPushButton("Clear counters")
        clear.setToolTip("Read the counters and reset them to zero on the board")
        clear.clicked.connect(lambda: self.link.call(lambda b: b.status(), self.update))
        lay.addWidget(clear, len(self.FIELDS), 0, 1, 2)

    def update(self, st):
        for key, lbl in self.vals.items():
            text = st.get(key, "--")
            if key == "uptime_s" and text.isdigit():
                s = int(text)
                text = "%d:%02d:%02d" % (s // 3600, s // 60 % 60, s % 60)
            lbl.setText(text)
            bad = key not in ("mcu_reset", "uptime_s") and text.split()[0] not in ("0", "--")
            lbl.setStyleSheet("color:%s; font-weight:600" % (RED if key == "ocp_trips" else AMBER)
                              if bad else "")

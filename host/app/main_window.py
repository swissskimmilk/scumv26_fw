"""The main window: connection bar, alert banner, control column and the tabs."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QMainWindow,
                               QPushButton, QScrollArea, QSplitter, QTabWidget, QToolBar,
                               QVBoxLayout, QWidget)

from scum import FEATURE_BLOCK, list_devices

from .capture import CaptureTab
from .control import ClockPanel, PowerPanel, StatusPanel
from .dut_io import DutIoTab
from .link import SIM, Link
from .telemetry import TelemetryTab
from .terminal import ConsoleTab, UartTab
from .widgets import AMBER, GREEN, GREY, RED, Led

POLL_MS = 500
STATUS_EVERY = 4  # status peek every 4th poll (2 s)


class MainWindow(QMainWindow):
    def __init__(self, port=None, autoconnect=False):
        super().__init__()
        self.setWindowTitle("SCuMV26 bring-up")
        self.resize(1400, 900)
        self.link = Link()
        self._polling = False
        self._tick = 0

        # ---- connection toolbar
        bar = QToolBar("Connection")
        bar.setMovable(False)
        self.addToolBar(bar)
        bar.addWidget(QLabel(" Port "))
        self.port = QComboBox()
        self.port.setMinimumWidth(260)
        bar.addWidget(self.port)
        rescan = QPushButton("Rescan")
        rescan.clicked.connect(self.list_ports)
        bar.addWidget(rescan)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_connection)
        bar.addWidget(self.connect_btn)
        bar.addSeparator()
        self.link_led = Led(14)
        bar.addWidget(self.link_led)
        self.ident = QLabel(" not connected")
        bar.addWidget(self.ident)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding,
                             spacer.sizePolicy().verticalPolicy().Preferred)
        bar.addWidget(spacer)
        self.live = QCheckBox("Live update ")
        self.live.setToolTip("Poll the rails (and the INA237s, once the gateware has I2C) every 0.5 s")
        self.live.setChecked(True)
        bar.addWidget(self.live)
        self.busy_led = Led(10)
        self.busy_led.setToolTip("Board busy")
        bar.addWidget(self.busy_led)

        # ---- alert banner
        self.banner = QWidget()
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(10, 6, 10, 6)
        self.banner_text = QLabel()
        self.banner_text.setStyleSheet("color:white; font-weight:600")
        dismiss = QPushButton("Dismiss")
        dismiss.clicked.connect(self.banner.hide)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(dismiss)
        self.banner.setStyleSheet("background:%s" % RED)
        self.banner.hide()

        # ---- body
        self.power = PowerPanel(self.link)
        self.clock = ClockPanel(self.link)
        self.status_panel = StatusPanel(self.link)
        self.power.changed.connect(self.refresh_power)
        col = QWidget()
        cl = QVBoxLayout(col)
        for w in (self.power, self.clock, self.status_panel):
            cl.addWidget(w)
        cl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(col)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(340)

        self.tele = TelemetryTab(self.link)
        self.capture = CaptureTab(self.link)
        self.dut = DutIoTab(self.link)
        self.uart = UartTab(self.link)
        self.console = ConsoleTab(self.link)
        self.tabs = QTabWidget()
        for w, name in ((self.tele, "Telemetry"), (self.capture, "ADC capture"),
                        (self.dut, "DUT I/O"), (self.uart, "DUT UART"), (self.console, "Console")):
            self.tabs.addTab(w, name)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(scroll)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.banner)
        lay.addWidget(split, 1)
        self.setCentralWidget(central)
        self.body = (scroll, self.tabs)
        self.console.ran.connect(self.refresh_settings)

        # ---- shortcuts
        off = QAction("Power down", self)
        off.setShortcut(QKeySequence("Ctrl+Shift+D"))
        off.triggered.connect(lambda: self.link.cmd("power down", lambda _: self.refresh_power()))
        self.addAction(off)

        # Tabs and controls that need an optional gateware block (FEATURES register).
        self.needs = {"I2C": [self.tele, self.power.vbat_row], "ADC": [self.capture], "UART": [self.uart]}

        # ---- link signals
        self.link.connected.connect(self.on_connected)
        self.link.disconnected.connect(self.on_disconnected)
        self.link.event.connect(self.on_event)
        self.link.error.connect(self.on_error)
        self.link.traffic.connect(self.on_traffic)
        self.link.busy.connect(lambda b: self.busy_led.set(AMBER if b else GREY))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)

        self.set_enabled(False)
        self.list_ports(prefer=port)
        if autoconnect:
            self.toggle_connection()

    # ---- connection -------------------------------------------------------------------

    def list_ports(self, prefer=None):
        self.port.clear()
        boards, err = list_devices()
        for dev, label in boards + [(SIM, "Simulator (no hardware)")]:
            self.port.addItem(label, dev)
        if err:
            self.statusBar().showMessage(err + ": only the simulator is available", 15000)
        if prefer:
            i = self.port.findData(prefer)
            if i >= 0:
                self.port.setCurrentIndex(i)
        self.connect_btn.setFocus()

    def toggle_connection(self):
        if self.link.is_open:
            self.link.close()
            return
        self.connect_btn.setEnabled(False)
        self.ident.setText(" connecting...")
        self.link.open(self.port.currentData())

    def on_connected(self, ident):
        self.link_led.set(GREEN)
        self.ident.setText(" " + ident)
        self.connect_btn.setText("Disconnect")
        self.connect_btn.setEnabled(True)
        self.port.setEnabled(False)
        self.set_enabled(True)
        self.console.log("info", "connected: " + ident)
        self.apply_features()
        self.refresh_settings()
        self.link.call(lambda b: b.status(peek=True), self.status_panel.update)
        self.dut.refresh()
        self.timer.start(POLL_MS)

    def on_disconnected(self, reason):
        self.timer.stop()
        self._polling = False
        self.link_led.set(RED if reason else GREY)
        self.ident.setText(" " + (reason or "not connected"))
        self.connect_btn.setText("Connect")
        self.connect_btn.setEnabled(True)
        self.port.setEnabled(True)
        self.set_enabled(False)
        for box in (self.clock.live, self.capture.repeat, self.uart.poll, self.dut.auto):
            box.setChecked(False)
        self.console.log("err" if reason else "info", "disconnected" + (": " + reason if reason else ""))

    def set_enabled(self, on):
        for w in self.body:
            w.setEnabled(on)

    def has(self, feature):
        return feature in self.link.features

    def apply_features(self):
        """Grey out what this gateware cannot do yet, saying which block it needs."""
        for feature, widgets in self.needs.items():
            on = self.has(feature)
            tip = "" if on else "Not in the gateware yet: needs %s" % FEATURE_BLOCK[feature]
            for w in widgets:
                i = self.tabs.indexOf(w)
                if i >= 0:
                    self.tabs.setTabEnabled(i, on)
                    self.tabs.setTabToolTip(i, tip)
                else:
                    w.setEnabled(on)
                    w.setToolTip(tip)
        if not self.tabs.isTabEnabled(self.tabs.currentIndex()):
            self.tabs.setCurrentWidget(self.dut)

    # ---- polling ------------------------------------------------------------------------

    def refresh_power(self):
        i2c = self.has("I2C")

        def show(res):
            rails, sw, vbat = res
            self.power.set_rails(rails)
            self.power.set_switches(sw)
            if vbat:
                self.power.set_vbat(vbat)

        self.link.call(lambda b: (b.rails(), b.switches(), b.vbat() if i2c else None), show, quiet=True)

    def refresh_settings(self):
        """Re-read everything the board holds as settings, after a change from anywhere."""
        self.refresh_power()
        if self.has("I2C"):
            self.link.call(lambda b: b.ilim(), self.tele.set_limits, quiet=True)
        self.link.call(lambda b: b.clk(), self.clock.set_clk, quiet=True)

    def poll(self):
        if self._polling:
            return  # the board is busy (a capture, a trim); skip rather than queue up
        self._tick += 1
        want_status = self._tick % STATUS_EVERY == 0
        if not self.live.isChecked() and not want_status:
            return
        live = self.live.isChecked()
        tele = self.has("I2C")
        self._polling = True

        def work(b):
            res = {}
            if live:
                res["tele"] = b.tele() if tele else None
                res["rails"], res["sw"] = b.rails(), b.switches()
            if want_status:
                res["status"] = b.status(peek=True)
            return res

        def done(res):
            self._polling = False
            if "rails" in res:
                if res["tele"] is not None:
                    self.tele.update(res["tele"], res["rails"], res["sw"])
                self.power.set_rails(res["rails"])
                self.power.set_switches(res["sw"])
            if "status" in res:
                self.status_panel.update(res["status"])

        def failed(e):
            self._polling = False
            self.on_error("poll: %s" % e)

        self.link.call(work, done, failed, quiet=True)

    # ---- board messages -----------------------------------------------------------------

    def on_event(self, text):
        self.console.log("!", text)
        if text.startswith("ALERT"):
            rails = text.split(":", 2)[-1].split() if text.count(":") >= 2 else []
            self.tele.mark_tripped(rails)
            self.banner_text.setText("Over-current trip: all DUT rails were switched off"
                                     + (" (%s)" % ", ".join(rails) if rails else "")
                                     + ". Check the load, then power up again.")
            self.banner.show()
            self.refresh_power()
            self.link.call(lambda b: b.status(peek=True), self.status_panel.update, quiet=True)
        self.statusBar().showMessage(text, 10000)

    def on_error(self, msg):
        self.console.log("err", msg)
        self.statusBar().showMessage("Error: " + msg, 10000)

    def on_traffic(self, direction, text, quiet):
        if not quiet or self.console.show_quiet.isChecked():
            self.console.log(direction, text)

    def closeEvent(self, e):
        self.link.close()
        super().closeEvent(e)

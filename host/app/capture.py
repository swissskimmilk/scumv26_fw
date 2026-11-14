"""ADC capture tab: ADC3910 setup, captures of both channels at once, time and spectrum plots."""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from scum import ADC_FS, rm, unpack

from .theme import style_plot
from .widgets import mono_font

FULL_SCALE = 512  # 10-bit two's complement
COLORS = {"A": "#4e79a7", "B": "#f28e2b"}


class CaptureTab(QWidget):
    def __init__(self, link):
        super().__init__()
        self.link = link
        self.last = None  # (raw, {"A": samples, "B": samples})
        self.capture_ms = None
        lay = QHBoxLayout(self)

        side_w = QWidget()
        side_w.setMaximumWidth(380)
        side = QVBoxLayout(side_w)
        side.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(side_w)

        adc = QGroupBox("ADC3910")
        af = QFormLayout(adc)
        row = QHBoxLayout()
        init, check = QPushButton("Init"), QPushButton("Status")
        init.setToolTip("Hardware reset, wait out the calibration, check CFG_ALERT and SPI read-back")
        init.clicked.connect(lambda: self.link.cmd("adc init"))
        check.setToolTip("DCLK rate (0 = the ADC is not clocked) and the capture state")
        check.clicked.connect(lambda: self.link.cmd("adc"))
        row.addWidget(init)
        row.addWidget(check)
        af.addRow(row)
        af.addRow("Interface", QLabel("10-bit DDR: A on DCLK rising, B on falling"))
        pat = QHBoxLayout()
        self.pat_a, self.pat_b = QLineEdit("155"), QLineEdit("2AA")
        for w in (self.pat_a, self.pat_b):
            w.setMaximumWidth(70)
            w.setToolTip("10-bit output code, hex")
        pat.addWidget(QLabel("A"))
        pat.addWidget(self.pat_a)
        pat.addWidget(QLabel("B"))
        pat.addWidget(self.pat_b)
        af.addRow("Test pattern", pat)
        prow = QHBoxLayout()
        pon, poff = QPushButton("Pattern on"), QPushButton("Pattern off")
        pon.clicked.connect(lambda: self.link.cmd("adc pat %s %s" % (self.pat_a.text(), self.pat_b.text())))
        poff.clicked.connect(lambda: self.link.cmd("adc pat off"))
        prow.addWidget(pon)
        prow.addWidget(poff)
        af.addRow(prow)
        reg = QHBoxLayout()
        self.reg_addr, self.reg_val = QLineEdit("39"), QLineEdit("00")
        for w in (self.reg_addr, self.reg_val):
            w.setMaximumWidth(70)
            w.setToolTip("hex")
        rd, wr = QPushButton("Read"), QPushButton("Write")
        rd.clicked.connect(self._reg_read)
        wr.clicked.connect(lambda: self.link.cmd("adc wr %s %s" % (self.reg_addr.text(), self.reg_val.text())))
        reg.addWidget(self.reg_addr)
        reg.addWidget(self.reg_val)
        reg.addWidget(rd)
        reg.addWidget(wr)
        af.addRow("Register", reg)
        side.addWidget(adc)

        cap = QGroupBox("Capture")
        cf = QFormLayout(cap)
        self.pairs = QSpinBox()
        self.pairs.setRange(1, rm.CAP_DEPTH)
        self.pairs.setValue(16384)
        self.pairs.setSingleStep(1024)
        self.pairs.setToolTip("Samples per channel; both channels are captured together")
        cf.addRow("Samples", self.pairs)
        cf.addRow("Sample rate", QLabel("32 MS/s (fixed 32 MHz oscillator)"))
        self.view = QComboBox()
        self.view.addItems(["A + jB (complex)", "A", "B"])
        self.view.setToolTip("Spectrum of channel A, B, or A + jB: as I/Q, the complex spectrum\n"
                             "shows which side of the carrier the signal is on")
        self.view.currentIndexChanged.connect(self._replot)
        cf.addRow("Spectrum", self.view)
        go = QPushButton("Capture")
        go.setDefault(True)
        go.clicked.connect(self.capture)
        self.repeat = QCheckBox("Repeat")
        self.repeat.toggled.connect(lambda on: self.capture() if on else None)
        save = QPushButton("Save .npz...")
        save.clicked.connect(self.save)
        crow = QHBoxLayout()
        crow.addWidget(go)
        crow.addWidget(self.repeat)
        cf.addRow(crow)
        cf.addRow(save)
        side.addWidget(cap)

        self.stats = QLabel("no capture yet")
        self.stats.setFont(mono_font())
        self.stats.setWordWrap(True)
        side.addWidget(self.stats)
        side.addStretch(1)

        plots = QVBoxLayout()
        lay.addLayout(plots, 1)
        self.tplot = pg.PlotWidget()
        self.tplot.setLabel("bottom", "time", "s")
        self.tplot.setLabel("left", "code")
        self.tplot.setDownsampling(auto=True, mode="peak")
        self.tplot.setClipToView(True)
        self.tplot.addLegend(offset=(10, 10))
        self.tcurves = {ch: self.tplot.plot([], [], name=ch, pen=pg.mkPen(c, width=1))
                        for ch, c in COLORS.items()}
        self.fplot = pg.PlotWidget()
        self.fplot.setLabel("bottom", "frequency", "Hz")
        self.fplot.setLabel("left", "dBFS")
        self.fcurve = self.fplot.plot([], [], pen=pg.mkPen("#59a14f", width=1))
        style_plot(self.tplot)
        style_plot(self.fplot)
        plots.addWidget(self.tplot, 1)
        plots.addWidget(self.fplot, 1)

    def _reg_read(self):
        def show(lines):
            self.reg_val.setText(lines[0].split("=")[1].strip().removeprefix("0x"))

        self.link.cmd("adc rd " + self.reg_addr.text(), show)

    def capture(self):
        def done(res):
            raw, self.capture_ms = res
            self.last = (raw, unpack(raw))
            self._replot()
            if self.repeat.isChecked():
                QTimer.singleShot(50, self.capture)

        def failed(e):
            self.stats.setText("capture failed: %s" % e)
            self.repeat.setChecked(False)

        n = self.pairs.value()  # read widgets here: the lambda runs on the link thread
        self.link.call(lambda b: (b.capture(n), b.last_capture_ms), done, failed,
                       quiet=self.repeat.isChecked())

    def _replot(self):
        if self.last is None:
            return
        raw, ch = self.last
        n = raw.size
        t = np.arange(n) / ADC_FS
        new_size = self.tcurves["A"].xData is None or self.tcurves["A"].xData.size != n
        for name, curve in self.tcurves.items():
            curve.setData(t, ch[name].astype(np.float64))
        if new_size:  # start zoomed in on the first few hundred samples; zoom out with the wheel
            self.tplot.setXRange(0, min(n, 400) / ADC_FS, padding=0.02)
        self.tplot.setTitle("%d samples per channel" % n)

        view = self.view.currentText()
        w = np.hanning(n)
        if view.startswith("A + jB"):
            x = ch["A"].astype(np.float64) + 1j * ch["B"].astype(np.float64)
            spec = np.fft.fftshift(np.abs(np.fft.fft((x - x.mean()) * w))) / (w.sum())
            f = np.fft.fftshift(np.fft.fftfreq(n, 1 / ADC_FS))
        else:
            x = ch[view].astype(np.float64)
            spec = np.abs(np.fft.rfft((x - x.mean()) * w)) / (w.sum() / 2)
            f = np.fft.rfftfreq(n, 1 / ADC_FS)
        db = 20 * np.log10(np.maximum(spec, 1e-9) / FULL_SCALE)
        self.fcurve.setData(f, db)
        peak = int(np.argmax(db)) if db.size else 0
        lines = ["%s  min %4d  max %4d  mean %7.2f  ac rms %6.2f  clipped %d" % (
            name, s.min(), s.max(), s.mean(), s.std(), int(np.count_nonzero((s >= 511) | (s <= -512))))
            for name, s in ch.items()]
        lines.append("peak (%s): %+.4f MHz at %.1f dBFS" % (view.split(" ")[0] if "j" not in view else "A+jB",
                                                          f[peak] / 1e6, db[peak]))
        lines.append("capture + read-back %.0f ms" % self.capture_ms)
        self.stats.setText("\n".join(lines))

    def save(self):
        if self.last is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save capture", "capture.npz", "NumPy (*.npz)")
        if path:
            raw, ch = self.last
            np.savez(path, raw=raw, fs=ADC_FS, **ch)

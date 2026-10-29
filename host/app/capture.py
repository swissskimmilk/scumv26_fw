"""ADC capture tab: ADC3910 setup, one-channel captures, time and spectrum plots."""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from scum import CAP_MAX_WORDS, unpack

from .theme import style_plot
from .widgets import mono_font

FULL_SCALE = 512  # 10-bit two's complement
FS = 32e6  # ADC3910 clock: the board's 32.000 MHz oscillator; one channel per capture


class CaptureTab(QWidget):
    def __init__(self, link):
        super().__init__()
        self.link = link
        self.last = None  # (raw, mode, channel name, samples)
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
        init, reset = QPushButton("Init"), QPushButton("Reset")
        init.setToolTip("Hardware reset, check CFG_ALERT and SPI read-back, then apply the mode")
        init.clicked.connect(lambda: self.link.cmd("adc init"))
        reset.clicked.connect(lambda: self.link.cmd("adc reset"))
        row.addWidget(init)
        row.addWidget(reset)
        af.addRow(row)
        self.mode = QComboBox()
        self.mode.addItems(["ddr-a", "ddr-b", "sdr-a", "sdr-b"])
        self.mode.setToolTip("ADC output mode: ddr = both channels on both DCLK edges,\n"
                             "sdr = one channel per DCLK (needs SPI)")
        self.mode.currentTextChanged.connect(lambda m: self.link.cmd("adc mode " + m))
        af.addRow("Mode", self.mode)
        pat = QHBoxLayout()
        self.pat_a, self.pat_b = QLineEdit("0x155"), QLineEdit("0x2AA")
        for w in (self.pat_a, self.pat_b):
            w.setMaximumWidth(70)
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
        self.reg_addr, self.reg_val = QLineEdit("0x39"), QLineEdit("0x00")
        for w in (self.reg_addr, self.reg_val):
            w.setMaximumWidth(70)
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
        self.words = QSpinBox()
        self.words.setRange(1, CAP_MAX_WORDS)
        self.words.setValue(16384)
        self.words.setSingleStep(1024)
        self.words.setToolTip("32-bit words; each holds two samples")
        cf.addRow("Words", self.words)
        cf.addRow("Sample rate", QLabel("32 MS/s (fixed 32 MHz oscillator)"))
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
        self.tcurve = self.tplot.plot([], [], pen=pg.mkPen("#4e79a7", width=1))
        self.fplot = pg.PlotWidget()
        self.fplot.setLabel("bottom", "frequency", "Hz")
        self.fplot.setLabel("left", "dBFS")
        self.fcurve = self.fplot.plot([], [], pen=pg.mkPen("#f28e2b", width=1))
        style_plot(self.tplot)
        style_plot(self.fplot)
        plots.addWidget(self.tplot, 1)
        plots.addWidget(self.fplot, 1)

    def _reg_read(self):
        def show(lines):
            self.reg_val.setText(lines[0].split("=")[1].strip())

        self.link.cmd("adc rd " + self.reg_addr.text(), show)

    def capture(self):
        def done(res):
            raw, mode, self.capture_ms = res
            chans = unpack(raw, mode)
            (name, s), = chans.items()
            self.last = (raw, mode, name, s)
            self._replot()
            if self.repeat.isChecked():
                QTimer.singleShot(50, self.capture)

        def failed(e):
            self.stats.setText("capture failed: %s" % e)
            self.repeat.setChecked(False)

        n = self.words.value()  # read widgets here: the lambda runs on the link thread
        self.link.call(lambda b: (*b.capture(n), b.last_capture_ms), done, failed,
                       quiet=self.repeat.isChecked())

    def _replot(self):
        if self.last is None:
            return
        raw, mode, name, s = self.last
        fs = FS
        x = s.astype(np.float64)
        new_size = self.tcurve.xData is None or self.tcurve.xData.size != x.size
        self.tcurve.setData(np.arange(x.size) / fs, x)
        if new_size:  # start zoomed in on the first few hundred samples; zoom out with the wheel
            self.tplot.setXRange(0, min(x.size, 400) / fs, padding=0.02)
        self.tplot.setTitle("channel %s (%s), %d samples" % (name, mode, x.size))
        w = np.hanning(x.size)
        spec = np.abs(np.fft.rfft((x - x.mean()) * w)) / (w.sum() / 2)
        db = 20 * np.log10(np.maximum(spec, 1e-9) / FULL_SCALE)
        f = np.fft.rfftfreq(x.size, 1 / fs)
        self.fcurve.setData(f, db)
        peak = int(np.argmax(db[1:])) + 1 if db.size > 1 else 0
        self.stats.setText(
            "mode    %s (channel %s)\nsamples %d\nmin     %d\nmax     %d\nmean    %.2f\n"
            "ac rms  %.2f\npeak    %.4f MHz at %.1f dBFS\nclipped %d\non board %s ms" % (
                mode, name, x.size, x.min(), x.max(), x.mean(), x.std(), f[peak] / 1e6, db[peak],
                int(np.count_nonzero((s >= 511) | (s <= -512))), self.capture_ms))

    def save(self):
        if self.last is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save capture", "capture.npz", "NumPy (*.npz)")
        if path:
            raw, mode, name, s = self.last
            np.savez(path, raw=raw, mode=mode, fs=FS, **{name: s})

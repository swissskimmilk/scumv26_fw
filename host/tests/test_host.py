"""Host tests against the simulated FPGA (sim.py). Run from the repo root: pytest host/tests"""
import os
import sys
import time
from pathlib import Path

import pytest

HOST = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOST))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from scum import Board, BoardError  # noqa: E402  (scum puts fpga/ on the path)
import regmap as rm  # noqa: E402
from sim import SimFpga  # noqa: E402


@pytest.fixture
def board():
    sim = SimFpga()
    b = Board(transport=sim)
    b.sim = sim
    b.events_seen = []
    b.on_event = b.events_seen.append
    return b


def test_generated_files_match_regmap():
    fpga = HOST.parent / "fpga"
    assert (fpga / "rtl" / "scum_regs_pkg.sv").read_text() == rm._sv(), "run python fpga/regmap.py"
    assert (fpga / "REGISTERS.md").read_text() == rm._md(), "run python fpga/regmap.py"


def test_ident_and_features(board):
    assert board.cmd("id")[0].startswith("SCuMV26 gateware")
    assert board.features == set()
    assert Board(transport=SimFpga(features=0b111)).features == {"I2C", "ADC", "UART"}


def test_power_sequence_and_switches(board):
    assert board.cmd("power up") == ["vio on", "vbat on", "1v on"]
    assert board.cmd("rail 1v off") == ["vio on", "vbat on", "1v off"]
    assert board.cmd("sw vdda board") == ["vddd dut", "vdda board", "rf dut"]
    assert board.cmd("power down") == ["vio off", "vbat off", "1v off"]


def test_trip_drops_rails_and_holds_them(board):
    board.cmd("power up")
    board.cmd("trip on")
    board.sim.alert = True
    board.poll()
    assert board.events_seen and board.events_seen[0].startswith("ALERT")
    assert board.rails() == {"vio": False, "vbat": False, "1v": False}
    with pytest.raises(BoardError, match="held off"):
        board.cmd("rail vio on")
    with pytest.raises(BoardError, match="still asserted"):
        board.cmd("power up")
    board.sim.alert = False
    assert board.cmd("power up") == ["vio on", "vbat on", "1v on"]
    assert board.status()["ocp_trips"] == "1"
    assert board.status()["ocp_trips"] == "0"  # zeroed by the previous read


def test_clock_counter(board):
    board.cmd("power up")
    assert board.cmd("clk sel dig win 20") == ["sel dig win 20 ms resolution 50 Hz"]
    assert board.freq() == pytest.approx(20e6, rel=1e-4)


def test_pins_and_pulse(board):
    board.cmd("power up")
    assert board.cmd("pin SCAN_IN 1") == ["SCAN_IN out 1"]
    assert board.cmd("pin SCAN_OUT") == ["SCAN_OUT in 1"]  # the sim loops scan in to out
    with pytest.raises(BoardError, match="is an input"):
        board.cmd("pin SCAN_OUT 1")
    assert board.cmd("pin BASEBAND_TX out") == ["BASEBAND_TX out 0"]
    t = time.monotonic()
    board.cmd("pulse CORE_RST 20000")
    assert time.monotonic() - t >= 0.02
    assert board.pin("CORE_RST") is True  # restored to its idle level


def test_pads_released_without_vio(board):
    assert board.pins()["CORE_RST"] == (True, False)  # driven high, but VIO is off


def test_missing_blocks_and_bad_commands(board):
    for line, block in (("tele", "I2C"), ("cap 16", "ADC"), ("uart rx", "UART")):
        with pytest.raises(BoardError, match="not in the gateware yet"):
            board.cmd(line)
    for line in ("bogus", "pin NOPE 1", "clk sel x", "rail vio maybe", "reg"):
        with pytest.raises(BoardError):
            board.cmd(line)


def test_framing_error_is_reported(board):
    board.link.dev.write(b"\x00")
    board.poll()
    assert board.events_seen[-1].startswith("LINK")


def test_gui_starts_on_the_simulator():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from app import theme
    from app.link import SIM
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    w = MainWindow(port=SIM, autoconnect=True)
    connected = []
    w.link.connected.connect(connected.append)
    QTimer.singleShot(3000, app.quit)
    app.exec()
    assert connected and "gateware" in connected[0]
    assert not w.tabs.isTabEnabled(w.tabs.indexOf(w.tele))  # no I2C block yet
    assert w.tabs.isTabEnabled(w.tabs.indexOf(w.dut))
    w.close()

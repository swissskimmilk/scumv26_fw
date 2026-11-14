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
    assert board.features == {"I2C", "ADC", "UART", "TL"}
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


def test_trip_switch_off_keeps_rails_up(board):
    assert board.trip_state() == {"enabled": False, "tripped": False, "alert": False}  # reset state
    board.cmd("power up")
    board.sim.alert = True
    board.poll()
    assert board.trip_state() == {"enabled": False, "tripped": False, "alert": True}
    assert board.rails() == {"vio": True, "vbat": True, "1v": True}
    board.cmd("trip on")
    board.poll()
    assert board.trip_state()["tripped"] and not any(board.rails().values())

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
    board.sim.pg_fail = {"VIO"}
    board.set_rail("vio", True)
    assert board.pins()["CORE_RST"] == (True, False)  # VIO enabled but not power good


def test_power_good(board):
    assert board.cmd("pg") == ["vio 0", "vbat 0", "vddd 0", "vdda 0", "rf 0",
                               "sys_1v8 1", "sys_3v3 1", "fpga_1v8 1"]
    board.cmd("power up")
    assert all(board.power_good().values())
    board.cmd("power down")
    board.sim.pg_fail = {"VDDA"}
    with pytest.raises(BoardError, match="no power good from vdda"):
        board.cmd("power up")
    assert board.rails() == {"vio": False, "vbat": False, "1v": False}


def test_gpio(board):
    board.sim.gpio_in = 0b100000
    assert board.cmd("gpio 5") == ["GPIO5 in 1"]
    with pytest.raises(BoardError, match="is an input"):
        board.cmd("gpio 0 1")
    board.cmd("gpio 0 out")
    assert board.cmd("gpio 0 1") == ["GPIO0 out 1"]
    with pytest.raises(BoardError):
        board.cmd("gpio 6")


def test_missing_blocks_and_bad_commands(board):
    old = Board(transport=SimFpga(features=0b011))
    for line, block in (("uart rx", "UART"), ("tl on", "TileLink")):
        with pytest.raises(BoardError, match="not in the gateware yet.*" + block):
            old.cmd(line)
    with pytest.raises(BoardError, match="not in the gateware yet"):
        Board(transport=SimFpga(features=0)).cmd("tele")
    for line in ("bogus", "pin NOPE 1", "clk sel x", "rail vio maybe", "reg"):
        with pytest.raises(BoardError):
            board.cmd(line)


def test_i2c_transactions_and_scan(board):
    assert board.i2c(0x40, bytes([0x3E]), 2) == b"\x54\x49"  # INA237 MFR_ID "TI"
    assert board.cmd("i2c 48 w d0 r 2") == ["00 0C"]           # DAC53401 STATUS: device ID
    assert board.i2c_scan() == [0x10, 0x40, 0x41, 0x43, 0x44, 0x45, 0x48, 0x4C]
    with pytest.raises(BoardError, match="no device answers at 0x22"):
        board.i2c(0x22, b"\x00", 1)
    with pytest.raises(BoardError, match="at most 4"):
        board.i2c(0x40, bytes(5))


def test_telemetry_follows_the_rails(board):
    assert all(m["v"] == 0 and m["ma"] == 0 for m in board.tele().values())
    board.cmd("power up")
    t = board.tele()
    assert t["vio"]["v"] == pytest.approx(1.8, abs=0.004)
    assert t["vio"]["ma"] == pytest.approx(3.0, rel=0.01)
    assert t["vio"]["mv_shunt"] == pytest.approx(6.0, rel=0.01)  # 3 mA through 2 ohms
    assert t["vbat"]["v"] == pytest.approx(1.3839, abs=0.004)    # DAC released
    assert t["vddd"]["v"] == 0  # VDDD from the DUT's own LDO: the board monitor sees nothing
    board.cmd("sw vddd board")
    assert board.tele()["vddd"]["v"] == pytest.approx(0.865, abs=0.004)
    board.sim.i2c_dead.add(0x44)
    board._ina_ok = None  # a fresh connection
    assert board.tele()["vdda"] is None
    assert board.cmd("tele")[4] == "vdda     no reply"


def test_vbat_dac_and_trim(board):
    import scum

    assert board.vbat() == {"on": False, "dac": 0, "vdac": 0.0, "setpoint": pytest.approx(1.3839, abs=1e-4)}
    v = board.vbat(1.5)
    assert v["on"] and v["setpoint"] == pytest.approx(1.5, abs=0.0005)
    assert board.sim.dac.reg[0x21] == v["dac"] << 2
    with pytest.raises(BoardError, match="1.110-1.604"):
        board.vbat(1.7)
    board.cmd("power up")
    v = board.trim(scum.VBAT_MIN + 0.1)
    assert v["measured"] == pytest.approx(scum.VBAT_MIN + 0.1, abs=0.002)
    assert board.cmd("vbat off") == ["dac off (Hi-Z) -> set point 1.3839 V"]


def test_aux_inputs(board):
    rows = board.aux()
    assert [name for _, name, _ in rows] == list(__import__("scum").AUX)
    assert rows[4][2] == pytest.approx(1.8, abs=0.001)  # AIN4 = VIO
    assert board.sim.tla.reg[0x11] == 7                 # left on the last channel


def test_current_limit_trips_and_names_the_rail(board):
    board.cmd("power up")
    board.cmd("trip on")
    assert board.cmd("ilim vddd 10")[3] == "vddd     10.00 mA"
    board.cmd("ilim vdda 2.5")
    assert board.ilim()["vdda"] == pytest.approx(2.5, abs=0.01)
    board.cmd("sw vdda board")
    board.sim.load_ma["vdda"] = 5.0                         # over its 2.5 mA limit
    board.poll()
    assert board.events_seen[-1] == "ALERT over-current trip: all DUT rails switched off: vdda"
    assert not any(board.rails().values())
    board.sim.load_ma["vdda"] = 1.5
    assert board.cmd("power up") == ["vio on", "vbat on", "1v on"]
    # a trip nobody polled: the INA237 still holds ALERT (latched), and power up clears it
    board.sim.load_ma["vdda"] = 5.0
    assert not any(board.rails().values())
    board.sim.load_ma["vdda"] = 1.5
    assert board.trip_state()["alert"]
    assert board.cmd("power up") == ["vio on", "vbat on", "1v on"]
    board.cmd("ilim vdda 0")
    assert board.ilim()["vdda"] == 0


def test_adc_init_registers_and_status(board):
    assert board.cmd("adc") == ["dclk 32000 kHz", "capture none"]
    assert board.cmd("adc init") == ["ADC3910 ready, DCLK 32000 kHz"]
    board.cmd("adc wr 39 3c")
    assert board.cmd("adc rd 39") == ["0x039 = 0x3C"]
    board.sim.adc.dclk = False
    with pytest.raises(BoardError, match="CFG_ALERT never came up .DCLK 0 kHz.; is the ADC powered"):
        board.adc_init()


def test_capture_both_channels_at_once(board):
    import numpy as np

    from scum import ADC_FS, unpack

    board.adc_init()
    raw = board.capture(8192)
    assert raw.size == 8192 and board.last_capture_ms is not None
    ch = unpack(raw)
    a, b = ch["A"].astype(float), ch["B"].astype(float)
    assert np.std(a) == pytest.approx(300 / np.sqrt(2), rel=0.02)
    # A and B are the same sample instants: the 2 MHz I/Q tone shows up at +2 MHz, not -2 MHz
    spec = np.fft.fftshift(np.abs(np.fft.fft(a + 1j * b)))
    f = np.fft.fftshift(np.fft.fftfreq(raw.size, 1 / ADC_FS))
    assert f[np.argmax(spec)] == pytest.approx(2e6, abs=ADC_FS / raw.size)
    assert board.cmd("cap 1000")[0].startswith("A  min ")
    with pytest.raises(BoardError, match="1-65536"):
        board.capture(70000)


def test_adc_test_pattern(board):
    from scum import unpack

    board.adc_init()
    board.cmd("adc pat 155 2aa")
    ch = unpack(board.capture(64))
    assert set(ch["A"]) == {0x155} and set(ch["B"]) == {0x2AA - 1024}  # 0x2AA is negative
    board.cmd("adc pat off")
    assert len(set(unpack(board.capture(64))["A"])) > 1


def test_capture_without_dclk_says_so(board):
    board.sim.adc.dclk = False
    with pytest.raises(BoardError, match="no clock from the ADC"):
        board.capture(16)


def test_tl_frame_layout():
    from scum import TL_BITS, tl_join, tl_pack, tl_unpack, tl_words

    assert TL_BITS == rm.TL_FRAME_BITS == 164
    f = tl_pack(chan=3, opcode=1, size=2, source=5, address=0x8000_0010, data=0xCAFE, union=0xF, last=1)
    assert f & 1 == 1                        # `last` is the first bit on the wire
    assert f >> 161 == 3                     # chanId the last three
    assert f >> 11 & 0xFFFF == 0xCAFE        # data just above corrupt + union + last
    assert tl_unpack(tl_join(tl_words(f))) == dict(chan=3, opcode=1, param=0, size=2, source=5,
                                                  address=0x8000_0010, data=0xCAFE, corrupt=0,
                                                  union=0xF, last=1)


def test_tl_read_write_and_lanes(board):
    board.cmd("power up")
    assert board.cmd("tl")[0] == "TL_CLK 10000 kHz"
    with pytest.raises(BoardError, match="engine is off"):
        board.tl_read(0x8000_0000)
    board.cmd("tl on")
    board.cmd("tl wr 80000000 deadbeef 12345678")
    # 4-byte puts at 0 and 4 land in the two halves of one 8-byte beat, each under its own mask
    assert board.sim.stl.mem[0x8000_0000] == 0x12345678_DEADBEEF
    assert board.cmd("tl rd 80000000 2") == ["0x80000000: 0xDEADBEEF", "0x80000004: 0x12345678"]
    assert board.tl_read(0x8000_0006, 1, size=1) == [0x1234]
    with pytest.raises(BoardError, match="not aligned"):
        board.tl_read(0x8000_0002)
    board.cmd("tl off")
    assert board.sim._tl_tx == []


def test_tl_load_verifies(board, tmp_path, monkeypatch):
    import scum

    board.cmd("power up")
    board.cmd("tl on")
    img = tmp_path / "fw.bin"
    img.write_bytes(bytes(range(256)) * 4 + b"\x01\x02")  # not a whole number of words
    monkeypatch.setattr(scum, "TL_WINDOW", 4)                # several requests in flight
    assert board.cmd("tl load %s 80001000" % img)[0].startswith("loaded and verified 1028 bytes")
    assert board.tl_read(0x8000_1400) == [0x0201]
    # a DUT whose memory changes under us fails the verify
    orig = board.sim.stl.handle

    def flaky(frame):
        board.sim.stl.mem[0x8000_1000] = 0
        return orig(frame)

    board.sim.stl.handle = flaky
    with pytest.raises(BoardError, match="0x80001000 reads"):
        board.tl_load(img, 0x8000_1000)


def test_tl_without_a_clock_says_so(board):
    board.cmd("tl on")  # the DUT is not powered, so nothing clocks the link
    assert "not clocking" in board.cmd("tl")[0]
    with pytest.raises(BoardError, match="no TL_CLK"):
        board.tl_write(0x8000_0000, [1])


def test_uart_echo_baud_and_errors(board):
    assert board.cmd("uart") == ["baud 115207, 0 bytes waiting, 0 to send"]
    board.cmd("uart tx hello")
    assert board.cmd("uart rx") == ["(nothing)"]  # the DUT is off: nothing comes back
    board.cmd("power up")
    board.cmd("uart tx hello there")
    assert board.cmd("uart rx") == ["hello there"]
    board.sim.uart_say(b"\xff\xfeok\r\n")
    assert board.cmd("uart rx") == ["\\xff\\xfeok"]
    board.uart_tx(b"x" * 3000)              # more than the FIFO: sent in pieces
    assert len(board.uart_rx()) == 2048 and "overrun" in board.events_seen[-1]
    assert board.cmd("uart baud 9600") == ["baud 9600"]
    board.cmd("uart tx a")
    board.uart_rx()
    assert "framing" in board.events_seen[-1]
    with pytest.raises(BoardError, match="out of reach"):
        board.cmd("uart baud 10000000")


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
    w = MainWindow(port=SIM)
    connected = []
    w.link.connected.connect(connected.append)
    w.toggle_connection()  # after hooking `connected`, so the signal cannot be missed
    QTimer.singleShot(3000, app.quit)
    app.exec()
    assert connected and "gateware" in connected[0]
    assert w.tabs.isTabEnabled(w.tabs.indexOf(w.tele))
    assert w.tabs.isTabEnabled(w.tabs.indexOf(w.capture))
    assert w.tabs.isTabEnabled(w.tabs.indexOf(w.uart))
    assert w.dut.tl_box.isEnabled()
    assert w.tabs.isTabEnabled(w.tabs.indexOf(w.dut))
    w.close()


def test_command_line_runs_through_the_connected_app(monkeypatch):
    pytest.importorskip("PySide6")
    import socket
    import threading

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    import scum
    from app import share
    from app.link import SIM
    from app.main_window import MainWindow

    with socket.socket() as s:  # a free port, so a running app is left alone
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(share, "APP_PORT", port)

    app = QApplication.instance() or QApplication([])
    w = MainWindow(port=SIM)
    traffic, refreshed, out = [], [], {}
    w.link.traffic.connect(lambda d, text, quiet: quiet or traffic.append((d, text)))
    w.share.ran.connect(lambda: refreshed.append(1))

    def cli():
        try:
            b = scum.AppBoard(port)
            out["up"] = b.cmd("power up")
            out["rail"] = b.cmd("rail")
            try:
                b.cmd("nonsense")
            except BoardError as e:
                out["err"] = str(e)
        except Exception as e:  # noqa: BLE001
            out["exc"] = e

    w.link.connected.connect(lambda _: threading.Thread(target=cli, daemon=True).start())
    w.toggle_connection()
    QTimer.singleShot(3000, app.quit)
    app.exec()
    assert "exc" not in out, out.get("exc")
    assert out["rail"] == ["vio on", "vbat on", "1v on"]
    assert "unknown command" in out["err"]
    assert (">", "power up") in traffic  # shown in the app's console like a typed command
    assert refreshed
    w.close()
    with pytest.raises(OSError):  # closed app: the command line falls back to the board
        scum.AppBoard(port)

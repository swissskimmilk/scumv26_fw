# scumv26_fw

Gateware and PC software for the SCuMV26 bring-up board. An Artix-7 FPGA (XC7A100T-2FGG676I)
powers, clocks and talks to the SCuMV26 DUT. A desktop app and a Python library drive it over
USB.

The FPGA is a dumb, reliable instrument with a register map. The test logic lives in Python on
the PC.

```
PC ── USB ── FT2232H ─┬─ channel A: JTAG (Vivado programs the FPGA)
                      └─ channel B: async 245 FIFO ── FPGA register bus ── DUT power, clock, pins
```

## Layout

| Path | What |
|---|---|
| [`fpga/`](fpga/README.md) | the gateware (SystemVerilog), its register map, testbench and Vivado build |
| `fpga/regmap.py` | **the register map**: one Python file that generates the RTL package and [`REGISTERS.md`](fpga/REGISTERS.md), and that the host imports |
| `host/scum.py` | Python library and command-line tool |
| `host/scum_app.py`, `host/app/` | the desktop app (PySide6 + pyqtgraph) |
| `host/sim.py` | a simulated board that answers the same register packets, for working without hardware |
| `host/tests/` | host tests against the simulator (run in CI) |
| `packaging/` | PyInstaller spec, the Windows installer script, and build scripts for Windows and macOS |
| `.github/workflows/build.yml` | CI: tests, app builds, releases |

## Install the app

Download the latest build from the repo's **Releases** page:

- **Windows**: `SCuMV26-<ver>-windows-setup.exe`. It installs per user with no admin prompt.
  SmartScreen will warn because the app is not code-signed: choose *More info → Run anyway*.
- **macOS** (Apple silicon): `SCuMV26-<ver>-macos.dmg`. Drag the app to Applications. It is not
  notarized, so the first launch is blocked: open *System Settings → Privacy & Security* and
  choose *Open Anyway*.

The app finds the board by itself. *Simulator* in the port list runs it with no hardware.

**USB driver.** The register link uses FTDI's D2XX driver.
- **Windows:** the driver is already there if Vivado's cable drivers are installed. Otherwise
  install FTDI's CDM driver package.
- **macOS:** install FTDI's D2XX package (`libftd2xx.dylib`). The board link has not yet been
  tried on a Mac; the app and the simulator have.

Programming the FPGA needs Vivado, which runs on Windows and Linux only. Once the bitstream is in
the board's SPI flash, a Mac only needs the app.

## First-time board setup

Do this once per board, on a PC with Vivado:

1. **FTDI EEPROM.** In a Vivado Tcl shell, with the board on USB, run
   `program_ftdi -write -ftdi FT2232H -serial <id> -vendor "UC Berkeley" -board "SCuMV26 bring-up" -desc "SCuMV26 FPGA"`.
   This makes channel A a JTAG cable that Vivado recognises.
2. **Channel B.** In FTDI's FT_PROG, set **channel B** to *245 FIFO* with the *D2XX* driver, and
   leave channel A alone. AMD (UG908) documents that changing the unused channel keeps the JTAG
   detection working.
3. **Bitstream.** Build it (below) or take it from a release. Program it from the Vivado Hardware
   Manager:
   - `fpga/out/scum_top.bit` over JTAG, for this session only;
   - `fpga/out/scum_top.bin` into the SPI flash (x4), so it loads at power-up.

## Use it

```powershell
python -m venv .venv; .venv\Scripts\pip install -r host\requirements.txt
.venv\Scripts\python host\scum_app.py          # the app; connects to the board if it is plugged in
.venv\Scripts\python host\scum_app.py --sim    # the app on the simulated board
.venv\Scripts\python host\scum.py              # interactive prompt (`help` lists commands)
.venv\Scripts\python host\scum.py power up     # one command
```

On macOS, use `.venv/bin/python` and forward slashes.

While the app is connected it holds the board, so `scum.py` sends its commands through the app
(localhost port 29563, `SCUM_APP_PORT` to change it): they appear in the app's console and its
panels follow. With no app connected, `scum.py` opens the board itself; `--direct` forces that.

### The app

The left column has three panels:
- **DUT power:** sequenced power up and down, single rails, and the rail-source switches.
- **DUT clock:** the clock counter.
- **Board status:** the board's event counters.

The tabs:

- **Telemetry**: the six INA237 rail monitors (bus voltage, current, shunt voltage, a strip
  chart), each rail's over-current limit, and the TLA2528's DUT analog inputs.
- **DUT I/O**: every DUT control pin by name. Each pin can be turned between input and output,
  or driven. The *Reset DUT* and *BOOT_SEL* shortcuts are here too, and the serial TileLink
  engine: on/off, the TL_CLK rate, word reads and writes, and loading a binary into the DUT.
- **Console**: every command and reply, plus a prompt for typing commands.
- **ADC capture**: ADC3910 set-up (reset and SPI check, test patterns, registers) and
  captures of both channels at once, up to 65536 samples each: time traces of A and B, and the
  spectrum of A, B or A + jB.
- **DUT UART**: a terminal on the DUT's serial port: baud rate, received text (polled), and a
  line to send.

A tab or control whose gateware block is missing is greyed out; the app reads the FEATURES
register to know which blocks are there.

An over-current trip raises a red banner. Ctrl+Shift+D powers the DUT down from anywhere in the
window.

### Commands

`scum.py`, the app's console and `Board.cmd()` all take the same text commands:

```
help                                 list commands
id                                   gateware version and the blocks it has
status [peek]                        event counters (trips, button resets, link framing errors)
                                     and uptime; zeroed after reading unless `peek`
power up | down                      sequenced: VIO -> VBAT -> 1V rails (reverse for down);
                                     power up clears a latched trip once ALERT has gone
rail [vio|vbat|1v on|off]            single regulator enable
sw [vddd|vdda|rf board|dut]          rail-source switches
trip [on|off|clear]                  hardware over-current trip on TELEM_ALERT
clk [sel dig|ana|off] [win ms]       DUT clock counter: LMK1D input and counting window
freq                                 DUT clock frequency from the next complete window
pin [name [0|1|in|out]]              read a pin, drive it, or turn it around
pulse <name> <us>                    invert an output for <us> microseconds (timed in the FPGA)
reg <addr> [value]                   raw register read or write
tele                                 the six INA237 rail monitors: bus V, current, shunt mV
ilim [rail mA]                       INA237 over-current limit per rail (0 = off)
aux                                  the TLA2528's eight DUT analog inputs, volts
vbat [V | off]                       VBAT set point through the DAC (open loop), or released
trim <V>                             VBAT to <V> closed loop on its INA237 (VBAT must be on)
i2c scan | recover                   list responding addresses / free a stuck bus
i2c <addr> [w b0 b1 ..] [r n] [sep]  raw transaction (hex bytes, up to 4 each way)
adc                                  ADC3910: DCLK rate, capture state
adc init                             hardware reset, wait out its calibration, check SPI
adc rd <addr> | wr <addr> <val>      ADC3910 register (hex)
adc pat <a> <b> | pat off            constant test codes on channels A and B (10-bit, hex)
cap [pairs]                          capture both channels and print per-channel statistics
tl                                   serial TileLink: TL_CLK rate, engine state, FIFOs
tl on | off                          the engine takes over / releases the TileLink pins
tl rd <addr> [n]                     n 32-bit words from the DUT's address space (hex)
tl wr <addr> <word> [word ..]        write 32-bit words (hex), each a PutFullData
tl load <file> <addr>                write a binary file into the DUT's memory, then verify it
uart                                 DUT UART: baud, bytes waiting, errors
uart baud <n>                        8N1 at n baud (default 115200)
uart tx <text>                       send text and CR LF
uart rx                              what the DUT has sent since the last read
```

From Python, `Board.capture(n)` returns the raw capture words and `scum.unpack(raw)` splits them
into the two channels' signed samples. `Board.tl_read`, `tl_write` and `tl_load` take any
access size; `Board.tl_xfer` sends raw TileLink messages and returns the answers.

The telemetry devices are driven from Python, one I2C transaction per register access:

| Device | Address | What |
|---|---|---|
| INA237 U13, U15, U17, U18, U14, U16 | 0x40, 0x41, 0x43, 0x44, 0x45, 0x4C | VIO (2 R), VBAT (0.82 R), VDDD (1.6 R), VDDA (5.6 R), VBAT_RF (1.6 R), RF_VDD (1.6 R) |
| TLA2528 U20 | 0x10 | AIN0-7: VNFC, spare 3, spare 4, PWR_I_REF, VIO, PWR_VDD_RF, spare 1, spare 2 (3.3 V reference) |
| DAC53401 U28 | 0x48 | VBAT trim: 1.110 V (code 1023) to 1.604 V (code 0); released (Hi-Z, the power-on state) leaves 1.384 V |

### From Python

```python
from scum import Board
b = Board()                        # the first board found; Board(transport=sim.SimFpga()) for the simulator
b.power_up()
b.clk(sel="dig", win=100); print(b.freq())
b.direction("BASEBAND_TX", True); b.pin("BASEBAND_TX", 1)
b.pulse("CORE_RST", 1000)
b.link.read(0x0000)                # raw register access (fpga/REGISTERS.md)
```

## Develop

- **Host tests:** `pytest host/tests` runs against the simulator, including starting the app
  headless.
- **Gateware:** see [`fpga/README.md`](fpga/README.md) for the xsim testbench, the Vivado build,
  and how to change the register map.
- **App builds:**
  - Windows: `.\packaging\build_app.ps1 -Version 1.2.3` writes `dist\SCuMV26\`, plus the
    installer if Inno Setup 6 is installed.
  - macOS: `./packaging/build_app.sh 1.2.3` writes the `.app` and the `.dmg`. It has to run on
    a Mac, because PyInstaller only builds for the OS it runs on.

### CI and releases

On every push and pull request, CI runs the host tests on Windows and macOS. On pushes to `main`,
it also builds the Windows installer and the Mac disk image, smoke-tests both against the
simulator, and keeps them as workflow artifacts.

A version tag publishes a release with both files:

```
git tag v0.2.0
git push origin v0.2.0
```

Vivado does not fit on a hosted runner, so the bitstream is built locally. Attach
`fpga/out/scum_top.bit` and `.bin` to the release by hand.

## Status and open items

- **Nothing has run on hardware yet.** The gateware passes its testbench (125 checks) and meets
  timing; the host passes its tests against the simulator.
- **Gateware still to build: the scan-chain engine.** Its pins are plain DUT GPIO for now. It
  waits on the SCuM-V26 scan-chain spec: SCuM-V24B's chain had no SCAN_OUT or SCAN_SEL.
- **Serial TileLink follows SCuM-V24B's frame,** from the public bring-up repo
  (ucb-ee290c/scum-v-bringup, `GenericSerializer.sv`): 164 bits, LSB first,
  {chanId, opcode, param, size, source, address[64], data[64], corrupt, union[9], last}. To
  confirm on V26:
  - the field layout (`TL_FIELDS` in `scum.py`; the FPGA only needs TL_FRAME);
  - the byte lanes: data and mask follow `address % 8` (`TL_BEAT`), where V24B's own host sent
    mask 0xFF with the data unshifted;
  - how many requests may be in flight (`TL_WINDOW`, 1 for now: one round trip per word).
- **TL_CLK comes from the DUT.** The FPGA no longer drives it at reset. The engine samples and
  drives on TL_CLK's falling edges, half a period from the DUT's rising edges.
- **CORE_RST polarity is unconfirmed.** The pin idles high, matching the fitted pull-up, and
  pulses invert whatever level it is at.
- **The over-current trip** acts on the INA237s' shared ALERT line. It is off at start-up: set
  the limits (`ilim`, or the Telemetry tab), then turn it on with `trip on` or the *Rails off on
  TELEM_ALERT* box. The INA237s latch their alerts, so a trip names the rail that caused it.
- **The ADC3910 runs in its power-on default** (10-bit DDR, two's complement); the gateware
  does not configure it. The test-pattern codes are written to the top 10 of the ADC's 14-bit
  pattern registers, which is an assumption to confirm on the board (`adc pat`, then `cap`).
- **The VBAT set point is open loop** on SYS_1V8 as the DAC's reference; `trim` closes the loop on
  the VBAT monitor.

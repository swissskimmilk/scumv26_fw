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

### The app

The left column has three panels:
- **DUT power:** sequenced power up and down, single rails, and the rail-source switches.
- **DUT clock:** the clock counter.
- **Board status:** the board's event counters.

The tabs:

- **DUT I/O**: every DUT control pin by name. Each pin can be turned between input and output,
  driven, or pulsed for a time the FPGA measures. The *Reset DUT* and *BOOT_SEL* shortcuts are
  here too.
- **Console**: every command and reply, plus a prompt for typing commands.
- **Telemetry**, **ADC capture**, **DUT UART**: these are greyed out until the gateware has the
  blocks they need. The app reads the FEATURES register to know which blocks are there.

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
```

`tele`, `ilim`, `aux`, `vbat`, `adc`, `cap` and `uart` answer `ERR not in the gateware yet` until
their blocks are built.

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

- **Nothing has run on hardware yet.** The gateware passes its testbench (44 checks) and meets
  timing; the host passes its tests against the simulator.
- **Gateware blocks still to build:**
  - the I2C master for the INA237 monitors, the TLA2528 and the VBAT DAC;
  - ADC3910 capture and its SPI;
  - the DUT UART;
  - TileLink and scan-chain engines. Their pins are plain DUT GPIO for now.
- **CORE_RST polarity is unconfirmed.** The pin idles high, matching the fitted pull-up, and
  pulses invert whatever level it is at.
- **The over-current trip** acts on the INA237s' shared ALERT line, so it only means something
  once the I2C block programs their limits. Enable it with `trip on` after that.

# FPGA gateware

Gateware for the bring-up board's XC7A100T-2FGG676I. The FPGA is a dumb, reliable instrument
with a register map; the sequencing and test logic live in Python on the PC (`host/scum.py`).

The PC reaches the registers through the FT2232H's channel B, in async 245 FIFO mode. Channel A
is the JTAG cable that Vivado programs the FPGA through.

## Layout

| Path | What |
|---|---|
| `regmap.py` | the register map: **the one definition** of addresses, bits and packet bytes. `python fpga/regmap.py` regenerates `rtl/scum_regs_pkg.sv` and `REGISTERS.md`. `host/scum.py` imports it. |
| [`REGISTERS.md`](REGISTERS.md) | the register map as a table (generated) |
| `rtl/` | SystemVerilog, one block per file |
| `constraints/scum_top.xdc` | pins and I/O standards, taken from the Altium schematic, plus clocks |
| `sim/` | `tb_scum_top.sv` (a behavioural FT2232H plus the board around the FPGA) and `run.ps1` |
| `build.tcl` | non-project Vivado build, to `out/` (gitignored) |

## Blocks

| Block | File | What it does |
|---|---|---|
| FT2232H async FIFO | `ft245_async.sv` | byte stream to and from the PC, with the datasheet's strobe timing |
| Host bridge | `host_bridge.sv` | turns packets into register reads and writes; drops stalled or malformed packets |
| System | `sys_ctrl.sv` | ID, version, FEATURES, uptime, event flags and counters |
| Power | `power_ctrl.sv` | rail enables, rail-source switches, **hardware over-current trip** |
| Clock counter | `clk_counter.sv` | counts the DUT clock through the LMK1D1204; drives its input select |
| DUT I/O | `dut_io.sv` | every DUT control pin: direction, drive, read, timed pulses, reset button |

FEATURES (0x0020) says which optional blocks a build has, so the host greys out the rest. Set
its bit in `regmap.py` when a block lands. Not built yet, with their pins held idle in
`scum_top.sv`:
- the I2C master for the INA237s, the TLA2528 and the DAC53401 (VBAT trim);
- ADC capture (both channels at once, into BRAM) and the ADC's SPI;
- the DUT UART;
- TileLink and scan-chain engines. Their pins are plain DUT GPIO for now.

How the parts that matter work:
- **One clock domain** for everything the host sees: the 100 MHz oscillator into a BUFG, with
  no MMCM.
- **The over-current trip is hardware.** TELEM_ALERT drops all three enables two clock edges
  (20 ns) after it reaches the pin. The rails stay off until the host clears TRIP_STATUS.
- **DUT pads are released while VIO is off,** so the FPGA cannot back-power the DUT through its
  I/O ring. Every pad is bidirectional, and the host picks directions in DUT_OE.
- **The clock counter has no prescaler.** It counts the DUT clock directly, constrained to
  500 MHz. A 16-bit counter runs in the DUT clock's domain and crosses to the system clock in
  Gray code. The resolution is 1 / window: 10 Hz at the default 100 ms.
- **Pulses are timed in hardware,** to the 10 ns system clock.

## Simulate

```powershell
.\fpga\sim\run.ps1
```

This runs xsim from the Vivado install (`C:\AMDDesignTools\2026.1\Vivado`, or
`$env:XILINX_VIVADO`) and simulates about 60 ms of board time. It covers:
- the link, framing errors and timeouts;
- rails and the trip;
- pads with VIO off;
- pulses and the button's debounce;
- the counter at 32.768 kHz, 50 MHz and 500 MHz.

The FT2232H model also checks the strobe widths and data setup and hold against the datasheet
minimums. The run ends with `ALL n CHECKS PASSED`.

## Build

```powershell
vivado -mode batch -nojournal -source fpga\build.tcl
```

This writes `out/scum_top.bit` (for JTAG), `out/scum_top.bin` (for the SPI flash, x4) and the
reports. The build fails if timing is not met.

## Changing the register map

1. Edit `regmap.py`.
2. Run `python fpga/regmap.py`.
3. Update the block that owns the register, and the testbench.

The host tests fail if the generated files are out of date.

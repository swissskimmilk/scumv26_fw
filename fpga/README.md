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
| Power | `power_ctrl.sv` | rail enables, rail-source switches, **hardware over-current trip**, the regulators' power-good pins |
| Clock counter | `clk_counter.sv` | counts the DUT clock through the LMK1D1204; drives its input select |
| DUT I/O | `dut_io.sv` | every DUT control pin: direction, drive, read, timed pulses, reset button |
| Header GPIO | `gpio.sv` | FPGA_GPIO0-5 on J16: direction, drive, read (released at reset, pulled down) |
| ADC3910 | `adc.sv` | its SPI and reset pin, a DCLK monitor, and captures of both channels (IDDR on DCLK) into a 64K-pair buffer read back through a data port |
| Serial TileLink | `tl_serial.sv`, `async_fifo.sv` | the DUT's 1-bit TileLink port on its own TL_CLK: frames of TL_FRAME bits each way, through two-clock FIFOs the host fills and drains |
| DUT UART | `uart.sv` | 8N1 on FPGA_UART_TX/RX at a set divider, a FIFO each way, framing and overrun flags |
| Telemetry I2C | `i2c_master.sv` | one transaction per command on TELEM_SCL/SDA: write up to 4 bytes, read up to 4; probe, clock stretching, stuck-bus recovery |

FEATURES (0x0020) says which optional blocks a build has, so the host greys out the rest. Set
its bit in `regmap.py` when a block lands. Not built yet: the scan-chain engine (its pins are
plain DUT GPIO for now).

How the parts that matter work:
- **One clock domain** for everything the host sees: the 100 MHz oscillator into a BUFG, with
  no MMCM.
- **The over-current trip is hardware.** TELEM_ALERT drops all three enables two clock edges
  (20 ns) after it reaches the pin. The rails stay off until the host clears TRIP_STATUS.
- **DUT pads are released until VIO is on and in regulation** (RAIL_EN.VIO and the VIO
  regulator's PG on K2), so the FPGA cannot back-power the DUT through its I/O ring or drive it
  while VIO ramps. Every pad is bidirectional, and the host picks directions in DUT_OE.
- **RAIL_PG reads all eight power-good pins.** `power up` waits for each step's PG and powers
  back down if one does not come up.
- **The clock counter has no prescaler.** It counts the DUT clock directly, constrained to
  500 MHz. A 16-bit counter runs in the DUT clock's domain and crosses to the system clock in
  Gray code. The resolution is 1 / window: 10 Hz at the default 100 ms.
- **Pulses are timed in hardware,** to the 10 ns system clock.
- **Both ADC channels in every capture.** The ADC3910's default DDR output puts channel A
  around DCLK's rising edge and B around its falling edge on the same ten lanes. An IDDR per
  lane, clocked by DCLK itself through a BUFG, hands each A/B pair over on one edge, and the pair
  goes into a two-clock block RAM written on DCLK. The host reads it with a burst of CAP_DATA.
  The data pins have centre-aligned DDR input delays in the constraints, so they are timed.
- **The TileLink engine shifts frames, not messages.** The host packs each TileLink message into
  TL_TX words and unpacks the answers from TL_RX (`scum.py`, `TL_FIELDS`), so a chip whose frame
  layout differs needs only TL_FRAME and that table. The DUT drives TL_CLK (on an MRCC pin,
  through a BUFG). The engine runs on its falling edges: it samples the DUT's valid, ready and
  data mid-bit and changes its own half a period before the DUT samples them, leaving half a
  period either way for the BUFG delay and the pads. While TL_CTRL.EN is set the engine owns the
  seven TileLink pads, still released until VIO is good.
- **The DUT UART** is 8N1 at SYS_HZ / UART_DIV, with a 512-byte transmit FIFO and a 2048-byte
  receive FIFO. TX is released, and RX ignored, until VIO is good.
- **The I2C master knows no device.** Each I2C_CMD runs one transaction (START, address, up to 4
  bytes written, a repeated START or STOP + START, up to 4 bytes read, STOP) at 100 kHz by
  default (I2C_DIV). The host builds the INA237, TLA2528 and DAC53401 protocols out of those.
  A device holding SCL low for 10 ms ends the transaction; RECOVER clocks a stuck SDA free.

## Simulate

```powershell
.\fpga\sim\run.ps1
```

This runs xsim from the Vivado install (`C:\AMDDesignTools\2026.1\Vivado`, or
`$env:XILINX_VIVADO`) and simulates about 60 ms of board time. It covers:
- the link, framing errors and timeouts;
- rails, the trip and power good;
- pads with VIO off or not yet power good;
- the header GPIO;
- the ADC3910 against a model: SPI writes and reads, the reset pin, the DCLK monitor, and a
  capture of DDR ramp data checked pair by pair (no gaps, A and B from the same sample);
- serial TileLink against a model of the chip's serdes (10 MHz TL_CLK from the "DUT"): the
  TL_CLK monitor, Put, Get and PutPartial frames and their answers, three frames queued back to
  back while the chip's ready drops every fifth bit, and EN off emptying both FIFOs;
- the DUT UART at 1 Mbaud: two bytes out, two in through a burst, an empty read, and a framing
  error;
- the I2C master against device models: writes, repeated START and STOP + START reads, probes
  and NACKs, clock stretching, 400 kHz, a stuck SDA and RECOVER, and the SCL timeout;
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

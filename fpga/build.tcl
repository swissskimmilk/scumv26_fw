# Build the bitstream, non-project flow. From the repo root:
#   vivado -mode batch -nojournal -source fpga/build.tcl
# Writes fpga/out/: scum_top.bit, scum_top.bin (for the SPI flash), and the utilisation,
# timing, DRC and power reports. Fails if timing does not close.
set here [file dirname [file normalize [info script]]]
set out $here/out
file mkdir $out
set part xc7a100tfgg676-2

foreach f {scum_regs_pkg sync2 ft245_async host_bridge sys_ctrl power_ctrl clk_counter dut_io scum_top} {
    read_verilog -sv $here/rtl/$f.sv
}
read_xdc $here/constraints/scum_top.xdc

synth_design -top scum_top -part $part
opt_design
place_design
phys_opt_design
route_design

report_utilization -file $out/utilization.txt
report_timing_summary -max_paths 20 -file $out/timing.txt
report_drc -file $out/drc.txt
report_power -file $out/power.txt
report_methodology -file $out/methodology.txt

if {[get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]] < 0 ||
    [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]] < 0} {
    error "timing not met: see $out/timing.txt"
}

write_bitstream -force $out/scum_top.bit
write_cfgmem -force -format bin -interface SPIx4 -size 16 -loadbit "up 0x0 $out/scum_top.bit" \
    -file $out/scum_top.bin
puts "built $out/scum_top.bit"

# Simulate scum_top with Vivado's xsim. Run from anywhere: .\fpga\sim\run.ps1
# Prints one pass/FAIL line per check and ends with "ALL n CHECKS PASSED".
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$rtl = Join-Path $here "..\rtl"
$vivado = if ($env:XILINX_VIVADO) { $env:XILINX_VIVADO } else { "C:\AMDDesignTools\2026.1\Vivado" }
if (-not $env:XILINXD_LICENSE_FILE) { $env:XILINXD_LICENSE_FILE = "$env:APPDATA\Xilinx\Xilinx.lic" }
$work = Join-Path $here "work"
New-Item -ItemType Directory -Force $work | Out-Null
Push-Location $work
try {
    $src = @("scum_regs_pkg.sv", "sync2.sv", "ft245_async.sv", "host_bridge.sv", "sys_ctrl.sv",
             "power_ctrl.sv", "clk_counter.sv", "dut_io.sv", "scum_top.sv") | ForEach-Object { Join-Path $rtl $_ }
    & "$vivado\bin\xvlog.bat" --sv @src (Join-Path $here "tb_scum_top.sv") "$vivado\data\verilog\src\glbl.v"
    if ($LASTEXITCODE) { throw "xvlog failed" }
    & "$vivado\bin\xelab.bat" -L unisims_ver tb_scum_top glbl -s tb -timescale 1ns/1ps
    if ($LASTEXITCODE) { throw "xelab failed" }
    & "$vivado\bin\xsim.bat" tb -R
    if ($LASTEXITCODE) { throw "xsim failed" }
} finally {
    Pop-Location
}

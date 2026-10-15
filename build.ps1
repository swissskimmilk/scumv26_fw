# Build (and optionally flash) using the toolchain bundled with STM32CubeIDE.
#   .\build.ps1            configure + build Debug
#   .\build.ps1 -Flash     ...then program over SWD with an ST-Link and reset
#   .\build.ps1 -Release
param([switch]$Flash, [switch]$Release)

# Native tools report failure through $LASTEXITCODE (Windows PowerShell 5.1 would turn
# their stderr chatter into errors under ErrorActionPreference=Stop).
$ide = Get-ChildItem "C:\ST" -Directory -Filter "STM32CubeIDE_*" | Sort-Object Name | Select-Object -Last 1
if (-not $ide) { throw "STM32CubeIDE not found under C:\ST" }
$plugins = Join-Path $ide.FullName "STM32CubeIDE\plugins"
$gcc = Get-ChildItem $plugins -Directory -Filter "*gnu-tools-for-stm32*win32*" | Sort-Object Name | Select-Object -Last 1
$env:PATH = (Join-Path $gcc.FullName "tools\bin") + ";" + $env:PATH

$preset = if ($Release) { "Release" } else { "Debug" }
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path "lib\tinyusb\src\tusb.c")) { git submodule update --init }
    cmake --preset $preset
    if ($LASTEXITCODE) { throw "configure failed" }
    cmake --build "build\$preset"
    if ($LASTEXITCODE) { throw "build failed" }

    if ($Flash) {
        $prog = Get-ChildItem $plugins -Directory -Filter "*cubeprogrammer*win32*" | Sort-Object Name | Select-Object -Last 1
        $cli = Join-Path $prog.FullName "tools\bin\STM32_Programmer_CLI.exe"
        & $cli -c port=SWD mode=UR -w "build\$preset\scumv26_fw.elf" -v -rst
        if ($LASTEXITCODE) { throw "flash failed" }
    }
} finally {
    Pop-Location
}

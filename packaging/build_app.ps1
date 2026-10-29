# Build the Windows app and installer. From the repo root:  .\packaging\build_app.ps1 [-Version 1.2.3]
# Needs the .venv (python -m venv .venv; .venv\Scripts\pip install -r host\requirements.txt
# pyinstaller) and, for the installer, Inno Setup 6 (winget install JRSoftware.InnoSetup).
# Without Inno Setup it stops after dist\SCuMV26\, which runs as it is.
param([string]$Version = "0.0.0")
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$env:APP_VERSION = $Version
& $py -m PyInstaller --noconfirm --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build\pyinstaller") `
    (Join-Path $PSScriptRoot "SCuMV26.spec")
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) {
    Write-Host "Inno Setup not found: built dist\SCuMV26\ only (no installer)"
    exit 0
}
& $iscc "/DAppVersion=$Version" (Join-Path $PSScriptRoot "installer.iss")
if ($LASTEXITCODE) { throw "Inno Setup failed" }

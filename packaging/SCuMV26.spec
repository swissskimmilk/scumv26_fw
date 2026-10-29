# PyInstaller spec for the desktop app, shared by Windows and macOS. Build from the repo root:
#   pyinstaller --noconfirm packaging/SCuMV26.spec
# Writes dist/SCuMV26/ (Windows: SCuMV26.exe inside) or dist/SCuMV26.app (macOS).
# PyInstaller only builds for the OS it runs on: the Mac app has to be built on a Mac (CI does).
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VERSION = os.environ.get("APP_VERSION", "0.0.0")

a = Analysis(
    [os.path.join(ROOT, "host", "scum_app.py")],
    pathex=[os.path.join(ROOT, "host"), os.path.join(ROOT, "fpga")],
    hiddenimports=["sim", "regmap", "ftd2xx"],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "matplotlib", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="SCuMV26",
    console=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="SCuMV26")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="SCuMV26.app",
        bundle_identifier="edu.berkeley.scumv26.bringup",
        version=VERSION,
        info_plist={"NSHighResolutionCapable": True, "LSMinimumSystemVersion": "12.0"},
    )

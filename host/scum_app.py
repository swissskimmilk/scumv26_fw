"""SCuMV26 bring-up desktop app.

    python scum_app.py              pick a port and press Connect
    python scum_app.py --sim        connect to the simulated board straight away
    python scum_app.py --port FT9ABCDB   connect to that board (FT2232H channel B serial)
"""
import argparse
import sys

from PySide6.QtWidgets import QApplication

from app import theme
from app.main_window import MainWindow
from scum import list_devices
from app.link import SIM


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--sim", action="store_true")
    a = ap.parse_args()
    port = SIM if a.sim else a.port
    if port is None:
        boards, _ = list_devices()
        port = boards[0][0] if boards else None

    app = QApplication(sys.argv[:1])
    theme.apply(app)
    w = MainWindow(port=port, autoconnect=port is not None)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

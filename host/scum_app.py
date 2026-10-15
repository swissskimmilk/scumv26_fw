"""SCuMV26 bring-up desktop app.

    python scum_app.py              pick a port and press Connect
    python scum_app.py --sim        connect to the simulated board straight away
    python scum_app.py --port COM7  connect to that port straight away
"""
import argparse
import sys

from PySide6.QtWidgets import QApplication

from app import theme
from app.main_window import MainWindow
from scum import Board, BoardError
from app.link import SIM


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--sim", action="store_true")
    a = ap.parse_args()
    port = SIM if a.sim else a.port
    if port is None:
        try:
            port = Board.find_port()
        except BoardError:
            port = None

    app = QApplication(sys.argv[:1])
    theme.apply(app)
    w = MainWindow(port=port, autoconnect=port is not None)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

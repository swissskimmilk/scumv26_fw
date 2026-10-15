"""Small shared widgets."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QLabel

from .theme import MUTED as GREY

GREEN, RED, AMBER = "#2bb34a", "#d9363e", "#e0a100"


class Led(QLabel):
    """A round status lamp."""

    def __init__(self, size=12):
        super().__init__()
        self._size = size
        self.setFixedSize(size, size)
        self.set(GREY)

    def set(self, color, tip=None):
        self.setStyleSheet("background:%s; border-radius:%dpx; border:1px solid #0004;"
                           % (color, self._size // 2))
        if tip is not None:
            self.setToolTip(tip)

    def on(self, state):
        self.set(GREEN if state else GREY)


def mono_font(point_size=None):
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    if point_size:
        f.setPointSize(point_size)
    return f


def big_label(text="", point_size=16):
    lbl = QLabel(text)
    f = mono_font(point_size)
    f.setWeight(QFont.Weight.DemiBold)
    lbl.setFont(f)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


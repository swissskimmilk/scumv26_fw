"""One dark theme for the whole window: two surface levels, one border colour, one accent.

Everything takes its colours from here: the Qt palette (so native widgets agree), a small
stylesheet for the frames Fusion draws its own way, and pyqtgraph's defaults (so plots sit
on the same surface as tables and text views instead of on pyqtgraph's black).
"""
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

WINDOW = "#1f2125"   # window, panels, group boxes, tab pages, buttons
BASE = "#17191c"     # anything holding data: inputs, tables, text views, plots
RAISED = "#2a2d32"   # buttons, headers
BORDER = "#34383e"
TEXT = "#d8dbe0"
MUTED = "#8a8f98"
ACCENT = "#4e8cd9"

STYLE = f"""
QMainWindow {{ background: {WINDOW}; }}
QToolBar {{ background: {WINDOW}; border: none; border-bottom: 1px solid {BORDER}; padding: 4px; spacing: 6px; }}
QStatusBar {{ background: {WINDOW}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 4px; margin-top: 14px; padding: 8px 6px 6px 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {MUTED}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 4px; top: -1px; }}
QTabBar::tab {{ background: {WINDOW}; color: {MUTED}; border: 1px solid transparent;
               padding: 6px 14px; margin-right: 2px; }}
QTabBar::tab:selected {{ color: {TEXT}; border: 1px solid {BORDER}; border-bottom-color: {WINDOW};
                        border-top: 2px solid {ACCENT}; }}
QTabBar::tab:hover:!selected:enabled {{ color: {TEXT}; }}
QTabBar::tab:disabled {{ color: #4d525a; }}
QPushButton {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 3px; padding: 4px 10px; }}
QPushButton:hover {{ border-color: {MUTED}; }}
QPushButton:pressed {{ background: {BASE}; }}
QPushButton:default {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTableWidget {{
    background: {BASE}; border: 1px solid {BORDER}; border-radius: 3px; selection-background-color: {ACCENT}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ padding: 3px 5px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {BASE}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; }}
QTableWidget {{ gridline-color: {BORDER}; }}
QTableWidget QWidget {{ background: transparent; }}
QTableWidget QSpinBox, QTableWidget QDoubleSpinBox {{ background: {BASE}; border: none; padding: 0 6px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QHeaderView::section {{ background: {RAISED}; color: {MUTED}; border: none;
                        border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER}; padding: 4px 6px; }}
QTableCornerButton::section {{ background: {RAISED}; border: none; }}
QScrollArea {{ border: none; }}
QSplitter::handle {{ background: {WINDOW}; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: {BASE}; border: none; width: 10px; height: 10px; }}
QScrollBar::handle {{ background: {RAISED}; border-radius: 4px; min-height: 24px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER}; border-radius: 3px; background: {BASE}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QLabel {{ background: transparent; }}
"""


def apply(app):
    app.setStyle("Fusion")
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)  # dark native title bar on Windows
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: WINDOW, QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: BASE, QPalette.ColorRole.AlternateBase: WINDOW,
        QPalette.ColorRole.Button: RAISED, QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.ToolTipBase: RAISED,
        QPalette.ColorRole.ToolTipText: TEXT, QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.PlaceholderText: MUTED,
        QPalette.ColorRole.Mid: BORDER, QPalette.ColorRole.Dark: BASE, QPalette.ColorRole.Light: RAISED,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(MUTED))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)
    pg.setConfigOptions(antialias=True, background=BASE, foreground=MUTED)


def style_plot(plot):
    """Grid, axis and legend colours for a pg.PlotWidget, matching the theme."""
    plot.showGrid(x=True, y=True, alpha=0.15)
    for side in ("left", "bottom"):
        ax = plot.getAxis(side)
        ax.setPen(pg.mkPen(BORDER))
        ax.setTextPen(pg.mkPen(MUTED))
    plot.getPlotItem().titleLabel.setAttr("color", MUTED)
    legend = plot.getPlotItem().legend
    if legend is not None:
        legend.setBrush(pg.mkBrush(WINDOW + "e0"))
        legend.setPen(pg.mkPen(BORDER))
        legend.setLabelTextColor(TEXT)

"""
Entry point for the ephys acquisition GUI.

Usage:
    python main.py
"""

import sys
from pathlib import Path

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


ASSETS_DIR = Path(__file__).parent / "assets"


STYLESHEET = """
/* ---------- Base window + panel backgrounds ---------- */
QMainWindow, QWidget {
    background-color: #14161a;
    color: #e7ebf0;
    font-size: 9pt;
}

/* ---------- QGroupBox ---------- */
QGroupBox {
    border: 1px solid #3a414c;
    border-radius: 5px;
    margin-top: 10px;
    padding: 10px 8px 8px 8px;
    background-color: transparent;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    color: #b8bec9;
    background-color: #14161a;
    text-transform: uppercase;
    letter-spacing: 1px;
    font-size: 8pt;
}

/* ---------- Buttons ---------- */
QPushButton {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                      stop:0 #2e343e, stop:1 #262b34);
    border: 1px solid #3a414c;
    border-radius: 3px;
    padding: 5px 12px;
    color: #e7ebf0;
    font-weight: 500;
    min-height: 18px;
}
QPushButton:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                      stop:0 #383e4a, stop:1 #2e343e);
    border-color: #4a525e;
}
QPushButton:pressed {
    background-color: #1f242c;
}
QPushButton:disabled {
    color: #5b626d;
    background-color: #1b1e24;
    border-color: #2e333d;
}

/* Accent (primary) button — used for Start / Run */
QPushButton[accent="primary"] {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                      stop:0 #b9c3d1, stop:1 #a4afc1);
    border: 1px solid #7f8794;
    color: #14161a;
    font-weight: 600;
}
QPushButton[accent="primary"]:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                      stop:0 #c7d0dc, stop:1 #b9c3d1);
}
QPushButton[accent="primary"]:pressed {
    background-color: #8f99a8;
}
QPushButton[accent="primary"]:disabled {
    background-color: #2a2f38;
    color: #5b626d;
    border-color: #2e333d;
}

/* Record button — recording red */
QPushButton[accent="record"] {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                      stop:0 #b63c30, stop:1 #8c2e25);
    border: 1px solid #5a1e18;
    color: #ffffff;
    font-weight: 600;
}
QPushButton[accent="record"]:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                      stop:0 #c94a3e, stop:1 #9a362a);
}
QPushButton[accent="record"]:disabled {
    background-color: #2a2f38;
    color: #5b626d;
    border-color: #2e333d;
}

/* Ghost button — transparent, subtle hover */
QPushButton[accent="ghost"] {
    background-color: transparent;
    border: 1px solid transparent;
    color: #b8bec9;
}
QPushButton[accent="ghost"]:hover {
    background-color: #22262e;
    border-color: #3a414c;
    color: #e7ebf0;
}

/* Pill-toggle button (used inside ModePill / ClampPill) */
QPushButton[pill="true"] {
    background-color: transparent;
    border: none;
    border-radius: 999px;
    padding: 3px 12px;
    color: #b8bec9;
    font-weight: 500;
    min-height: 16px;
}
QPushButton[pill="true"]:hover {
    color: #e7ebf0;
}
QPushButton[pill="true"]:checked {
    background-color: #a4afc1;
    color: #14161a;
    font-weight: 600;
}

/* Sidebar button */
QToolButton[sidebar="true"] {
    background-color: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0;
    padding: 4px 2px;
    color: #7f8794;
    font-size: 8pt;
    font-weight: 500;
}
QToolButton[sidebar="true"]:hover {
    background-color: #1b1e24;
    color: #b8bec9;
}
QToolButton[sidebar="true"]:checked {
    background-color: rgba(164, 175, 193, 0.18);
    color: #a4afc1;
    border-left-color: #a4afc1;
}

/* ---------- Inputs ---------- */
QLineEdit, QPlainTextEdit, QTextEdit,
QDoubleSpinBox, QSpinBox, QComboBox {
    background-color: #2a2f38;
    border: 1px solid #3a414c;
    border-radius: 3px;
    padding: 4px 6px;
    color: #e7ebf0;
    selection-background-color: rgba(164, 175, 193, 0.35);
    selection-color: #ffffff;
}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover,
QDoubleSpinBox:hover, QSpinBox:hover, QComboBox:hover {
    border-color: #4a525e;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #a4afc1;
    background-color: #262b34;
}
QLineEdit:read-only {
    background-color: #22262e;
    color: #b8bec9;
}
QLineEdit[placeholderText], QPlainTextEdit[placeholderText] {
    color: #e7ebf0;
}

/* Spin buttons inside QDoubleSpinBox / QSpinBox */
QDoubleSpinBox::up-button, QSpinBox::up-button,
QDoubleSpinBox::down-button, QSpinBox::down-button {
    background-color: transparent;
    border: none;
    width: 16px;
}
QDoubleSpinBox::up-button:hover, QSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover, QSpinBox::down-button:hover {
    background-color: #3a414c;
}

/* ---------- Labels ---------- */
QLabel { color: #e7ebf0; background: transparent; }
QLabel[tier="muted"]    { color: #7f8794; }
QLabel[tier="secondary"]{ color: #b8bec9; }
QLabel[mono="true"]     { font-family: "JetBrains Mono", "Consolas", monospace; }
QLabel[tier="tiny"]     { color: #7f8794; font-size: 8pt; letter-spacing: 1px; }

/* ---------- Radio / Checkbox ---------- */
QRadioButton, QCheckBox { color: #e7ebf0; spacing: 7px; }
QRadioButton::indicator, QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #4a525e;
    background-color: #2a2f38;
}
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator    { border-radius: 2px; }
QRadioButton::indicator:hover, QCheckBox::indicator:hover { border-color: #a4afc1; }
QRadioButton::indicator:checked {
    border: 1px solid #a4afc1;
    background-color: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                                      stop:0.0 #a4afc1, stop:0.38 #a4afc1,
                                      stop:0.42 #2a2f38, stop:1.0 #2a2f38);
}
QCheckBox::indicator:checked {
    background-color: #a4afc1;
    border-color: #7f8794;
    image: none;
}

/* ---------- ComboBox ---------- */
QComboBox {
    padding: 4px 26px 4px 6px;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 22px;
    border: none;
    background: transparent;
    padding-right: 6px;
}
QComboBox::down-arrow {
    image: url("{CHEVRON_DOWN}");
    width: 10px;
    height: 6px;
}
QComboBox QAbstractItemView {
    background-color: #1b1e24;
    border: 1px solid #3a414c;
    color: #e7ebf0;
    selection-background-color: rgba(164, 175, 193, 0.25);
    selection-color: #e7ebf0;
    outline: none;
    padding: 2px;
}

/* Icon-only compact buttons (e.g. refresh) */
QPushButton[icon="true"] {
    padding: 2px 0;
    font-size: 12pt;
    font-weight: 500;
    min-height: 0;
}

/* ---------- Splitter ---------- */
QSplitter::handle {
    background-color: #3a414c;
}
QSplitter::handle:horizontal { width: 4px; }
QSplitter::handle:vertical   { height: 4px; }
QSplitter::handle:hover {
    background-color: #6b7585;
}

/* ---------- Scroll area / scrollbars ---------- */
QScrollArea { border: none; background-color: transparent; }
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2e333d;
    border-radius: 5px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #3f4650; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #2e333d;
    border-radius: 5px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background: #3f4650; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ---------- ToolTip ---------- */
QToolTip {
    background-color: #1b1e24;
    color: #e7ebf0;
    border: 1px solid #3a414c;
    padding: 4px 6px;
}

/* ---------- Menu bar / menus (protocol builder dialog) ---------- */
QMenuBar { background-color: #1b1e24; color: #b8bec9; border-bottom: 1px solid #2e333d; }
QMenuBar::item { background: transparent; padding: 4px 10px; }
QMenuBar::item:selected { background: #2a2f38; color: #e7ebf0; }
QMenu { background-color: #1b1e24; border: 1px solid #3a414c; color: #e7ebf0; padding: 4px; }
QMenu::item { padding: 4px 20px; border-radius: 2px; }
QMenu::item:selected { background-color: rgba(164, 175, 193, 0.18); }

/* ---------- Frames used as visual containers (cards / badges) ---------- */
QFrame[card="true"] {
    background-color: #1b1e24;
    border: 1px solid #3a414c;
    border-radius: 5px;
}
QFrame[pillbox="true"] {
    background-color: #2a2f38;
    border: 1px solid #3a414c;
    border-radius: 999px;
    padding: 2px;
}
QFrame[chrome="true"] {
    background-color: #1b1e24;
    border-bottom: 1px solid #2e333d;
}
QFrame[sidebar="true"] {
    background-color: #1b1e24;
    border-right: 1px solid #2e333d;
}
QFrame[led="idle"]  { background-color: #4a525e; border-radius: 5px; }
QFrame[led="ok"]    { background-color: #4cc28e; border-radius: 5px; }
QFrame[led="warn"]  { background-color: #e0a544; border-radius: 5px; }
QFrame[led="err"]   { background-color: #e05e3e; border-radius: 5px; }
QFrame[led="rec"]   { background-color: #e05e3e; border-radius: 5px; }

/* Keyboard shortcut chip */
QLabel[kbd="true"] {
    background-color: #14161a;
    border: 1px solid #3a414c;
    border-radius: 3px;
    padding: 0px 5px;
    color: #7f8794;
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-size: 8pt;
}
"""


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Ephys Acquisition")
    app.setOrganizationName("Lab")
    app.setWindowIcon(QIcon("assets/icon.ico"))

    default_font = QFont("Segoe UI", 10)
    app.setFont(default_font)

    chevron_path = (ASSETS_DIR / "chevron-down.svg").as_posix()
    app.setStyleSheet(STYLESHEET.replace("{CHEVRON_DOWN}", chevron_path))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

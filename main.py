"""
Entry point for the ephys acquisition GUI.

Usage:
    python main.py
"""

import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Ephys Acquisition")
    app.setOrganizationName("Lab")
    app.setWindowIcon(QIcon("assets/icon.ico"))

    # Ensure a valid default font point size (Windows can return -1 from system font)
    default_font = QFont("Segoe UI", 10)
    app.setFont(default_font)

    # Dark stylesheet — keep it readable, not over-styled
    app.setStyleSheet("""
        QMainWindow, QWidget {
            background-color: #1a1a2e;
            color: #e0e0e0;
            font-size: 9pt;
        }
        QGroupBox {
            border: 1px solid #444466;
            border-radius: 4px;
            margin-top: 6px;
            padding-top: 4px;
            font-weight: bold;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            color: #aaaaff;
        }
        QPushButton {
            background-color: #2a2a4a;
            border: 1px solid #555577;
            border-radius: 3px;
            padding: 4px 10px;
            color: #e0e0e0;
        }
        QPushButton:hover  { background-color: #3a3a6a; }
        QPushButton:pressed { background-color: #1a1a3a; }
        QPushButton:disabled { color: #666688; border-color: #333355; }
        QDoubleSpinBox, QSpinBox, QLineEdit {
            background-color: #121225;
            border: 1px solid #444466;
            border-radius: 2px;
            padding: 2px 4px;
            color: #e0e0e0;
        }
        QLabel { color: #ccccdd; }
        QRadioButton { color: #ccccdd; }
        QRadioButton::indicator {
            width: 13px;
            height: 13px;
            border: 1px solid #8888bb;
            border-radius: 7px;
            background-color: #121225;
        }
        QRadioButton::indicator:checked {
            background-color: #aaaaff;
            border-color: #aaaaff;
        }
        QCheckBox { color: #ccccdd; spacing: 6px; }
        QCheckBox::indicator {
            width: 13px;
            height: 13px;
            border: 1px solid #8888bb;
            border-radius: 2px;
            background-color: #121225;
        }
        QCheckBox::indicator:checked {
            background-color: #5555cc;
            border-color: #aaaaff;
        }
        QComboBox {
            background-color: #121225;
            border: 1px solid #444466;
            border-radius: 2px;
            padding: 2px 6px;
            color: #e0e0e0;
        }
        QComboBox:hover { border-color: #8888bb; }
        QComboBox QAbstractItemView {
            background-color: #1e1e38;
            border: 1px solid #555577;
            color: #e0e0e0;
            selection-background-color: #3a3a6a;
            selection-color: #ffffff;
            outline: none;
        }
        QSplitter::handle { background-color: #333355; }
        QTabWidget::pane {
            border: 1px solid #444466;
            background-color: #1a1a2e;
        }
        QTabBar::tab {
            background-color: #2a2a4a;
            border: 1px solid #444466;
            border-bottom: none;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
            padding: 6px 16px;
            color: #ccccdd;
        }
        QTabBar::tab:selected {
            background-color: #1a1a2e;
            color: #aaaaff;
            font-weight: bold;
        }
        QTabBar::tab:hover:!selected {
            background-color: #3a3a6a;
        }
        QScrollArea { border: none; background-color: transparent; }
    """)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

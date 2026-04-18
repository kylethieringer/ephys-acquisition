"""
Reusable visual widgets for the redesigned GUI.

- :class:`PillToggle`: two-option exclusive pill selector (used for Mode and Clamp).
- :class:`StatusBadge`: colored LED dot + status text, driven by a state key.
- :class:`TopChromeBar`: title bar above the sidebar/workspace showing the
  current Session ID, Mode/Clamp pills, and the StatusBadge.
- :class:`Sidebar`: vertical icon rail that drives a :class:`QStackedWidget`.

These widgets are pure view — they emit Qt signals but hold no references to
acquisition back-ends.  :class:`~ui.main_window.MainWindow` wires them up.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


# ----------------------------------------------------------------------
# Icons — tiny inline SVG-style paths rendered to QPixmap
# ----------------------------------------------------------------------

def _make_icon(draw_fn, size: int = 18, color: str = "#b8bec9") -> QIcon:
    """Build a QIcon by painting into a transparent pixmap.

    Args:
        draw_fn: Callable ``(painter, size, pen)`` that draws the glyph.
        size: Pixmap edge length in pixels.
        color: Stroke color as a hex string.
    """
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    draw_fn(p, size, pen)
    p.end()
    return QIcon(px)


def _ic_wave(p: QPainter, s: int, _pen: QPen) -> None:
    from PySide6.QtGui import QPainterPath
    path = QPainterPath()
    path.moveTo(s * 0.12, s * 0.55)
    path.quadTo(s * 0.28, s * 0.15, s * 0.44, s * 0.55)
    path.quadTo(s * 0.60, s * 0.95, s * 0.76, s * 0.55)
    path.quadTo(s * 0.88, s * 0.25, s * 0.95, s * 0.55)
    p.drawPath(path)


def _ic_flask(p: QPainter, s: int, _pen: QPen) -> None:
    from PySide6.QtGui import QPainterPath
    path = QPainterPath()
    path.moveTo(s * 0.40, s * 0.15)
    path.lineTo(s * 0.40, s * 0.40)
    path.lineTo(s * 0.18, s * 0.80)
    path.quadTo(s * 0.14, s * 0.90, s * 0.25, s * 0.90)
    path.lineTo(s * 0.75, s * 0.90)
    path.quadTo(s * 0.86, s * 0.90, s * 0.82, s * 0.80)
    path.lineTo(s * 0.60, s * 0.40)
    path.lineTo(s * 0.60, s * 0.15)
    p.drawPath(path)
    p.drawLine(int(s * 0.34), int(s * 0.15), int(s * 0.66), int(s * 0.15))


def _ic_camera(p: QPainter, s: int, _pen: QPen) -> None:
    r = int(s * 0.16)
    p.drawRoundedRect(int(s * 0.15), int(s * 0.28), int(s * 0.70), int(s * 0.55), 3, 3)
    p.drawLine(int(s * 0.38), int(s * 0.28), int(s * 0.43), int(s * 0.18))
    p.drawLine(int(s * 0.43), int(s * 0.18), int(s * 0.57), int(s * 0.18))
    p.drawLine(int(s * 0.57), int(s * 0.18), int(s * 0.62), int(s * 0.28))
    p.drawEllipse(int(s * 0.5 - r / 2), int(s * 0.56 - r / 2), r, r)


def _ic_dot(p: QPainter, s: int, _pen: QPen) -> None:
    p.setBrush(QColor("#b8bec9"))
    p.drawEllipse(int(s * 0.36), int(s * 0.36), int(s * 0.28), int(s * 0.28))


def _ic_gear(p: QPainter, s: int, _pen: QPen) -> None:
    cx = cy = s / 2
    p.drawEllipse(int(cx - s * 0.18), int(cy - s * 0.18), int(s * 0.36), int(s * 0.36))
    for angle in range(0, 360, 45):
        import math
        a = math.radians(angle)
        x1 = cx + math.cos(a) * s * 0.28
        y1 = cy + math.sin(a) * s * 0.28
        x2 = cx + math.cos(a) * s * 0.42
        y2 = cy + math.sin(a) * s * 0.42
        p.drawLine(int(x1), int(y1), int(x2), int(y2))


ICONS = {
    "wave":   lambda: _make_icon(_ic_wave),
    "flask":  lambda: _make_icon(_ic_flask),
    "camera": lambda: _make_icon(_ic_camera),
    "dot":    lambda: _make_icon(_ic_dot),
    "gear":   lambda: _make_icon(_ic_gear),
}


# ----------------------------------------------------------------------
# PillToggle
# ----------------------------------------------------------------------

class PillToggle(QFrame):
    """Two-option exclusive pill selector, styled to match the design's pills.

    Signals:
        changed(str): Emitted when the selection changes; argument is the
            value of the newly-selected option.
    """

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], parent=None) -> None:
        """
        Args:
            options: List of ``(value, label)`` tuples. Exactly two recommended
                to match the pill visual; more are technically allowed.
            parent: Qt parent.
        """
        super().__init__(parent)
        self.setProperty("pillbox", True)
        self.setFrameShape(QFrame.NoFrame)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for value, label in options:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("pill", True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            btn.clicked.connect(lambda _=False, v=value: self._on_selected(v))
            layout.addWidget(btn)
            self._group.addButton(btn)
            self._buttons[value] = btn

        # Default: first option
        if options:
            self._buttons[options[0][0]].setChecked(True)

    def set_value(self, value: str) -> None:
        """Programmatically select an option without emitting ``changed``."""
        btn = self._buttons.get(value)
        if btn and not btn.isChecked():
            btn.blockSignals(True)
            btn.setChecked(True)
            btn.blockSignals(False)

    def value(self) -> str:
        for v, btn in self._buttons.items():
            if btn.isChecked():
                return v
        return ""

    def _on_selected(self, value: str) -> None:
        self.changed.emit(value)


# ----------------------------------------------------------------------
# StatusBadge
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class _StateStyle:
    led: str
    label: str
    bg: str
    border: str
    fg: str


_STATE_STYLES: dict[str, _StateStyle] = {
    "idle":      _StateStyle("idle", "Ready",             "#22262e", "#3a414c", "#e7ebf0"),
    "acquiring": _StateStyle("ok",   "Acquiring",         "#22262e", "#3a414c", "#e7ebf0"),
    "recording": _StateStyle("rec",  "Recording",         "#2c1d1a", "#e05e3e", "#e05e3e"),
    "protocol":  _StateStyle("rec",  "Protocol running",  "#2c1d1a", "#e05e3e", "#e05e3e"),
    "error":     _StateStyle("err",  "Error",             "#2c1d1a", "#e05e3e", "#e05e3e"),
    "stopping":  _StateStyle("warn", "Stopping…",         "#2a241a", "#e0a544", "#e0a544"),
}


class StatusBadge(QFrame):
    """LED dot + status text rolled into a single rounded pill.

    Use :meth:`set_state` with a state key (``"idle"``, ``"acquiring"``,
    ``"recording"``, ``"protocol"``, ``"error"``, ``"stopping"``) to update
    the color scheme; :meth:`set_text` overrides the label for that state.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self._state = "idle"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 10, 3)
        layout.setSpacing(7)

        self._led = QFrame()
        self._led.setFixedSize(QSize(10, 10))
        self._led.setProperty("led", "idle")
        layout.addWidget(self._led)

        self._label = QLabel("Ready")
        self._label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self._label)

        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self._apply()

    def set_state(self, state: str) -> None:
        """Set the state key and update colors to match.

        Args:
            state: One of the keys in ``_STATE_STYLES``.  Unknown states
                default to ``"idle"``.
        """
        if state not in _STATE_STYLES:
            state = "idle"
        self._state = state
        self._label.setText(_STATE_STYLES[state].label)
        self._apply()

    def set_text(self, text: str) -> None:
        """Override the visible label text for the current state."""
        self._label.setText(text)

    def _apply(self) -> None:
        s = _STATE_STYLES[self._state]
        self._led.setProperty("led", s.led)
        self._led.style().unpolish(self._led)
        self._led.style().polish(self._led)
        self._label.setStyleSheet(f"font-weight: 600; color: {s.fg};")
        self.setStyleSheet(
            f"QFrame {{ background-color: {s.bg}; border: 1px solid {s.border};"
            f" border-radius: 4px; }}"
        )


# ----------------------------------------------------------------------
# TopChromeBar
# ----------------------------------------------------------------------

class TopChromeBar(QFrame):
    """Top chrome row: session label, mode/clamp pills, status badge.

    Widgets are exposed as attributes so :class:`~ui.main_window.MainWindow`
    can wire them to control-panel signals:

    Attributes:
        mode_pill: Two-option pill (``"continuous"`` / ``"trial"``).
        clamp_pill: Two-option pill (``"current_clamp"`` / ``"voltage_clamp"``).
        status_badge: :class:`StatusBadge` on the right.
        session_label: Monospace :class:`QLabel` showing the current experiment ID.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("chrome", True)
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(42)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 4, 14, 4)
        layout.setSpacing(10)

        # --- Session label ---
        session_tag = QLabel("SESSION")
        session_tag.setProperty("tier", "tiny")
        self.session_label = QLabel("—")
        self.session_label.setProperty("mono", True)
        self.session_label.setStyleSheet(
            "font-weight: 600; color: #e7ebf0; font-size: 10pt;"
            " font-family: 'JetBrains Mono', 'Consolas', monospace;"
        )
        layout.addWidget(session_tag)
        layout.addWidget(self.session_label)

        layout.addStretch(1)

        # --- Mode pill ---
        self.mode_pill = PillToggle(
            [("continuous", "Continuous"), ("trial", "Trial")]
        )
        layout.addWidget(self.mode_pill)

        # --- Clamp pill ---
        self.clamp_pill = PillToggle(
            [("current_clamp", "CC"), ("voltage_clamp", "VC")]
        )
        layout.addWidget(self.clamp_pill)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #3a414c;")
        sep.setFixedHeight(20)
        layout.addWidget(sep)

        # --- Status badge ---
        self.status_badge = StatusBadge()
        layout.addWidget(self.status_badge)

    def set_session_label(self, text: str) -> None:
        self.session_label.setText(text or "—")


# ----------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------

class Sidebar(QFrame):
    """Vertical icon rail that drives a QStackedWidget.

    Signals:
        page_changed(str): Emitted when a sidebar item is selected; argument
            is the item ``key``.
    """

    page_changed = Signal(str)

    def __init__(self, items: list[tuple[str, str, str]], parent=None) -> None:
        """
        Args:
            items: List of ``(key, icon_name, label)`` tuples. ``icon_name``
                is a key into :data:`ICONS`.
        """
        super().__init__(parent)
        self.setProperty("sidebar", True)
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedWidth(84)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(2)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QToolButton] = {}

        for key, icon_name, label in items:
            btn = QToolButton()
            btn.setText(label)
            btn.setCheckable(True)
            btn.setProperty("sidebar", True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIcon(ICONS.get(icon_name, ICONS["dot"])())
            btn.setIconSize(QSize(20, 20))
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.setFixedHeight(58)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.clicked.connect(lambda _=False, k=key: self._on_clicked(k))
            layout.addWidget(btn)
            self._group.addButton(btn)
            self._buttons[key] = btn

        layout.addStretch(1)

        # Default: first item
        if items:
            self._buttons[items[0][0]].setChecked(True)

    def set_current(self, key: str) -> None:
        btn = self._buttons.get(key)
        if btn:
            btn.setChecked(True)

    def _on_clicked(self, key: str) -> None:
        self.page_changed.emit(key)

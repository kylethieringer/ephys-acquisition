"""
LiveTracePanel — scrolling real-time display of all 5 AI channels.

Layout (inside a QWidget):
    Vertical QSplitter with one pg.PlotWidget per channel.
    X axes are linked so they always scroll together.
    ScAmpOut (ai0) starts at 2× the height of other channels.
    Users can drag splitter handles to resize individual channels.

The panel pulls data from a RingBuffer on a 33 ms QTimer (≈30 Hz).
Raw voltages are converted to display units using each channel's scale factor.
"""

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from config import (
    AI_CHANNELS,
    AI_CHANNELS_VC,
    AI_Y_DEFAULTS,
    AI_Y_DEFAULTS_VC,
    DISPLAY_SAMPLES,
    DISPLAY_SECONDS,
    SAMPLE_RATE,
    TRACE_COLORS,
)

pg.setConfigOptions(antialias=False)

REFRESH_INTERVAL_MS = 33   # ~30 Hz
DOWNSAMPLE_FACTOR   = 4    # display every 4th point (5 kHz → plenty for display)


class ChannelYControls(QWidget):
    """Compact Y-range controls for a single channel."""

    def __init__(self, channel_index: int, plot_item: pg.PlotItem, parent=None):
        super().__init__(parent)
        self._plot = plot_item
        name, _, _, _, units = AI_CHANNELS[channel_index]
        y_min, y_max = AI_Y_DEFAULTS[channel_index]

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)

        self._lbl = QLabel(f"{name} ({units})")
        self._lbl.setFixedWidth(130)
        self._lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._lbl)

        self._min_spin = QDoubleSpinBox()
        self._min_spin.setRange(-1e6, 1e6)
        self._min_spin.setDecimals(1)
        self._min_spin.setValue(y_min)
        self._min_spin.setSuffix(f" {units}")
        self._min_spin.setFixedWidth(100)
        self._min_spin.valueChanged.connect(self._apply_range)
        layout.addWidget(self._min_spin)

        self._max_spin = QDoubleSpinBox()
        self._max_spin.setRange(-1e6, 1e6)
        self._max_spin.setDecimals(1)
        self._max_spin.setValue(y_max)
        self._max_spin.setSuffix(f" {units}")
        self._max_spin.setFixedWidth(100)
        self._max_spin.valueChanged.connect(self._apply_range)
        layout.addWidget(self._max_spin)

        self._auto_cb = QCheckBox("Auto")
        self._auto_cb.setChecked(False)
        self._auto_cb.toggled.connect(self._on_auto_toggled)
        layout.addWidget(self._auto_cb)

        self._apply_range()

    def update_channel(self, name: str, units: str, y_min: float, y_max: float) -> None:
        """Update labels and Y-range defaults when the clamp mode changes.

        Args:
            name: New channel display name.
            units: New unit string (used for label and spinbox suffix).
            y_min: New default Y-axis minimum in display units.
            y_max: New default Y-axis maximum in display units.
        """
        self._lbl.setText(f"{name} ({units})")
        self._min_spin.setSuffix(f" {units}")
        self._max_spin.setSuffix(f" {units}")
        self._min_spin.setValue(y_min)
        self._max_spin.setValue(y_max)
        self._apply_range()

    def _apply_range(self) -> None:
        if not self._auto_cb.isChecked():
            self._plot.setYRange(self._min_spin.value(), self._max_spin.value(), padding=0)

    def _on_auto_toggled(self, checked: bool) -> None:
        self._plot.enableAutoRange(axis="y", enable=checked)
        self._min_spin.setEnabled(not checked)
        self._max_spin.setEnabled(not checked)
        if not checked:
            self._apply_range()


class LiveTracePanel(QWidget):
    """
    Main live-trace display panel.

    Call set_ring_buffer(buf) after construction to connect data source.
    The QTimer starts automatically; it is paused when no buffer is set.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ring_buffer  = None
        self._channel_defs = list(AI_CHANNELS)
        self._y_defaults   = list(AI_Y_DEFAULTS)

        n_ch = len(self._channel_defs)
        t_axis = np.linspace(-DISPLAY_SECONDS, 0, DISPLAY_SAMPLES // DOWNSAMPLE_FACTOR)
        self._t_axis = t_axis

        self._plot_widgets: list[pg.PlotWidget] = []
        self._plots:        list[pg.PlotItem]   = []
        self._curves:       list[pg.PlotDataItem] = []

        # Vertical splitter — one PlotWidget per channel
        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.setChildrenCollapsible(False)

        for i, (name, _, _, scale, units) in enumerate(self._channel_defs):
            pw = pg.PlotWidget(background="#1a1a2e")
            plot = pw.plotItem

            plot.setLabel("left", f"{name} ({units})", color=TRACE_COLORS[i])
            plot.setLabel("bottom", "Time (s)" if i == n_ch - 1 else "")
            plot.showAxis("bottom", i == n_ch - 1)
            plot.setMenuEnabled(False)
            plot.setXRange(-DISPLAY_SECONDS, 0, padding=0)

            y_min, y_max = self._y_defaults[i]
            plot.setYRange(y_min, y_max, padding=0)
            plot.enableAutoRange(axis="y", enable=False)

            # Link X axis to the first plot
            if i > 0:
                plot.getViewBox().setXLink(self._plots[0].getViewBox())

            curve = plot.plot(
                x=t_axis,
                y=np.zeros_like(t_axis),
                pen=pg.mkPen(color=TRACE_COLORS[i], width=1),
            )

            self._plot_widgets.append(pw)
            self._plots.append(plot)
            self._curves.append(curve)
            self._splitter.addWidget(pw)

        # ScAmpOut (index 0) gets 2× height; all others get 1×
        unit = 100
        sizes = [unit * 2 if i == 0 else unit for i in range(n_ch)]
        self._splitter.setSizes(sizes)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._splitter)

        # Refresh timer
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def plots(self) -> list[pg.PlotItem]:
        return self._plots

    @property
    def curves(self) -> list[pg.PlotDataItem]:
        return self._curves

    @property
    def plot_widgets(self) -> list[pg.PlotWidget]:
        return self._plot_widgets

    def set_ring_buffer(self, buf) -> None:
        self._ring_buffer = buf

    def set_clamp_mode(self, mode: str) -> None:
        """Switch channel definitions and Y-range defaults for CC or VC mode.

        Updates axis labels, scale factors (used by the refresh loop), and
        Y-axis ranges on all plots.

        Args:
            mode: ``"current_clamp"`` or ``"voltage_clamp"``.
        """
        self._channel_defs = list(AI_CHANNELS_VC if mode == "voltage_clamp" else AI_CHANNELS)
        self._y_defaults   = list(AI_Y_DEFAULTS_VC if mode == "voltage_clamp" else AI_Y_DEFAULTS)
        for i, (name, _, _, _, units) in enumerate(self._channel_defs):
            self._plots[i].setLabel("left", f"{name} ({units})", color=TRACE_COLORS[i])
            y_min, y_max = self._y_defaults[i]
            self._plots[i].setYRange(y_min, y_max, padding=0)

    # ------------------------------------------------------------------
    # Internal refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        if self._ring_buffer is None:
            return

        data = self._ring_buffer.read_contiguous(DISPLAY_SAMPLES)  # (N_AI, DISPLAY_SAMPLES)
        ds   = DOWNSAMPLE_FACTOR

        for i, (_, _, _, scale, _) in enumerate(self._channel_defs):
            raw  = data[i, ::ds]
            disp = raw * scale
            self._curves[i].setData(self._t_axis, disp)

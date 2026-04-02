"""
StimulusPanel — quick staircase current-injection stimulus builder and preview.

This panel is for **continuous mode** ad-hoc stimulation.  For structured,
repeatable stimulation use the Protocol Builder and trial-based mode instead.

Parameters (all in pA / ms):
    min_pA, max_pA, step_pA  — amplitude range and step size
    step_width_ms             — duration each current step is held
    gap_ms                    — silent gap between steps (ao0 = 0 V)
    repeats                   — number of times to tile the staircase

Preview:
    All steps overlaid on a single plot, each drawn in a distinct colour,
    starting at t = 0 so the user can compare relative amplitudes and timing.

Apply (Stimulate button):
    Emits ``stimulus_applied`` with a 1-D Volts waveform (ao0 only).
    The waveform is automatically cleared after one pass via a QTimer.

Developer notes
---------------
The panel talks to the acquisition layer only via signals — it has no direct
reference to :class:`~acquisition.continuous_mode.ContinuousAcquisition`.
:data:`~config.SAMPLE_RATE` is imported to compute waveform duration for the
auto-clear timer.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
import pyqtgraph as pg
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from config import AO_MV_PER_VOLT, AO_PA_PER_VOLT, SAMPLE_RATE
from utils.stimulus_generator import (
    generate_ao0_waveform,
    generate_preview_steps,
    get_step_amplitudes,
)

# Qualitative color palette for step traces (cycles if > 10 steps)
_STEP_COLORS: list[str] = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
    "#ff7f00", "#ffff33", "#a65628", "#f781bf",
    "#999999", "#66c2a5",
]


class StimulusPanel(QWidget):
    """Panel for defining a staircase stimulus and applying it in continuous mode.

    Signals:
        stimulus_applied(object): Emitted when the user clicks "Stimulate".
            Argument is a 1-D float64 ``numpy.ndarray`` of ao0 voltages in V.
        stimulus_cleared(): Emitted when the user clicks "Clear" or when the
            auto-clear timer fires after a single waveform pass.

    Attributes:
        _auto_clear_timer (QTimer): Single-shot timer that emits
            ``stimulus_cleared`` after the waveform duration has elapsed.
    """

    stimulus_applied = Signal(object)   # 1-D float64 ndarray in Volts
    stimulus_cleared = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._clamp_mode = "current_clamp"
        self._auto_clear_timer = QTimer(self)
        self._auto_clear_timer.setSingleShot(True)
        self._auto_clear_timer.timeout.connect(self._on_auto_clear)

        self._build_ui()

    def set_clamp_mode(self, mode: str) -> None:
        """Switch spinbox labels and ranges between CC (pA) and VC (mV).

        Args:
            mode: ``"current_clamp"`` or ``"voltage_clamp"``.
        """
        self._clamp_mode = mode
        if mode == "voltage_clamp":
            suffix, lo, hi = " mV", -200.0, 200.0
            self._preview_plot.setLabel("left", "Voltage (mV)")
        else:
            suffix, lo, hi = " pA", -5000.0, 5000.0
            self._preview_plot.setLabel("left", "Current (pA)")

        for spin in (self._min_spin, self._max_spin, self._step_spin):
            spin.setSuffix(suffix)
            spin.setRange(lo, hi)
        self._update_step_count()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Build the full panel layout: parameter form, buttons, preview plot."""
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        # --- Parameter form ---
        param_box = QGroupBox("Stimulus Parameters")
        form = QFormLayout(param_box)
        form.setSpacing(4)

        def _dspin(lo, hi, val, decimals=1, suffix=""):
            w = QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setDecimals(decimals)
            w.setValue(val)
            if suffix:
                w.setSuffix(suffix)
            return w

        self._min_spin   = _dspin(-5000,    0,   -100, suffix=" pA")
        self._max_spin   = _dspin(    0, 5000,    100, suffix=" pA")
        self._step_spin  = _dspin(    1, 5000,     50, suffix=" pA")
        self._width_spin = _dspin(    1, 60000,   500, suffix=" ms")
        self._gap_spin   = _dspin(    0, 60000,   200, suffix=" ms")

        self._repeats_spin = QSpinBox()
        self._repeats_spin.setRange(1, 10000)
        self._repeats_spin.setValue(1)
        self._repeats_spin.setSuffix(" ×")

        form.addRow("Min current",    self._min_spin)
        form.addRow("Max current",    self._max_spin)
        form.addRow("Step size",      self._step_spin)
        form.addRow("Step width",     self._width_spin)
        form.addRow("Gap between",    self._gap_spin)
        form.addRow("Repeats",        self._repeats_spin)

        root.addWidget(param_box)

        # --- Step count label ---
        self._step_count_lbl = QLabel()
        root.addWidget(self._step_count_lbl)
        self._update_step_count()
        for sp in (self._min_spin, self._max_spin, self._step_spin,
                   self._width_spin, self._gap_spin):
            sp.valueChanged.connect(self._update_step_count)
        self._repeats_spin.valueChanged.connect(self._update_step_count)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self._preview_btn = QPushButton("Preview")
        self._apply_btn   = QPushButton("Stimulate")
        self._clear_btn   = QPushButton("Clear")
        self._preview_btn.clicked.connect(self._on_preview)
        self._apply_btn.clicked.connect(self._on_apply)
        self._clear_btn.clicked.connect(self._on_clear)
        btn_row.addWidget(self._preview_btn)
        btn_row.addWidget(self._apply_btn)
        btn_row.addWidget(self._clear_btn)
        root.addLayout(btn_row)

        # --- Preview plot ---
        preview_box = QGroupBox("Preview (steps overlaid)")
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(2, 2, 2, 2)

        self._preview_plot = pg.PlotWidget(background="#1a1a2e")
        self._preview_plot.setLabel("left",   "Current (pA)")
        self._preview_plot.setLabel("bottom", "Time (ms)")
        self._preview_plot.setMinimumHeight(160)
        self._preview_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        preview_layout.addWidget(self._preview_plot)
        root.addWidget(preview_box)

        root.addStretch()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _update_step_count(self) -> None:
        """Recompute and display the step count and total duration label."""
        amps = get_step_amplitudes(
            self._min_spin.value(),
            self._max_spin.value(),
            self._step_spin.value(),
        )
        n = len(amps)
        repeats = self._repeats_spin.value()
        single_ms = n * (self._width_spin.value() + self._gap_spin.value())
        total_ms = single_ms * repeats
        repeat_str = f" × {repeats} repeats" if repeats > 1 else ""
        self._step_count_lbl.setText(
            f"{n} step{'s' if n != 1 else ''}{repeat_str} — total duration: {total_ms:.0f} ms"
        )

    def _on_preview(self) -> None:
        """Render the step-overlay preview plot from the current parameters."""
        self._preview_plot.clear()

        t_ms, traces = generate_preview_steps(
            self._min_spin.value(),
            self._max_spin.value(),
            self._step_spin.value(),
            self._width_spin.value(),
            self._gap_spin.value(),
        )

        if not traces:
            return

        for idx, trace in enumerate(traces):
            color = _STEP_COLORS[idx % len(_STEP_COLORS)]
            self._preview_plot.plot(
                x=t_ms,
                y=trace,
                pen=pg.mkPen(color=color, width=1.5),
                name=f"{trace[t_ms < self._width_spin.value()][0]:.0f} pA"
                if len(trace) else "",
            )

        all_vals = np.concatenate(traces)
        pad = max(abs(all_vals.max() - all_vals.min()) * 0.05, 10)
        self._preview_plot.setYRange(all_vals.min() - pad, all_vals.max() + pad, padding=0)

    def _on_apply(self) -> None:
        """Generate the ao0 waveform and emit ``stimulus_applied``.

        Builds the waveform in Volts (using AO_PA_PER_VOLT in CC mode or
        AO_MV_PER_VOLT in VC mode), tiles it by the repeat count, emits
        ``stimulus_applied``, and starts the auto-clear timer so ao0 returns
        to zero after one full pass.
        """
        scale = AO_PA_PER_VOLT if self._clamp_mode == "current_clamp" else AO_MV_PER_VOLT
        ao0 = generate_ao0_waveform(
            self._min_spin.value(),
            self._max_spin.value(),
            self._step_spin.value(),
            self._width_spin.value(),
            self._gap_spin.value(),
            scale_pa_per_v=scale,
        )
        if len(ao0) == 0:
            return

        repeats = self._repeats_spin.value()
        if repeats > 1:
            ao0 = np.tile(ao0, repeats)

        self.stimulus_applied.emit(ao0)

        # Schedule auto-clear after the waveform finishes one pass
        duration_ms = int(len(ao0) / SAMPLE_RATE * 1000)
        self._auto_clear_timer.start(duration_ms)

    def _on_auto_clear(self) -> None:
        """Emit ``stimulus_cleared`` when the auto-clear timer fires."""
        self.stimulus_cleared.emit()

    def _on_clear(self) -> None:
        """Cancel the auto-clear timer and immediately emit ``stimulus_cleared``."""
        self._auto_clear_timer.stop()
        self.stimulus_cleared.emit()

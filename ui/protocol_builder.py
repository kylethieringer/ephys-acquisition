"""
ProtocolBuilderPanel — embeddable widget for composing trial protocols.

The panel is embedded inline on the Protocol sidebar page (see
:class:`~ui.main_window.MainWindow._build_protocol_page`). It manages
an ordered list of
:class:`~acquisition.trial_protocol.StimulusDefinition` objects and the
global timing / amplifier-scaling parameters, and emits
``protocol_run_requested`` when the user clicks **Load Protocol**.

Layout::

    ┌──────────────────────────────────────────────┐
    │  Protocol Name  [___________________]         │
    │  Clamp mode: ○ Current clamp  ○ Voltage clamp │
    ├────────────────────┬─────────────────────────-┤
    │  Stimuli           │  Edit selected stimulus   │
    │  ┌────────────────┐│  (stacked widget)         │
    │  │ step protocol 0││                           │
    │  │ step protocol 1││                           │
    │  └──────────────┘  │                           │
    │  [+Add][−Remove]   │                           │
    │  [↑Up ][↓Down  ]   │                           │
    ├────────────────────┴──────────────────────────┤
    │  Global Timing                                 │
    │  Pre/Post/ITI/Repeats + CC hyperpol / VC scale │
    ├────────────────────────────────────────────────┤
    │  [Save Protocol] [Load Protocol file]          │
    │  Estimated run time: XX min XX sec             │
    │  [       Load Protocol       ]                 │
    └────────────────────────────────────────────────┘

Developer notes
---------------
``_stimuli`` is a Python list of
:class:`~acquisition.trial_protocol.StimulusDefinition` objects kept in
parallel with the ``QListWidget`` rows. The editor widgets operate on
the currently selected row. :meth:`_sync_editor_to_stim` writes the
editor back to ``_stimuli`` before any read so the stored list stays
consistent.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from acquisition.trial_protocol import (
    StimulusDefinition,
    TrialProtocol,
    estimated_total_duration_s,
    load_protocol,
    protocol_from_dict,
    protocol_to_dict,
    save_protocol,
    stimulus_label,
    validate_protocol,
)


class ProtocolBuilderPanel(QWidget):
    """Embeddable protocol composer.

    Signals:
        protocol_run_requested(dict): Emitted when "Load Protocol" is
            clicked. The dict contains the serialised protocol (same
            format as
            :func:`~acquisition.trial_protocol.protocol_to_dict`) plus
            a ``"save_dir"`` key with the current save directory path.
    """

    protocol_run_requested = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._save_dir = "D:/protocols"
        self._stimuli: list[StimulusDefinition] = []

        self._build_ui()
        self._update_clamp_visibility()
        self._update_estimated_time()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_save_dir(self, path: str) -> None:
        """Update the default save directory (used in the emitted dict)."""
        self._save_dir = path

    def get_protocol(self) -> TrialProtocol:
        """Return a :class:`TrialProtocol` built from current UI state."""
        self._sync_editor_to_stim()
        return self._read_protocol()

    def load_protocol_from_file(self, path: str | Path) -> None:
        """Load a protocol JSON file and populate all UI fields."""
        try:
            p = load_protocol(path)
            self._populate_from_protocol(p)
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", f"Could not load protocol:\n{exc}")

    def clear(self) -> None:
        """Reset the panel to an empty Unnamed protocol."""
        self._name_edit.setText("Unnamed protocol")
        self._cc_rb.setChecked(True)
        self._pre_sb.setValue(500.0)
        self._post_sb.setValue(1000.0)
        self._iti_sb.setValue(2000.0)
        self._reps_sb.setValue(5)
        self._hp_amp.setValue(-50.0)
        self._hp_dur.setValue(100.0)
        self._ao_scale_sb.setValue(20.0)
        self._stim_list.clear()
        self._stimuli = []
        self._update_clamp_visibility()
        self._update_estimated_time()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        root.addWidget(self._build_header())

        mid_splitter = QSplitter(Qt.Horizontal)
        mid_splitter.addWidget(self._build_stimulus_list())
        mid_splitter.addWidget(self._build_stimulus_editor())
        mid_splitter.setStretchFactor(0, 1)
        mid_splitter.setStretchFactor(1, 2)
        root.addWidget(mid_splitter, stretch=1)

        root.addWidget(self._build_global_settings())
        root.addWidget(self._build_run_controls())

    def _build_header(self) -> QWidget:
        box = QGroupBox("Protocol")
        layout = QFormLayout(box)
        layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._name_edit = QLineEdit("Unnamed protocol")
        self._name_edit.setProperty("mono", True)
        layout.addRow("Name:", self._name_edit)
        self._name_edit.textChanged.connect(self._update_estimated_time)

        clamp_row = QHBoxLayout()
        self._cc_rb = QRadioButton("Current clamp")
        self._vc_rb = QRadioButton("Voltage clamp")
        self._cc_rb.setChecked(True)
        self._cc_rb.toggled.connect(self._on_clamp_mode_changed)
        clamp_row.addWidget(self._cc_rb)
        clamp_row.addWidget(self._vc_rb)
        clamp_row.addStretch()
        layout.addRow("Clamp mode:", clamp_row)

        return box

    def _build_stimulus_list(self) -> QWidget:
        box = QGroupBox("Stimuli")
        layout = QVBoxLayout(box)

        self._stim_list = QListWidget()
        self._stim_list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self._stim_list, stretch=1)

        btn_row1 = QHBoxLayout()
        self._add_btn = QPushButton("+ Add")
        self._remove_btn = QPushButton("- Remove")
        self._dup_btn = QPushButton("Duplicate")
        self._add_btn.clicked.connect(self._on_add)
        self._remove_btn.clicked.connect(self._on_remove)
        self._dup_btn.clicked.connect(self._on_duplicate)
        btn_row1.addWidget(self._add_btn)
        btn_row1.addWidget(self._remove_btn)
        btn_row1.addWidget(self._dup_btn)
        layout.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        self._up_btn = QPushButton("Up")
        self._down_btn = QPushButton("Down")
        self._up_btn.clicked.connect(self._on_move_up)
        self._down_btn.clicked.connect(self._on_move_down)
        btn_row2.addWidget(self._up_btn)
        btn_row2.addWidget(self._down_btn)
        layout.addLayout(btn_row2)

        return box

    def _build_stimulus_editor(self) -> QWidget:
        box = QGroupBox("Edit Stimulus")
        outer = QVBoxLayout(box)

        top_form = QFormLayout()
        top_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        type_row = QHBoxLayout()
        self._sc_type_rb = QRadioButton("Step protocol (CC)")
        self._rp_type_rb = QRadioButton("Ramp (CC)")
        self._ch_type_rb = QRadioButton("Chirp (CC)")
        self._vc_type_rb = QRadioButton("Voltage step (VC)")
        self._bl_type_rb = QRadioButton("Baseline")
        self._sc_type_rb.setChecked(True)
        for rb in (self._sc_type_rb, self._rp_type_rb, self._ch_type_rb,
                   self._vc_type_rb, self._bl_type_rb):
            rb.toggled.connect(self._on_stim_type_changed)
            type_row.addWidget(rb)
        type_row.addStretch()
        top_form.addRow("Type:", type_row)
        outer.addLayout(top_form)

        self._stim_stack = QStackedWidget()
        self._stim_stack.addWidget(self._build_step_protocol_page())
        self._stim_stack.addWidget(self._build_vstep_page())
        self._stim_stack.addWidget(self._build_baseline_page())
        self._stim_stack.addWidget(self._build_ramp_page())
        self._stim_stack.addWidget(self._build_chirp_page())
        outer.addWidget(self._stim_stack)
        outer.addStretch()

        return box

    def _build_step_protocol_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        def _dbl(vmin, vmax, val, decimals=1, suffix=""):
            sb = QDoubleSpinBox()
            sb.setRange(vmin, vmax)
            sb.setValue(val)
            sb.setDecimals(decimals)
            if suffix:
                sb.setSuffix(f" {suffix}")
            sb.valueChanged.connect(self._update_estimated_time)
            sb.valueChanged.connect(self._on_stim_param_changed)
            return sb

        self._sc_min = _dbl(-2000, 2000, -50.0, 1, "pA")
        self._sc_max = _dbl(-2000, 2000, 50.0, 1, "pA")
        self._sc_step = _dbl(0.1, 2000, 10.0, 1, "pA")
        self._sc_width = _dbl(1, 10000, 500.0, 0, "ms")
        self._sc_gap = _dbl(0, 10000, 100.0, 0, "ms")
        self._sc_reps = QSpinBox()
        self._sc_reps.setRange(1, 100)
        self._sc_reps.setValue(1)
        self._sc_reps.valueChanged.connect(self._update_estimated_time)
        self._sc_reps.valueChanged.connect(self._on_stim_param_changed)

        form.addRow("Min current:", self._sc_min)
        form.addRow("Max current:", self._sc_max)
        form.addRow("Step size:", self._sc_step)
        form.addRow("Step width:", self._sc_width)
        form.addRow("Gap between steps:", self._sc_gap)
        form.addRow("Repeats:", self._sc_reps)
        return w

    def _build_vstep_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        def _dbl(vmin, vmax, val, suffix=""):
            sb = QDoubleSpinBox()
            sb.setRange(vmin, vmax)
            sb.setValue(val)
            sb.setDecimals(1)
            if suffix:
                sb.setSuffix(f" {suffix}")
            sb.valueChanged.connect(self._update_estimated_time)
            sb.valueChanged.connect(self._on_stim_param_changed)
            return sb

        self._vs_step_mv = _dbl(-1000, 1000, -40.0, "mV")
        self._vs_duration = _dbl(1, 10000, 500.0, "ms")

        form.addRow("Step voltage:", self._vs_step_mv)
        form.addRow("Duration:", self._vs_duration)
        return w

    def _build_baseline_page(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        lbl = QLabel(
            "Baseline trial: AO output is 0 V throughout.\n"
            "Duration = pre + post from global timing settings.\n"
            "Camera TTL fires as normal."
        )
        lbl.setWordWrap(True)
        layout.addWidget(lbl)
        layout.addStretch()
        return w

    def _stim_dbl(self, vmin, vmax, val, decimals, suffix) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(vmin, vmax)
        sb.setDecimals(decimals)
        sb.setValue(val)
        sb.setSuffix(f" {suffix}")
        sb.valueChanged.connect(self._update_estimated_time)
        sb.valueChanged.connect(self._on_stim_param_changed)
        return sb

    def _stim_reps(self) -> QSpinBox:
        sb = QSpinBox()
        sb.setRange(1, 100)
        sb.setValue(1)
        sb.valueChanged.connect(self._update_estimated_time)
        sb.valueChanged.connect(self._on_stim_param_changed)
        return sb

    def _stim_combo(self, items: list[tuple[str, str]]) -> QComboBox:
        combo = QComboBox()
        for text, data in items:
            combo.addItem(text, data)
        combo.currentIndexChanged.connect(self._update_estimated_time)
        combo.currentIndexChanged.connect(self._on_stim_param_changed)
        return combo

    def _build_ramp_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._rp_start = self._stim_dbl(-4000, 4000, 0.0, 1, "pA")
        self._rp_min = self._stim_dbl(-4000, 4000, 50.0, 1, "pA")
        self._rp_max = self._stim_dbl(-4000, 4000, 150.0, 1, "pA")
        self._rp_step = self._stim_dbl(0.1, 4000, 50.0, 1, "pA")
        self._rp_width = self._stim_dbl(1, 60000, 1000.0, 0, "ms")
        self._rp_hold = self._stim_dbl(0, 60000, 0.0, 0, "ms")
        self._rp_return = self._stim_combo([("Instant", "instant"), ("Ramp down", "ramp_down")])
        self._rp_gap = self._stim_dbl(0, 60000, 500.0, 0, "ms")
        self._rp_reps = self._stim_reps()

        form.addRow("Start current:", self._rp_start)
        form.addRow("Lowest peak current:", self._rp_min)
        form.addRow("Highest peak current:", self._rp_max)
        form.addRow("Peak step size:", self._rp_step)
        form.addRow("Ramp duration:", self._rp_width)
        form.addRow("Hold at peak:", self._rp_hold)
        form.addRow("Return to start:", self._rp_return)
        form.addRow("Gap between ramps:", self._rp_gap)
        form.addRow("Repeats:", self._rp_reps)
        return w

    def _build_chirp_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._ch_start = self._stim_dbl(0, 10000, 0.5, 2, "Hz")
        self._ch_end = self._stim_dbl(0, 10000, 100.0, 2, "Hz")
        self._ch_dur = self._stim_dbl(1, 60000, 20000.0, 0, "ms")
        self._ch_spacing = self._stim_combo([("Log", "log"), ("Linear", "linear")])
        self._ch_amp = self._stim_dbl(0, 4000, 20.0, 1, "pA")
        self._ch_offset = self._stim_dbl(-4000, 4000, 0.0, 1, "pA")
        self._ch_gap = self._stim_dbl(0, 60000, 500.0, 0, "ms")
        self._ch_reps = self._stim_reps()

        form.addRow("Start frequency:", self._ch_start)
        form.addRow("End frequency:", self._ch_end)
        form.addRow("Sweep duration:", self._ch_dur)
        form.addRow("Sweep spacing:", self._ch_spacing)
        form.addRow("Amplitude (peak):", self._ch_amp)
        form.addRow("DC offset:", self._ch_offset)
        form.addRow("Gap between chirps:", self._ch_gap)
        form.addRow("Repeats:", self._ch_reps)
        return w

    def _build_global_settings(self) -> QWidget:
        box = QGroupBox("Global Timing")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(4, 4, 4, 4)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        def _dbl(vmin, vmax, val, suffix=""):
            sb = QDoubleSpinBox()
            sb.setRange(vmin, vmax)
            sb.setValue(val)
            sb.setDecimals(0)
            if suffix:
                sb.setSuffix(f" {suffix}")
            sb.valueChanged.connect(self._update_estimated_time)
            return sb

        self._pre_sb = _dbl(0, 60000, 500.0, "ms")
        self._post_sb = _dbl(0, 60000, 1000.0, "ms")
        self._iti_sb = _dbl(0, 60000, 2000.0, "ms")
        self._reps_sb = QSpinBox()
        self._reps_sb.setRange(1, 1000)
        self._reps_sb.setValue(5)
        self._reps_sb.valueChanged.connect(self._update_estimated_time)

        form.addRow("Pre-baseline:", self._pre_sb)
        form.addRow("Post-stimulus:", self._post_sb)
        form.addRow("Inter-trial interval (ITI):", self._iti_sb)
        form.addRow("Repeats / stimulus:", self._reps_sb)

        self._hyperpol_group = QGroupBox("Hyperpolarisation Pulse (CC only)")
        hp_form = QFormLayout(self._hyperpol_group)
        self._hp_amp = QDoubleSpinBox()
        self._hp_amp.setRange(-2000, 0)
        self._hp_amp.setValue(-50.0)
        self._hp_amp.setDecimals(1)
        self._hp_amp.setSuffix(" pA")
        self._hp_amp.valueChanged.connect(self._update_estimated_time)
        self._hp_dur = QDoubleSpinBox()
        self._hp_dur.setRange(1, 10000)
        self._hp_dur.setValue(100.0)
        self._hp_dur.setDecimals(0)
        self._hp_dur.setSuffix(" ms")
        self._hp_dur.valueChanged.connect(self._update_estimated_time)
        hp_form.addRow("Amplitude:", self._hp_amp)
        hp_form.addRow("Duration:", self._hp_dur)

        self._ao_scale_group = QGroupBox("Voltage Clamp AO Scale")
        ao_form = QFormLayout(self._ao_scale_group)
        self._ao_scale_sb = QDoubleSpinBox()
        self._ao_scale_sb.setRange(0.1, 1000)
        self._ao_scale_sb.setValue(20.0)
        self._ao_scale_sb.setDecimals(1)
        self._ao_scale_sb.setSuffix(" mV/V")
        ao_form.addRow("AO sensitivity:", self._ao_scale_sb)

        outer.addLayout(form)
        outer.addWidget(self._hyperpol_group)
        outer.addWidget(self._ao_scale_group)

        return box

    def _build_run_controls(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)

        btn_row = QHBoxLayout()
        self._save_btn = QPushButton("Save Protocol…")
        self._load_btn = QPushButton("Load from file…")
        self._save_btn.clicked.connect(self._on_save)
        self._load_btn.clicked.connect(self._on_load)
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._load_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._time_lbl = QLabel("Estimated run time: —")
        layout.addWidget(self._time_lbl)

        self._run_btn = QPushButton("Load Protocol")
        self._run_btn.setFixedHeight(34)
        self._run_btn.setProperty("accent", "primary")
        self._run_btn.clicked.connect(self._on_run)
        layout.addWidget(self._run_btn)

        return w

    # ------------------------------------------------------------------
    # Clamp mode visibility
    # ------------------------------------------------------------------

    def _update_clamp_visibility(self) -> None:
        cc = self._cc_rb.isChecked()
        self._hyperpol_group.setVisible(cc)
        self._ao_scale_group.setVisible(not cc)

    def _on_clamp_mode_changed(self) -> None:
        self._update_clamp_visibility()
        self._update_estimated_time()

    # ------------------------------------------------------------------
    # Stimulus list management
    # ------------------------------------------------------------------

    def _on_add(self) -> None:
        stim = StimulusDefinition(type="step_protocol")
        self._stimuli.append(stim)
        self._stim_list.addItem(QListWidgetItem())
        self._refresh_list_labels()
        self._stim_list.setCurrentRow(len(self._stimuli) - 1)
        self._update_estimated_time()

    def _on_remove(self) -> None:
        row = self._stim_list.currentRow()
        if row < 0:
            return
        self._stim_list.takeItem(row)
        self._stimuli.pop(row)
        self._refresh_list_labels()
        self._update_estimated_time()

    def _on_duplicate(self) -> None:
        row = self._stim_list.currentRow()
        if row < 0:
            return
        self._sync_editor_to_stim()
        import copy
        stim = copy.deepcopy(self._stimuli[row])
        self._stimuli.insert(row + 1, stim)
        self._stim_list.insertItem(row + 1, QListWidgetItem())
        self._refresh_list_labels()
        self._stim_list.setCurrentRow(row + 1)
        self._update_estimated_time()

    def _on_move_up(self) -> None:
        row = self._stim_list.currentRow()
        if row <= 0:
            return
        self._swap_rows(row, row - 1)
        self._stim_list.setCurrentRow(row - 1)

    def _on_move_down(self) -> None:
        row = self._stim_list.currentRow()
        if row < 0 or row >= len(self._stimuli) - 1:
            return
        self._swap_rows(row, row + 1)
        self._stim_list.setCurrentRow(row + 1)

    def _swap_rows(self, a: int, b: int) -> None:
        self._stimuli[a], self._stimuli[b] = self._stimuli[b], self._stimuli[a]
        item_a = self._stim_list.takeItem(a)
        self._stim_list.insertItem(b, item_a)
        self._refresh_list_labels()

    def _on_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._stimuli):
            return
        self._populate_editor(self._stimuli[row])

    def _refresh_list_labels(self) -> None:
        """Label each list row with its position and parameter summary."""
        for i, stim in enumerate(self._stimuli):
            item = self._stim_list.item(i)
            if item is not None:
                item.setText(f"{i + 1}. {stimulus_label(stim)}")

    def _on_stim_param_changed(self) -> None:
        self._sync_editor_to_stim()
        self._refresh_list_labels()

    def _on_stim_type_changed(self) -> None:
        row = self._stim_list.currentRow()
        if row < 0:
            return
        if self._sc_type_rb.isChecked():
            new_type = "step_protocol"
            page = 0
        elif self._vc_type_rb.isChecked():
            new_type = "voltage_step"
            page = 1
        elif self._rp_type_rb.isChecked():
            new_type = "ramp"
            page = 3
        elif self._ch_type_rb.isChecked():
            new_type = "chirp"
            page = 4
        else:
            new_type = "baseline"
            page = 2
        self._stimuli[row].type = new_type
        self._stim_stack.setCurrentIndex(page)
        self._on_stim_param_changed()
        self._update_estimated_time()

    # ------------------------------------------------------------------
    # Editor sync
    # ------------------------------------------------------------------

    def _populate_editor(self, stim: StimulusDefinition) -> None:
        widgets = (
            self._sc_type_rb, self._vc_type_rb, self._bl_type_rb,
            self._sc_min, self._sc_max, self._sc_step,
            self._sc_width, self._sc_gap, self._sc_reps,
            self._vs_step_mv, self._vs_duration,
            self._rp_type_rb, self._ch_type_rb,
            self._rp_start, self._rp_min, self._rp_max, self._rp_step,
            self._rp_width, self._rp_hold, self._rp_return, self._rp_gap, self._rp_reps,
            self._ch_start, self._ch_end, self._ch_dur, self._ch_spacing,
            self._ch_amp, self._ch_offset, self._ch_gap, self._ch_reps,
        )
        for w in widgets:
            w.blockSignals(True)
        try:
            self._sc_type_rb.setChecked(stim.type == "step_protocol")
            self._vc_type_rb.setChecked(stim.type == "voltage_step")
            self._bl_type_rb.setChecked(stim.type == "baseline")
            self._rp_type_rb.setChecked(stim.type == "ramp")
            self._ch_type_rb.setChecked(stim.type == "chirp")

            page = {
                "step_protocol": 0, "voltage_step": 1, "baseline": 2, "ramp": 3, "chirp": 4,
            }.get(stim.type, 0)
            self._stim_stack.setCurrentIndex(page)

            if stim.type == "step_protocol":
                self._sc_min.setValue(stim.min_pA if stim.min_pA is not None else -50.0)
                self._sc_max.setValue(stim.max_pA if stim.max_pA is not None else 50.0)
                self._sc_step.setValue(stim.step_pA if stim.step_pA is not None else 10.0)
                self._sc_width.setValue(stim.step_width_ms if stim.step_width_ms is not None else 500.0)
                self._sc_gap.setValue(stim.gap_ms if stim.gap_ms is not None else 500.0)
                self._sc_reps.setValue(stim.step_protocol_repeats if stim.step_protocol_repeats is not None else 1)
            elif stim.type == "voltage_step":
                self._vs_step_mv.setValue(stim.step_mV if stim.step_mV is not None else -40.0)
                self._vs_duration.setValue(stim.duration_ms if stim.duration_ms is not None else 500.0)
            elif stim.type == "ramp":
                self._rp_start.setValue(stim.ramp_start_pA)
                self._rp_min.setValue(stim.ramp_min_peak_pA)
                self._rp_max.setValue(stim.ramp_max_peak_pA)
                self._rp_step.setValue(stim.ramp_peak_step_pA)
                self._rp_width.setValue(stim.ramp_duration_ms)
                self._rp_hold.setValue(stim.ramp_hold_ms)
                self._rp_return.setCurrentIndex(self._rp_return.findData(stim.ramp_return))
                self._rp_gap.setValue(stim.gap_ms if stim.gap_ms is not None else 500.0)
                self._rp_reps.setValue(stim.step_protocol_repeats if stim.step_protocol_repeats is not None else 1)
            elif stim.type == "chirp":
                self._ch_start.setValue(stim.chirp_start_hz)
                self._ch_end.setValue(stim.chirp_end_hz)
                self._ch_dur.setValue(stim.chirp_duration_ms)
                self._ch_spacing.setCurrentIndex(self._ch_spacing.findData(stim.chirp_spacing))
                self._ch_amp.setValue(stim.chirp_amplitude_pA)
                self._ch_offset.setValue(stim.chirp_offset_pA)
                self._ch_gap.setValue(stim.gap_ms if stim.gap_ms is not None else 500.0)
                self._ch_reps.setValue(stim.step_protocol_repeats if stim.step_protocol_repeats is not None else 1)
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _sync_editor_to_stim(self) -> None:
        row = self._stim_list.currentRow()
        if row < 0 or row >= len(self._stimuli):
            return
        stim = self._stimuli[row]
        if self._sc_type_rb.isChecked():
            stim.type = "step_protocol"
        elif self._vc_type_rb.isChecked():
            stim.type = "voltage_step"
        elif self._rp_type_rb.isChecked():
            stim.type = "ramp"
        elif self._ch_type_rb.isChecked():
            stim.type = "chirp"
        else:
            stim.type = "baseline"

        if stim.type == "step_protocol":
            stim.min_pA = self._sc_min.value()
            stim.max_pA = self._sc_max.value()
            stim.step_pA = self._sc_step.value()
            stim.step_width_ms = self._sc_width.value()
            stim.gap_ms = self._sc_gap.value()
            stim.step_protocol_repeats = self._sc_reps.value()
        elif stim.type == "voltage_step":
            stim.step_mV = self._vs_step_mv.value()
            stim.duration_ms = self._vs_duration.value()
        elif stim.type == "ramp":
            stim.ramp_start_pA = self._rp_start.value()
            stim.ramp_min_peak_pA = self._rp_min.value()
            stim.ramp_max_peak_pA = self._rp_max.value()
            stim.ramp_peak_step_pA = self._rp_step.value()
            stim.ramp_duration_ms = self._rp_width.value()
            stim.ramp_hold_ms = self._rp_hold.value()
            stim.ramp_return = self._rp_return.currentData()
            stim.gap_ms = self._rp_gap.value()
            stim.step_protocol_repeats = self._rp_reps.value()
        elif stim.type == "chirp":
            stim.chirp_start_hz = self._ch_start.value()
            stim.chirp_end_hz = self._ch_end.value()
            stim.chirp_duration_ms = self._ch_dur.value()
            stim.chirp_spacing = self._ch_spacing.currentData()
            stim.chirp_amplitude_pA = self._ch_amp.value()
            stim.chirp_offset_pA = self._ch_offset.value()
            stim.gap_ms = self._ch_gap.value()
            stim.step_protocol_repeats = self._ch_reps.value()

    # ------------------------------------------------------------------
    # Protocol construction / population
    # ------------------------------------------------------------------

    def _read_protocol(self) -> TrialProtocol:
        from acquisition.trial_protocol import HyperpolarizationParams
        cc = self._cc_rb.isChecked()
        hyperpol = (
            HyperpolarizationParams(
                amplitude_pA=self._hp_amp.value(),
                duration_ms=self._hp_dur.value(),
            )
            if cc
            else None
        )
        return TrialProtocol(
            name=self._name_edit.text().strip() or "Unnamed protocol",
            clamp_mode="current_clamp" if cc else "voltage_clamp",
            pre_ms=self._pre_sb.value(),
            post_ms=self._post_sb.value(),
            iti_ms=self._iti_sb.value(),
            repeats_per_stimulus=self._reps_sb.value(),
            ao_mv_per_volt=self._ao_scale_sb.value(),
            hyperpolarization=hyperpol,
            stimuli=list(self._stimuli),
        )

    def _populate_from_protocol(self, p: TrialProtocol) -> None:
        self._name_edit.setText(p.name)

        cc = p.clamp_mode == "current_clamp"
        self._cc_rb.setChecked(cc)
        self._vc_rb.setChecked(not cc)

        self._pre_sb.setValue(p.pre_ms)
        self._post_sb.setValue(p.post_ms)
        self._iti_sb.setValue(p.iti_ms)
        self._reps_sb.setValue(p.repeats_per_stimulus)
        self._ao_scale_sb.setValue(p.ao_mv_per_volt)

        if p.hyperpolarization is not None:
            self._hp_amp.setValue(p.hyperpolarization.amplitude_pA)
            self._hp_dur.setValue(p.hyperpolarization.duration_ms)

        self._stim_list.clear()
        self._stimuli = list(p.stimuli)
        for _ in self._stimuli:
            self._stim_list.addItem(QListWidgetItem())
        self._refresh_list_labels()

        if self._stimuli:
            self._stim_list.setCurrentRow(0)

        self._update_clamp_visibility()
        self._update_estimated_time()

    # ------------------------------------------------------------------
    # Estimated run time
    # ------------------------------------------------------------------

    def _update_estimated_time(self) -> None:
        self._sync_editor_to_stim()
        try:
            p = self._read_protocol()
            secs = estimated_total_duration_s(p)
            mins = int(secs // 60)
            s = int(secs % 60)
            n = len(p.stimuli) * p.repeats_per_stimulus
            self._time_lbl.setText(
                f"Estimated run time: {mins} min {s:02d} sec  ({n} trials)"
            )
        except Exception:
            self._time_lbl.setText("Estimated run time: —")

    # ------------------------------------------------------------------
    # Save / Load
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        self._sync_editor_to_stim()
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Protocol", self._save_dir, "Protocol files (*.json)"
        )
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        try:
            save_protocol(self._read_protocol(), path)
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", f"Could not save protocol:\n{exc}")

    def _on_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Protocol", "D:/protocols", "Protocol files (*.json)"
        )
        if path:
            self.load_protocol_from_file(path)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _on_run(self) -> None:
        self._sync_editor_to_stim()
        p = self._read_protocol()

        if not p.stimuli:
            QMessageBox.warning(
                self, "No Stimuli",
                "Add at least one stimulus before running."
            )
            return

        errors, warnings = validate_protocol(p)
        if errors:
            QMessageBox.warning(self, "Invalid Stimulus", "\n\n".join(errors))
            return
        if warnings:
            answer = QMessageBox.question(
                self, "Check Stimulus",
                "\n\n".join(warnings) + "\n\nRun anyway?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        d = protocol_to_dict(p)
        d["save_dir"] = self._save_dir
        self.protocol_run_requested.emit(d)

"""
ProtocolBuilderDialog — non-modal dialog for composing and running trial protocols.

The dialog is created lazily and kept alive (hidden, not destroyed) between uses
so that in-progress protocol edits are preserved across open/close cycles.

Layout:
    ┌─────────────────────────────────────────────┐
    │  Protocol Name  [___________________]        │
    │  Clamp mode: ○ Current clamp  ○ Voltage clamp│
    ├────────────────────┬────────────────────────-┤
    │  Stimuli           │  Edit selected stimulus  │
    │  ┌──────────────┐  │  ─────────────────────  │
    │  │ staircase 0  │  │  (stacked widget)       │
    │  │ staircase 1  │  │                          │
    │  └──────────────┘  │                          │
    │  [+Add][−Remove]   │                          │
    │  [↑Up ][↓Down  ]   │                          │
    ├────────────────────┴─────────────────────────┤
    │  Global Timing                                │
    │  Pre: [___]ms  Post: [___]ms  ITI: [___]ms   │
    │  Repeats/stimulus: [___]                      │
    │  CC only: Hyperpol amp [___]pA  dur [___]ms   │
    │  VC only: AO scale [___]mV/V                  │
    ├───────────────────────────────────────────────┤
    │  [Save Protocol]  [Load Protocol]             │
    │  Estimated run time: XX min XX sec            │
    │  [       Run Protocol       ]                 │
    └───────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
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
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from acquisition.trial_protocol import (
    StimulusDefinition,
    TrialProtocol,
    estimated_total_duration_s,
    load_protocol,
    protocol_from_dict,
    protocol_to_dict,
    save_protocol,
)


class ProtocolBuilderDialog(QDialog):
    """
    Non-modal dialog for building and running trial protocols.

    Signals:
        protocol_run_requested(dict):  emitted when "Run Protocol" is clicked.
            The dict contains the serialised protocol plus "save_dir".
    """

    protocol_run_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Protocol Builder")
        self.resize(820, 680)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowMaximizeButtonHint
        )

        self._save_dir = "E:/data"

        # Internal stimulus list (parallel to QListWidget rows)
        self._stimuli: list[StimulusDefinition] = []

        self._build_ui()
        self._update_clamp_visibility()
        self._update_estimated_time()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_save_dir(self, path: str) -> None:
        self._save_dir = path

    def get_protocol(self) -> TrialProtocol:
        """Read all UI fields and return the current TrialProtocol."""
        self._sync_editor_to_stim()
        return self._read_protocol()

    def load_protocol_from_file(self, path: str | Path) -> None:
        """Load a protocol JSON and populate all UI fields."""
        try:
            p = load_protocol(path)
            self._populate_from_protocol(p)
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", f"Could not load protocol:\n{exc}")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Protocol name + clamp mode (always visible at top)
        root.addWidget(self._build_header())

        # Main splitter: stimulus list | editor
        mid_splitter = QSplitter(Qt.Horizontal)
        mid_splitter.addWidget(self._build_stimulus_list())
        mid_splitter.addWidget(self._build_stimulus_editor())
        mid_splitter.setStretchFactor(0, 1)
        mid_splitter.setStretchFactor(1, 2)
        root.addWidget(mid_splitter, stretch=1)

        # Global timing settings
        root.addWidget(self._build_global_settings())

        # Save/Load + run controls
        root.addWidget(self._build_run_controls())

    def _build_header(self) -> QWidget:
        box = QGroupBox("Protocol")
        layout = QFormLayout(box)
        layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._name_edit = QLineEdit("Unnamed protocol")
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
        self._remove_btn = QPushButton("− Remove")
        self._dup_btn = QPushButton("Duplicate")
        self._add_btn.clicked.connect(self._on_add)
        self._remove_btn.clicked.connect(self._on_remove)
        self._dup_btn.clicked.connect(self._on_duplicate)
        btn_row1.addWidget(self._add_btn)
        btn_row1.addWidget(self._remove_btn)
        btn_row1.addWidget(self._dup_btn)
        layout.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        self._up_btn = QPushButton("↑ Up")
        self._down_btn = QPushButton("↓ Down")
        self._up_btn.clicked.connect(self._on_move_up)
        self._down_btn.clicked.connect(self._on_move_down)
        btn_row2.addWidget(self._up_btn)
        btn_row2.addWidget(self._down_btn)
        layout.addLayout(btn_row2)

        return box

    def _build_stimulus_editor(self) -> QWidget:
        box = QGroupBox("Edit Stimulus")
        outer = QVBoxLayout(box)

        # Name + type row (common)
        top_form = QFormLayout()
        top_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self._stim_name_edit = QLineEdit()
        self._stim_name_edit.setPlaceholderText("e.g. 0-400 pA staircase")
        self._stim_name_edit.textChanged.connect(self._on_stim_name_changed)
        top_form.addRow("Stimulus name:", self._stim_name_edit)

        type_row = QHBoxLayout()
        self._sc_type_rb  = QRadioButton("Staircase (CC)")
        self._vc_type_rb  = QRadioButton("Voltage step (VC)")
        self._sc_type_rb.setChecked(True)
        self._sc_type_rb.toggled.connect(self._on_stim_type_changed)
        type_row.addWidget(self._sc_type_rb)
        type_row.addWidget(self._vc_type_rb)
        type_row.addStretch()
        top_form.addRow("Type:", type_row)
        outer.addLayout(top_form)

        # Stacked pages
        self._stim_stack = QStackedWidget()
        self._stim_stack.addWidget(self._build_staircase_page())   # page 0
        self._stim_stack.addWidget(self._build_vstep_page())       # page 1
        outer.addWidget(self._stim_stack)
        outer.addStretch()

        return box

    def _build_staircase_page(self) -> QWidget:
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
            return sb

        self._sc_min   = _dbl(-2000, 2000, 0.0,   1, "pA")
        self._sc_max   = _dbl(-2000, 2000, 400.0,  1, "pA")
        self._sc_step  = _dbl(0.1,   2000, 100.0,  1, "pA")
        self._sc_width = _dbl(1,    10000, 500.0,  0, "ms")
        self._sc_gap   = _dbl(0,    10000, 100.0,  0, "ms")
        self._sc_reps  = QSpinBox()
        self._sc_reps.setRange(1, 100)
        self._sc_reps.setValue(1)
        self._sc_reps.valueChanged.connect(self._update_estimated_time)

        form.addRow("Min current:", self._sc_min)
        form.addRow("Max current:", self._sc_max)
        form.addRow("Step size:",   self._sc_step)
        form.addRow("Step width:",  self._sc_width)
        form.addRow("Gap between steps:", self._sc_gap)
        form.addRow("Repeats:",     self._sc_reps)
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
            return sb

        self._vs_step_mv  = _dbl(-1000, 1000, -40.0, "mV")
        self._vs_duration = _dbl(1,    10000, 500.0,  "ms")

        form.addRow("Step voltage:", self._vs_step_mv)
        form.addRow("Duration:",     self._vs_duration)
        return w

    def _build_global_settings(self) -> QWidget:
        box = QGroupBox("Global Timing")
        outer = QVBoxLayout(box)   # outer is box's layout; form is a child layout
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

        self._pre_sb    = _dbl(0, 60000, 500.0,  "ms")
        self._post_sb   = _dbl(0, 60000, 1000.0, "ms")
        self._iti_sb    = _dbl(0, 60000, 2000.0, "ms")
        self._reps_sb   = QSpinBox()
        self._reps_sb.setRange(1, 1000)
        self._reps_sb.setValue(5)
        self._reps_sb.valueChanged.connect(self._update_estimated_time)

        form.addRow("Pre-baseline:",     self._pre_sb)
        form.addRow("Post-stimulus:",    self._post_sb)
        form.addRow("Inter-trial interval (ITI):", self._iti_sb)
        form.addRow("Repeats / stimulus:", self._reps_sb)

        # CC-only: hyperpolarisation
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
        hp_form.addRow("Duration:",  self._hp_dur)

        # VC-only: AO scale
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
        self._load_btn = QPushButton("Load Protocol…")
        self._save_btn.clicked.connect(self._on_save)
        self._load_btn.clicked.connect(self._on_load)
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._load_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._time_lbl = QLabel("Estimated run time: —")
        layout.addWidget(self._time_lbl)

        self._run_btn = QPushButton("Run Protocol")
        self._run_btn.setFixedHeight(36)
        font = self._run_btn.font()
        font.setBold(True)
        self._run_btn.setFont(font)
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
        """Add a default staircase stimulus to the list."""
        stim = StimulusDefinition(
            type="staircase",
            name=f"Staircase {len(self._stimuli)}",
        )
        self._stimuli.append(stim)
        item = QListWidgetItem(stim.name)
        self._stim_list.addItem(item)
        self._stim_list.setCurrentRow(len(self._stimuli) - 1)
        self._update_estimated_time()

    def _on_remove(self) -> None:
        row = self._stim_list.currentRow()
        if row < 0:
            return
        self._stim_list.takeItem(row)
        self._stimuli.pop(row)
        self._update_estimated_time()

    def _on_duplicate(self) -> None:
        row = self._stim_list.currentRow()
        if row < 0:
            return
        self._sync_editor_to_stim()
        import copy
        stim = copy.deepcopy(self._stimuli[row])
        stim.name = stim.name + " (copy)"
        self._stimuli.insert(row + 1, stim)
        item = QListWidgetItem(stim.name)
        self._stim_list.insertItem(row + 1, item)
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

    def _on_row_changed(self, row: int) -> None:
        """Populate editor from the newly selected stimulus."""
        if row < 0 or row >= len(self._stimuli):
            return
        self._populate_editor(self._stimuli[row])

    def _on_stim_name_changed(self, text: str) -> None:
        row = self._stim_list.currentRow()
        if row < 0 or row >= len(self._stimuli):
            return
        self._stimuli[row].name = text
        self._stim_list.item(row).setText(text)

    def _on_stim_type_changed(self) -> None:
        row = self._stim_list.currentRow()
        if row < 0:
            return
        new_type = "staircase" if self._sc_type_rb.isChecked() else "voltage_step"
        self._stimuli[row].type = new_type
        self._stim_stack.setCurrentIndex(0 if new_type == "staircase" else 1)
        self._update_estimated_time()

    # ------------------------------------------------------------------
    # Editor sync
    # ------------------------------------------------------------------

    def _populate_editor(self, stim: StimulusDefinition) -> None:
        """Fill editor widgets from a StimulusDefinition."""
        self._stim_name_edit.blockSignals(True)
        self._stim_name_edit.setText(stim.name)
        self._stim_name_edit.blockSignals(False)

        is_sc = stim.type == "staircase"
        self._sc_type_rb.blockSignals(True)
        self._vc_type_rb.blockSignals(True)
        self._sc_type_rb.setChecked(is_sc)
        self._vc_type_rb.setChecked(not is_sc)
        self._sc_type_rb.blockSignals(False)
        self._vc_type_rb.blockSignals(False)
        self._stim_stack.setCurrentIndex(0 if is_sc else 1)

        if is_sc:
            self._sc_min.setValue(stim.min_pA or 0.0)
            self._sc_max.setValue(stim.max_pA or 400.0)
            self._sc_step.setValue(stim.step_pA or 100.0)
            self._sc_width.setValue(stim.step_width_ms or 500.0)
            self._sc_gap.setValue(stim.gap_ms or 100.0)
            self._sc_reps.setValue(stim.staircase_repeats or 1)
        else:
            self._vs_step_mv.setValue(stim.step_mV or -40.0)
            self._vs_duration.setValue(stim.duration_ms or 500.0)

    def _sync_editor_to_stim(self) -> None:
        """Write editor widget values back into the selected StimulusDefinition."""
        row = self._stim_list.currentRow()
        if row < 0 or row >= len(self._stimuli):
            return
        stim = self._stimuli[row]
        stim.name = self._stim_name_edit.text()
        stim.type = "staircase" if self._sc_type_rb.isChecked() else "voltage_step"

        if stim.type == "staircase":
            stim.min_pA            = self._sc_min.value()
            stim.max_pA            = self._sc_max.value()
            stim.step_pA           = self._sc_step.value()
            stim.step_width_ms     = self._sc_width.value()
            stim.gap_ms            = self._sc_gap.value()
            stim.staircase_repeats = self._sc_reps.value()
        else:
            stim.step_mV    = self._vs_step_mv.value()
            stim.duration_ms = self._vs_duration.value()

    # ------------------------------------------------------------------
    # Protocol construction / population
    # ------------------------------------------------------------------

    def _read_protocol(self) -> TrialProtocol:
        """Construct a TrialProtocol from all UI fields."""
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
            name                 = self._name_edit.text().strip() or "Unnamed protocol",
            clamp_mode           = "current_clamp" if cc else "voltage_clamp",
            pre_ms               = self._pre_sb.value(),
            post_ms              = self._post_sb.value(),
            iti_ms               = self._iti_sb.value(),
            repeats_per_stimulus = self._reps_sb.value(),
            ao_mv_per_volt       = self._ao_scale_sb.value(),
            hyperpolarization    = hyperpol,
            stimuli              = list(self._stimuli),
        )

    def _populate_from_protocol(self, p: TrialProtocol) -> None:
        """Fill all UI fields from a TrialProtocol."""
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
        for stim in self._stimuli:
            self._stim_list.addItem(QListWidgetItem(stim.name))

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
            s    = int(secs % 60)
            n    = len(p.stimuli) * p.repeats_per_stimulus
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
            self, "Load Protocol", self._save_dir, "Protocol files (*.json)"
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

        d = protocol_to_dict(p)
        d["save_dir"] = self._save_dir
        self.hide()
        self.protocol_run_requested.emit(d)

    # ------------------------------------------------------------------
    # Keep dialog alive on close
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self.hide()
        event.ignore()

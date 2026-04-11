"""
ProtocolBuilderDialog — non-modal dialog for composing and running trial protocols.

The dialog is created lazily by :class:`~ui.main_window.MainWindow` and kept
alive (hidden, not destroyed) between uses so that in-progress protocol edits
are preserved across open/close cycles.

Layout::

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

Developer notes
---------------
``_stimuli`` is a Python list of :class:`~acquisition.trial_protocol.StimulusDefinition`
objects kept in parallel with the ``QListWidget`` rows.  The editor widgets
operate on the currently selected row.  :meth:`_sync_editor_to_stim` writes
the editor back to ``_stimuli`` before any read, ensuring consistency.

The dialog overrides ``closeEvent`` to hide itself rather than being destroyed,
preserving all UI state between invocations.
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
    """Non-modal dialog for composing, saving, loading, and running trial protocols.

    The dialog manages an ordered list of
    :class:`~acquisition.trial_protocol.StimulusDefinition` objects and
    global timing parameters.  When the user clicks "Run Protocol" the
    dialog serialises the protocol and emits ``protocol_run_requested``.

    Signals:
        protocol_run_requested(dict): Emitted when "Run Protocol" is clicked.
            The dict contains the serialised protocol (same format as
            :func:`~acquisition.trial_protocol.protocol_to_dict`) plus a
            ``"save_dir"`` key with the current save directory path.

    Attributes:
        _save_dir (str): Save directory kept in sync with
            :class:`~ui.control_panel.ControlPanel` via :meth:`set_save_dir`.
        _stimuli (list[StimulusDefinition]): Internal list of stimulus
            objects, maintained in parallel with ``_stim_list`` (QListWidget).
    """

    protocol_run_requested = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Protocol Builder")
        self.resize(820, 680)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowMaximizeButtonHint
        )

        self._save_dir = "D:/protocols"
        self._stimuli: list[StimulusDefinition] = []

        self._build_ui()
        self._update_clamp_visibility()
        self._update_estimated_time()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_save_dir(self, path: str) -> None:
        """Update the default save directory.

        Called by :class:`~ui.main_window.MainWindow` each time the dialog
        is shown to keep it in sync with the control panel.

        Args:
            path: Absolute path string to the save directory.
        """
        self._save_dir = path

    def get_protocol(self) -> TrialProtocol:
        """Read all UI fields and return the current :class:`~acquisition.trial_protocol.TrialProtocol`.

        Syncs the editor to the selected stimulus before reading so that
        any unsaved edits are included.

        Returns:
            Fully populated :class:`~acquisition.trial_protocol.TrialProtocol`.
        """
        self._sync_editor_to_stim()
        return self._read_protocol()

    def load_protocol_from_file(self, path: str | Path) -> None:
        """Load a protocol JSON file and populate all UI fields.

        Displays an error dialog if loading fails.

        Args:
            path: Path to a JSON file produced by
                :func:`~acquisition.trial_protocol.save_protocol`.
        """
        try:
            p = load_protocol(path)
            self._populate_from_protocol(p)
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", f"Could not load protocol:\n{exc}")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Assemble the full dialog layout."""
        root = QVBoxLayout(self)
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
        """Build the protocol name and clamp mode selector row."""
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
        """Build the stimulus list panel with Add / Remove / Duplicate / Move buttons."""
        box = QGroupBox("Stimuli")
        layout = QVBoxLayout(box)

        self._stim_list = QListWidget()
        self._stim_list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self._stim_list, stretch=1)

        btn_row1 = QHBoxLayout()
        self._add_btn    = QPushButton("+ Add")
        self._remove_btn = QPushButton("− Remove")
        self._dup_btn    = QPushButton("Duplicate")
        self._add_btn.clicked.connect(self._on_add)
        self._remove_btn.clicked.connect(self._on_remove)
        self._dup_btn.clicked.connect(self._on_duplicate)
        btn_row1.addWidget(self._add_btn)
        btn_row1.addWidget(self._remove_btn)
        btn_row1.addWidget(self._dup_btn)
        layout.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        self._up_btn   = QPushButton("↑ Up")
        self._down_btn = QPushButton("↓ Down")
        self._up_btn.clicked.connect(self._on_move_up)
        self._down_btn.clicked.connect(self._on_move_down)
        btn_row2.addWidget(self._up_btn)
        btn_row2.addWidget(self._down_btn)
        layout.addLayout(btn_row2)

        return box

    def _build_stimulus_editor(self) -> QWidget:
        """Build the stimulus editor with stacked pages for staircase and voltage-step."""
        box = QGroupBox("Edit Stimulus")
        outer = QVBoxLayout(box)

        top_form = QFormLayout()
        top_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self._stim_name_edit = QLineEdit()
        self._stim_name_edit.setPlaceholderText("e.g. 0-50 pA staircase")
        self._stim_name_edit.textChanged.connect(self._on_stim_name_changed)
        top_form.addRow("Stimulus name:", self._stim_name_edit)

        type_row = QHBoxLayout()
        self._sc_type_rb = QRadioButton("Staircase (CC)")
        self._vc_type_rb = QRadioButton("Voltage step (VC)")
        self._bl_type_rb = QRadioButton("Baseline")
        self._sc_type_rb.setChecked(True)
        self._sc_type_rb.toggled.connect(self._on_stim_type_changed)
        self._vc_type_rb.toggled.connect(self._on_stim_type_changed)
        self._bl_type_rb.toggled.connect(self._on_stim_type_changed)
        type_row.addWidget(self._sc_type_rb)
        type_row.addWidget(self._vc_type_rb)
        type_row.addWidget(self._bl_type_rb)
        type_row.addStretch()
        top_form.addRow("Type:", type_row)
        outer.addLayout(top_form)

        self._stim_stack = QStackedWidget()
        self._stim_stack.addWidget(self._build_staircase_page())   # page 0
        self._stim_stack.addWidget(self._build_vstep_page())       # page 1
        self._stim_stack.addWidget(self._build_baseline_page())    # page 2
        outer.addWidget(self._stim_stack)
        outer.addStretch()

        return box

    def _build_staircase_page(self) -> QWidget:
        """Build the staircase (CC) stimulus parameter form (page 0)."""
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

        self._sc_min   = _dbl(-2000, 2000, -50.0,   1, "pA")
        self._sc_max   = _dbl(-2000, 2000, 50.0,  1, "pA")
        self._sc_step  = _dbl(0.1,   2000, 10.0,  1, "pA")
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
        """Build the voltage-step (VC) stimulus parameter form (page 1)."""
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

    def _build_baseline_page(self) -> QWidget:
        """Build the baseline stimulus page (page 2) — no parameters needed."""
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

    def _build_global_settings(self) -> QWidget:
        """Build the global timing, hyperpolarization (CC), and AO scale (VC) group."""
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

        self._pre_sb  = _dbl(0, 60000, 500.0,  "ms")
        self._post_sb = _dbl(0, 60000, 1000.0, "ms")
        self._iti_sb  = _dbl(0, 60000, 2000.0, "ms")
        self._reps_sb = QSpinBox()
        self._reps_sb.setRange(1, 1000)
        self._reps_sb.setValue(5)
        self._reps_sb.valueChanged.connect(self._update_estimated_time)

        form.addRow("Pre-baseline:",              self._pre_sb)
        form.addRow("Post-stimulus:",             self._post_sb)
        form.addRow("Inter-trial interval (ITI):", self._iti_sb)
        form.addRow("Repeats / stimulus:",        self._reps_sb)

        # CC-only: hyperpolarisation pulse
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

        # VC-only: AO command sensitivity
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
        """Build the Save/Load buttons, estimated time label, and Run button."""
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

        self._run_btn = QPushButton("Use This Protocol")
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
        """Show/hide CC and VC parameter groups based on the selected clamp mode."""
        cc = self._cc_rb.isChecked()
        self._hyperpol_group.setVisible(cc)
        self._ao_scale_group.setVisible(not cc)

    def _on_clamp_mode_changed(self) -> None:
        """Handle clamp mode radio button toggle: update visibility and time estimate."""
        self._update_clamp_visibility()
        self._update_estimated_time()

    # ------------------------------------------------------------------
    # Stimulus list management
    # ------------------------------------------------------------------

    def _on_add(self) -> None:
        """Add a new default staircase stimulus entry to the list."""
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
        """Remove the currently selected stimulus from the list."""
        row = self._stim_list.currentRow()
        if row < 0:
            return
        self._stim_list.takeItem(row)
        self._stimuli.pop(row)
        self._update_estimated_time()

    def _on_duplicate(self) -> None:
        """Insert a deep copy of the selected stimulus after it in the list."""
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
        """Move the selected stimulus one position up in the list."""
        row = self._stim_list.currentRow()
        if row <= 0:
            return
        self._swap_rows(row, row - 1)
        self._stim_list.setCurrentRow(row - 1)

    def _on_move_down(self) -> None:
        """Move the selected stimulus one position down in the list."""
        row = self._stim_list.currentRow()
        if row < 0 or row >= len(self._stimuli) - 1:
            return
        self._swap_rows(row, row + 1)
        self._stim_list.setCurrentRow(row + 1)

    def _swap_rows(self, a: int, b: int) -> None:
        """Swap two rows in both the internal list and the QListWidget.

        Args:
            a: Index of the first row.
            b: Index of the second row.
        """
        self._stimuli[a], self._stimuli[b] = self._stimuli[b], self._stimuli[a]
        item_a = self._stim_list.takeItem(a)
        self._stim_list.insertItem(b, item_a)

    def _on_row_changed(self, row: int) -> None:
        """Populate the editor from the newly selected stimulus.

        Args:
            row: 0-based row index in the stimulus list.
        """
        if row < 0 or row >= len(self._stimuli):
            return
        self._populate_editor(self._stimuli[row])

    def _on_stim_name_changed(self, text: str) -> None:
        """Propagate an editor name change to the list widget and the internal list.

        Args:
            text: New stimulus name from the name QLineEdit.
        """
        row = self._stim_list.currentRow()
        if row < 0 or row >= len(self._stimuli):
            return
        self._stimuli[row].name = text
        self._stim_list.item(row).setText(text)

    def _on_stim_type_changed(self) -> None:
        """Switch the stacked editor page when the stimulus type radio button changes."""
        row = self._stim_list.currentRow()
        if row < 0:
            return
        if self._sc_type_rb.isChecked():
            new_type = "staircase"
            page = 0
        elif self._vc_type_rb.isChecked():
            new_type = "voltage_step"
            page = 1
        else:
            new_type = "baseline"
            page = 2
        self._stimuli[row].type = new_type
        self._stim_stack.setCurrentIndex(page)
        self._update_estimated_time()

    # ------------------------------------------------------------------
    # Editor sync
    # ------------------------------------------------------------------

    def _populate_editor(self, stim: StimulusDefinition) -> None:
        """Fill editor widgets from a :class:`~acquisition.trial_protocol.StimulusDefinition`.

        Blocks signals during fill to avoid spurious ``_update_estimated_time``
        calls.

        Args:
            stim: Stimulus whose values should populate the editor.
        """
        self._stim_name_edit.blockSignals(True)
        self._stim_name_edit.setText(stim.name)
        self._stim_name_edit.blockSignals(False)

        for rb in (self._sc_type_rb, self._vc_type_rb, self._bl_type_rb):
            rb.blockSignals(True)
        self._sc_type_rb.setChecked(stim.type == "staircase")
        self._vc_type_rb.setChecked(stim.type == "voltage_step")
        self._bl_type_rb.setChecked(stim.type == "baseline")
        for rb in (self._sc_type_rb, self._vc_type_rb, self._bl_type_rb):
            rb.blockSignals(False)

        page = {"staircase": 0, "voltage_step": 1, "baseline": 2}.get(stim.type, 0)
        self._stim_stack.setCurrentIndex(page)

        if stim.type == "staircase":
            self._sc_min.setValue(stim.min_pA or -50.0)
            self._sc_max.setValue(stim.max_pA or 50.0)
            self._sc_step.setValue(stim.step_pA or 10.0)
            self._sc_width.setValue(stim.step_width_ms or 500.0)
            self._sc_gap.setValue(stim.gap_ms or 500.0)
            self._sc_reps.setValue(stim.staircase_repeats or 1)
        elif stim.type == "voltage_step":
            self._vs_step_mv.setValue(stim.step_mV or -40.0)
            self._vs_duration.setValue(stim.duration_ms or 500.0)

    def _sync_editor_to_stim(self) -> None:
        """Write editor widget values back into the selected StimulusDefinition.

        Must be called before any read of ``_stimuli`` to ensure the currently
        displayed edits are captured.  No-op if no row is selected.
        """
        row = self._stim_list.currentRow()
        if row < 0 or row >= len(self._stimuli):
            return
        stim = self._stimuli[row]
        stim.name = self._stim_name_edit.text()
        if self._sc_type_rb.isChecked():
            stim.type = "staircase"
        elif self._vc_type_rb.isChecked():
            stim.type = "voltage_step"
        else:
            stim.type = "baseline"

        if stim.type == "staircase":
            stim.min_pA            = self._sc_min.value()
            stim.max_pA            = self._sc_max.value()
            stim.step_pA           = self._sc_step.value()
            stim.step_width_ms     = self._sc_width.value()
            stim.gap_ms            = self._sc_gap.value()
            stim.staircase_repeats = self._sc_reps.value()
        elif stim.type == "voltage_step":
            stim.step_mV     = self._vs_step_mv.value()
            stim.duration_ms = self._vs_duration.value()

    # ------------------------------------------------------------------
    # Protocol construction / population
    # ------------------------------------------------------------------

    def _read_protocol(self) -> TrialProtocol:
        """Construct a :class:`~acquisition.trial_protocol.TrialProtocol` from all UI fields.

        Returns:
            A fully populated :class:`~acquisition.trial_protocol.TrialProtocol`.
        """
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
        """Fill all UI fields from a :class:`~acquisition.trial_protocol.TrialProtocol`.

        Args:
            p: Protocol to display in the dialog.
        """
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
        """Recompute and display the estimated run time and trial count."""
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
        """Open a save dialog and write the current protocol to a JSON file."""
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
        """Open a file dialog and load a protocol JSON into the dialog."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Protocol", "D:/protocols", "Protocol files (*.json)"
        )
        if path:
            self.load_protocol_from_file(path)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _on_run(self) -> None:
        """Validate, serialise, and emit the protocol, then hide the dialog.

        Shows a warning if no stimuli are defined.  Otherwise serialises
        the protocol to a dict (adding ``"save_dir"``), hides the dialog,
        and emits ``protocol_run_requested``.  The main window stores the
        protocol as pending; the user starts the run by clicking
        "Run Protocol" in the main window.
        """
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
        """Hide instead of destroying the dialog so edits are preserved.

        Args:
            event: Qt close event.  Always ignored (dialog stays alive).
        """
        self.hide()
        event.ignore()

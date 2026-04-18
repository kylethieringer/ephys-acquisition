"""
ControlPanel — acquisition mode selector, start/stop, recording, and metadata.

The panel is split into several separately-placeable sub-widgets so
:class:`~ui.main_window.MainWindow` can position them independently across
the redesigned sidebar layout:

- :attr:`ControlPanel.mode_pill`: Continuous / Trial pill toggle (top chrome).
- :attr:`ControlPanel.clamp_pill`: CC / VC pill toggle (top chrome, hidden
  in trial mode).
- :attr:`ControlPanel.subject_card`: Horizontal metadata form (Acquire page).
- :attr:`ControlPanel.recording_settings`: Save directory picker (Setup page).
- :attr:`ControlPanel.protocol_widget`: Protocol dropdown + run/stop buttons
  (Acquire page / Protocol page).
- :attr:`ControlPanel.recording_bar`: Start/Stop, Record/Stop, Quick note
  (bottom action bar).

Developer notes
---------------
The panel emits signals but holds no references to acquisition objects.
:class:`~ui.main_window.MainWindow` connects the signals to the appropriate
acquisition controller methods.  All public signal names and helper methods
are preserved from the previous layout so the wiring in MainWindow is
unchanged.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import PillToggle


class ControlPanel(QWidget):
    """Acquisition control panel: mode, clamp, metadata, protocol, recording.

    This widget itself is invisible — :class:`~ui.main_window.MainWindow`
    uses the individual sub-widget accessors directly and places them in
    different parts of the layout.

    Signals:
        start_requested(): User clicked the Start button.
        stop_requested(): User clicked the Stop button.
        record_requested(str, dict): User clicked Record.  Arguments are
            ``(save_dir, metadata)`` where ``metadata`` is the subject info
            dict from :meth:`get_metadata`.
        stop_record_requested(): User clicked Stop Recording.
        mode_changed(str): User changed the acquisition-mode pill.
            Argument is ``"continuous"`` or ``"trial"``.
        clamp_mode_changed(str): User changed the clamp pill.  Argument is
            ``"current_clamp"`` or ``"voltage_clamp"``.
        open_protocol_builder_requested(): User clicked "Open Protocol Builder…".
        run_protocol_requested(): User clicked "Run Protocol".
        stop_protocol_requested(): User clicked "Stop Protocol".
        protocol_selected(str): User chose a protocol file from the dropdown;
            argument is the full file path.

    Attributes:
        _save_dir (str): Currently selected save directory path.
    """

    start_requested                 = Signal()
    stop_requested                  = Signal()
    record_requested                = Signal(str, dict)   # save_dir, metadata
    stop_record_requested           = Signal()
    mode_changed                    = Signal(str)
    clamp_mode_changed              = Signal(str)         # "current_clamp" or "voltage_clamp"
    open_protocol_builder_requested = Signal()
    run_protocol_requested          = Signal()
    stop_protocol_requested         = Signal()
    protocol_selected               = Signal(str)         # full file path
    status_text_changed             = Signal(str)         # forwarded from set_status
    expt_id_changed                 = Signal(str)         # forwarded from Experiment ID field

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._save_dir       = "D:/data"
        self._protocol_folder = "D:/protocols"

        # Sub-widgets built lazily in _build_*
        self._mode_pill         = PillToggle(
            [("continuous", "Continuous"), ("trial", "Trial")]
        )
        self._clamp_pill        = PillToggle(
            [("current_clamp", "CC"), ("voltage_clamp", "VC")]
        )
        self._subject_card      = QWidget()
        self._recording_settings = QWidget()
        self._protocol_widget   = QWidget()
        self._recording_bar     = QWidget()

        self._build_subject_card()
        self._build_recording_settings()
        self._build_protocol_widget()
        self._build_recording_bar()
        self._wire_pills()

        # This widget is an invisible host; main_window only takes sub-widgets.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

    # ------------------------------------------------------------------
    # Sub-widget accessors
    # ------------------------------------------------------------------

    @property
    def mode_pill(self) -> PillToggle:
        """Continuous / Trial pill selector (placed in top chrome)."""
        return self._mode_pill

    @property
    def clamp_pill(self) -> PillToggle:
        """CC / VC pill selector (placed in top chrome; hidden in trial mode)."""
        return self._clamp_pill

    @property
    def subject_card(self) -> QWidget:
        """Horizontal subject-metadata form (placed on Acquire page)."""
        return self._subject_card

    @property
    def recording_settings(self) -> QWidget:
        """Save-directory picker (placed on Setup page)."""
        return self._recording_settings

    @property
    def protocol_widget(self) -> QWidget:
        """Protocol dropdown, builder, run/stop (Acquire and Protocol pages)."""
        return self._protocol_widget

    @property
    def recording_bar(self) -> QWidget:
        """Start/Stop, Record/Stop Recording (bottom action bar)."""
        return self._recording_bar

    # ------------------------------------------------------------------
    # Public API (preserved from previous layout)
    # ------------------------------------------------------------------

    @property
    def save_dir(self) -> str:
        """Currently selected save directory path."""
        return self._save_dir

    def get_metadata(self) -> dict:
        """Read the subject metadata form and return it as a dict.

        Returns:
            Dict with keys: ``"expt_id"``, ``"genotype"``, ``"age"``,
            ``"sex"``, ``"targeted_cell_type"``, ``"notes"``,
            ``"drug_applied"``, ``"drug_name"``, ``"drug_concentration"``.
            ``expt_id`` falls back to ``"expt_xx"`` if the field is empty.
        """
        return {
            "expt_id":            self._expt_id_edit.text().strip() or "expt_xx",
            "genotype":           self._genotype_edit.text().strip(),
            "age":                self._age_edit.text().strip(),
            "sex":                self._sex_combo.currentText(),
            "targeted_cell_type": self._cell_type_edit.text().strip(),
            "notes":              self._notes_edit.toPlainText().strip(),
            "drug_applied":       self._drug_check.isChecked(),
            "drug_name":          self._drug_name_edit.text().strip() if self._drug_check.isChecked() else "",
            "drug_concentration": self._drug_conc_edit.text().strip() if self._drug_check.isChecked() else "",
        }

    def validate_metadata(self) -> list[str]:
        """Check required metadata fields and return names of empty ones."""
        missing: list[str] = []
        if not self._expt_id_edit.text().strip():
            missing.append("Experiment ID")
        if not self._genotype_edit.text().strip():
            missing.append("Genotype")
        if not self._age_edit.text().strip():
            missing.append("Age")
        if not self._cell_type_edit.text().strip():
            missing.append("Target cell type")
        return missing

    def set_running(self, running: bool) -> None:
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        if not running:
            self.set_recording(False)

    def set_stopping(self) -> None:
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)

    def set_recording(self, recording: bool) -> None:
        self._record_btn.setEnabled(not recording)
        self._stop_rec_btn.setEnabled(recording)

    def set_status(self, msg: str) -> None:
        self._status_lbl.setText(msg)
        self.status_text_changed.emit(msg)

    def enable_run_protocol_button(self, enabled: bool) -> None:
        self._run_protocol_btn.setEnabled(enabled)

    def enable_record_button(self, enabled: bool) -> None:
        self._record_btn.setEnabled(enabled)

    def enable_stop_protocol_button(self, enabled: bool) -> None:
        self._stop_protocol_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Pill wiring
    # ------------------------------------------------------------------

    def _wire_pills(self) -> None:
        """Forward pill selection changes to mode_changed / clamp_mode_changed."""
        self._mode_pill.changed.connect(self.mode_changed.emit)
        self._clamp_pill.changed.connect(self.clamp_mode_changed.emit)
        # Clamp pill is hidden in trial mode (matches previous clamp_mode_box behavior)
        self._mode_pill.changed.connect(
            lambda v: self._clamp_pill.setVisible(v == "continuous")
        )

    # ------------------------------------------------------------------
    # UI construction — Subject card (horizontal grid)
    # ------------------------------------------------------------------

    def _build_subject_card(self) -> None:
        """Build the horizontal subject-metadata card for the Acquire page."""
        box = QGroupBox("Subject", self._subject_card)
        root = QVBoxLayout(self._subject_card)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(box)

        grid = QGridLayout(box)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setContentsMargins(10, 14, 10, 10)

        def _tiny(label: str) -> QLabel:
            lab = QLabel(label)
            lab.setProperty("tier", "tiny")
            return lab

        # --- Row 1: ExptID | Genotype | Age | Sex | Target ---
        self._expt_id_edit   = QLineEdit()
        self._expt_id_edit.setPlaceholderText("e.g. fly_241017")
        self._expt_id_edit.setProperty("mono", True)
        self._expt_id_edit.textChanged.connect(self.expt_id_changed.emit)

        self._genotype_edit  = QLineEdit()
        self._genotype_edit.setPlaceholderText("e.g. gal4-uas")

        self._age_edit       = QLineEdit()
        self._age_edit.setPlaceholderText("e.g. 4")
        self._age_edit.setMaximumWidth(70)

        self._sex_combo      = QComboBox()
        self._sex_combo.addItems(["F", "M"])
        self._sex_combo.setMaximumWidth(70)

        self._cell_type_edit = QLineEdit()
        self._cell_type_edit.setPlaceholderText("e.g. dvmn")

        grid.addWidget(_tiny("Experiment ID"),     0, 0)
        grid.addWidget(_tiny("Genotype"),          0, 1)
        grid.addWidget(_tiny("Age"),               0, 2)
        grid.addWidget(_tiny("Sex"),               0, 3)
        grid.addWidget(_tiny("Target cell"),       0, 4)

        grid.addWidget(self._expt_id_edit,   1, 0)
        grid.addWidget(self._genotype_edit,  1, 1)
        grid.addWidget(self._age_edit,       1, 2)
        grid.addWidget(self._sex_combo,      1, 3)
        grid.addWidget(self._cell_type_edit, 1, 4)

        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(3, 1)
        grid.setColumnStretch(4, 3)

        # --- Row 2: Drug toggle + drug name + drug conc ---
        self._drug_check       = QCheckBox("Drug applied")
        self._drug_name_edit   = QLineEdit()
        self._drug_name_edit.setPlaceholderText("e.g. TTX")
        self._drug_name_edit.setEnabled(False)
        self._drug_conc_edit   = QLineEdit()
        self._drug_conc_edit.setPlaceholderText("e.g. 1 µM")
        self._drug_conc_edit.setEnabled(False)
        self._drug_name_label  = _tiny("Drug name")
        self._drug_conc_label  = _tiny("Concentration")
        self._drug_check.toggled.connect(self._on_drug_toggled)

        grid.addWidget(self._drug_check,      2, 0, 1, 1, Qt.AlignBottom)
        grid.addWidget(self._drug_name_label, 2, 1)
        grid.addWidget(self._drug_conc_label, 2, 2, 1, 2)
        grid.addWidget(self._drug_name_edit,  3, 1)
        grid.addWidget(self._drug_conc_edit,  3, 2, 1, 2)

        # --- Row 3: Notes textarea spanning all columns ---
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText(
            "Cell health, perfusion changes, or anything notable about this experiment…"
        )
        self._notes_edit.setFixedHeight(66)
        grid.addWidget(_tiny("Notes"),     4, 0, 1, 5)
        grid.addWidget(self._notes_edit,   5, 0, 1, 5)

    # ------------------------------------------------------------------
    # UI construction — Recording settings (save dir)
    # ------------------------------------------------------------------

    def _build_recording_settings(self) -> None:
        """Build the save-directory picker (Setup page)."""
        box = QGroupBox("Data Recording", self._recording_settings)
        root = QVBoxLayout(self._recording_settings)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(box)

        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 14, 10, 10)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        dir_label = QLabel("Directory:")
        dir_label.setProperty("tier", "secondary")
        dir_row.addWidget(dir_label)
        self._dir_edit = QLineEdit(self._save_dir)
        self._dir_edit.setReadOnly(True)
        dir_row.addWidget(self._dir_edit, stretch=1)
        self._browse_btn = QPushButton("Browse")
        self._browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(self._browse_btn)
        layout.addLayout(dir_row)

    # ------------------------------------------------------------------
    # UI construction — protocol widget
    # ------------------------------------------------------------------

    def _build_protocol_widget(self) -> None:
        """Build the protocol sub-widget: dropdown, builder, run, and stop buttons."""
        box = QGroupBox("Protocol", self._protocol_widget)
        root = QVBoxLayout(self._protocol_widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(box)

        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 14, 10, 10)
        layout.setSpacing(6)

        # Dropdown + refresh + builder row
        proto_row = QHBoxLayout()
        proto_row.setSpacing(4)
        self._protocol_combo = QComboBox()
        self._protocol_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._protocol_combo.setPlaceholderText("Select saved protocol…")
        self._protocol_combo.activated.connect(self._on_protocol_selected)

        self._refresh_protocols_btn = QPushButton("↻")
        self._refresh_protocols_btn.setFixedWidth(30)
        self._refresh_protocols_btn.setToolTip("Refresh protocol list")
        self._refresh_protocols_btn.clicked.connect(self._scan_protocol_folder)

        self._open_builder_btn = QPushButton("Open Builder…")
        self._open_builder_btn.clicked.connect(self.open_protocol_builder_requested)

        proto_row.addWidget(self._protocol_combo, stretch=1)
        proto_row.addWidget(self._refresh_protocols_btn)
        proto_row.addWidget(self._open_builder_btn)
        layout.addLayout(proto_row)

        # Run / Stop Protocol buttons
        run_stop_row = QHBoxLayout()
        run_stop_row.setSpacing(4)
        self._run_protocol_btn = QPushButton("Run Protocol")
        self._run_protocol_btn.setEnabled(False)
        self._run_protocol_btn.setProperty("accent", "primary")
        self._run_protocol_btn.clicked.connect(self.run_protocol_requested)

        self._stop_protocol_btn = QPushButton("Stop Protocol")
        self._stop_protocol_btn.setEnabled(False)
        self._stop_protocol_btn.clicked.connect(self.stop_protocol_requested)

        run_stop_row.addWidget(self._run_protocol_btn)
        run_stop_row.addWidget(self._stop_protocol_btn)
        layout.addLayout(run_stop_row)

        self._scan_protocol_folder()

    # ------------------------------------------------------------------
    # UI construction — recording bar (bottom action bar)
    # ------------------------------------------------------------------

    def _build_recording_bar(self) -> None:
        """Build the bottom action bar: Start/Stop, Record/Stop Recording, status."""
        root = QHBoxLayout(self._recording_bar)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)

        self._start_btn = QPushButton("Start")
        self._start_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._start_btn.setProperty("accent", "primary")
        self._start_btn.clicked.connect(self.start_requested)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self.stop_requested)

        self._record_btn = QPushButton("● Record")
        self._record_btn.setEnabled(False)
        self._record_btn.setProperty("accent", "record")
        self._record_btn.clicked.connect(self._on_record)

        self._stop_rec_btn = QPushButton("Stop Recording")
        self._stop_rec_btn.setEnabled(False)
        self._stop_rec_btn.clicked.connect(self.stop_record_requested)

        # Status label is hidden in the action bar (StatusBadge shows state
        # in the top chrome); kept here so set_status still works for API.
        self._status_lbl = QLabel("Ready")
        self._status_lbl.setProperty("tier", "secondary")
        self._status_lbl.setWordWrap(True)

        root.addWidget(self._start_btn)
        root.addWidget(self._stop_btn)
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #2e333d;")
        sep.setFixedHeight(20)
        root.addWidget(sep)
        root.addWidget(self._record_btn)
        root.addWidget(self._stop_rec_btn)
        root.addWidget(self._status_lbl, stretch=1)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _scan_protocol_folder(self) -> None:
        """Scan the protocol folder and populate the dropdown with .json files."""
        from pathlib import Path as _Path
        self._protocol_combo.clear()
        folder = _Path(self._protocol_folder)
        if folder.exists():
            for p in sorted(folder.glob("*.json")):
                self._protocol_combo.addItem(p.stem, userData=str(p))

    def _on_protocol_selected(self, index: int) -> None:
        path = self._protocol_combo.itemData(index)
        if path:
            self.protocol_selected.emit(path)

    def _on_drug_toggled(self, checked: bool) -> None:
        self._drug_name_edit.setEnabled(checked)
        self._drug_conc_edit.setEnabled(checked)
        # Visible but dimmed when unchecked (design-driven behavior)

    def _browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select Save Directory", self._save_dir
        )
        if path:
            self._save_dir = path
            self._dir_edit.setText(path)

    def _on_record(self) -> None:
        self.record_requested.emit(self._save_dir, self.get_metadata())

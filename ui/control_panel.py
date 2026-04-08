"""
ControlPanel — acquisition mode selector, start/stop, and recording controls.

The panel is split into three separately-placeable sub-widgets so
:class:`~ui.main_window.MainWindow` can position them independently:

- :attr:`ControlPanel.settings_widget`: mode toggle, save directory browser,
  and subject metadata form.
- :attr:`ControlPanel.protocol_widget`: protocol dropdown, builder button,
  Run Protocol, and Stop Protocol buttons.
- :attr:`ControlPanel.recording_bar`: Start/Stop, Record/Stop Recording buttons
  and a status label (always visible at the bottom of the window).

Developer notes
---------------
The panel emits signals but holds no references to acquisition objects.
:class:`~ui.main_window.MainWindow` connects the signals to the appropriate
acquisition controller methods.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class ControlPanel(QWidget):
    """Acquisition control panel: mode selection, start/stop, and recording.

    This widget itself is invisible — :class:`~ui.main_window.MainWindow`
    uses :attr:`settings_widget` and :attr:`recording_bar` directly and
    places them in different parts of the layout.

    Signals:
        start_requested(): User clicked the Start button.
        stop_requested(): User clicked the Stop button.
        record_requested(str, dict): User clicked Record.  Arguments are
            ``(save_dir, metadata)`` where ``metadata`` is the subject info
            dict from :meth:`get_metadata`.
        stop_record_requested(): User clicked Stop Recording.
        mode_changed(str): User toggled the acquisition mode radio button.
            Argument is ``"continuous"`` or ``"trial"``.
        open_protocol_builder_requested(): User clicked "Open Protocol Builder…".

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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._save_dir       = "E:/data"
        self._protocol_folder = "E:/protocols"

        self._settings_widget  = QWidget()
        self._protocol_widget  = QWidget()
        self._recording_bar    = QWidget()
        self._build_settings()
        self._build_protocol_widget()
        self._build_recording_bar()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

    # ------------------------------------------------------------------
    # Accessors for the two sub-widgets
    # ------------------------------------------------------------------

    @property
    def settings_widget(self) -> QWidget:
        """Sub-widget containing mode selector, save dir, and metadata."""
        return self._settings_widget

    @property
    def protocol_widget(self) -> QWidget:
        """Sub-widget containing the protocol dropdown, builder, and run/stop buttons."""
        return self._protocol_widget

    @property
    def recording_bar(self) -> QWidget:
        """Sub-widget containing Start/Stop, Record/Stop Recording buttons and status label."""
        return self._recording_bar

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def save_dir(self) -> str:
        """Currently selected save directory path."""
        return self._save_dir

    def get_metadata(self) -> dict:
        """Read the subject metadata form and return it as a dict.

        Returns:
            Dict with keys: ``"expt_id"``, ``"genotype"``, ``"age"``,
            ``"sex"``, ``"targeted_cell_type"``, ``"notes"``.
            ``expt_id`` falls back to ``"expt_xx"`` if the field is empty.
        """
        return {
            "expt_id":            self._expt_id_edit.text().strip() or "expt_xx",
            "genotype":           self._genotype_edit.text().strip(),
            "age":                self._age_edit.text().strip(),
            "sex":                self._sex_combo.currentText(),
            "targeted_cell_type": self._cell_type_edit.text().strip(),
            "notes":              self._notes_edit.toPlainText().strip(),
        }

    def set_running(self, running: bool) -> None:
        """Update Start/Stop button states to reflect running status.

        When ``running`` is ``False``, also calls :meth:`set_recording` to
        ensure the Record button is disabled.

        Args:
            running: ``True`` if acquisition is active.
        """
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        if not running:
            self.set_recording(False)

    def set_stopping(self) -> None:
        """Disable both Start and Stop while the post-trigger guard delay runs.

        Called by :class:`~ui.main_window.MainWindow` between pressing Stop
        and the ``stopped`` signal arriving, to prevent double-clicks.
        """
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)

    def set_recording(self, recording: bool) -> None:
        """Update Record/Stop Recording button states.

        Args:
            recording: ``True`` if a recording is in progress.
        """
        self._record_btn.setEnabled(not recording)
        self._stop_rec_btn.setEnabled(recording)

    def set_status(self, msg: str) -> None:
        """Update the status label text in the recording bar.

        Args:
            msg: Message to display (e.g. ``"Recording…"`` or ``"Stopped."``).
        """
        self._status_lbl.setText(msg)

    def enable_run_protocol_button(self, enabled: bool) -> None:
        """Enable or disable the Run Protocol button.

        Enabled by :class:`~ui.main_window.MainWindow` once a protocol has
        been staged via "Use This Protocol" in the builder dialog.

        Args:
            enabled: ``True`` to enable the button.
        """
        self._run_protocol_btn.setEnabled(enabled)

    def enable_record_button(self, enabled: bool) -> None:
        """Enable or disable the Record button independently of recording state.

        Used by :class:`~ui.main_window.MainWindow` to keep Record disabled
        until acquisition is running.

        Args:
            enabled: ``True`` to enable the button.
        """
        self._record_btn.setEnabled(enabled)

    def enable_stop_protocol_button(self, enabled: bool) -> None:
        """Enable or disable the Stop Protocol button.

        Enabled by :class:`~ui.main_window.MainWindow` once a protocol is
        actively running; disabled again when it finishes or is cancelled.

        Args:
            enabled: ``True`` to enable the button.
        """
        self._stop_protocol_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    # UI construction — settings widget
    # ------------------------------------------------------------------

    def _build_settings(self) -> None:
        """Build the settings sub-widget: mode, clamp mode, protocol buttons, save dir, metadata."""
        root = QVBoxLayout(self._settings_widget)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        # --- Mode selector ---
        mode_box = QGroupBox("Acquisition Mode")
        mode_layout = QHBoxLayout(mode_box)
        self._continuous_rb = QRadioButton("Continuous")
        self._trial_rb      = QRadioButton("Trial-based")
        self._continuous_rb.setChecked(True)
        self._continuous_rb.toggled.connect(
            lambda checked: self.mode_changed.emit("continuous") if checked else None
        )
        self._trial_rb.toggled.connect(
            lambda checked: self.mode_changed.emit("trial") if checked else None
        )
        mode_layout.addWidget(self._continuous_rb)
        mode_layout.addWidget(self._trial_rb)
        root.addWidget(mode_box)

        # --- Clamp Mode selector (continuous mode only) ---
        self._clamp_mode_box = QGroupBox("Clamp Mode")
        clamp_layout = QHBoxLayout(self._clamp_mode_box)
        self._cc_clamp_rb = QRadioButton("Current clamp")
        self._vc_clamp_rb = QRadioButton("Voltage clamp")
        self._cc_clamp_rb.setChecked(True)
        self._cc_clamp_rb.toggled.connect(
            lambda checked: self.clamp_mode_changed.emit("current_clamp") if checked else None
        )
        self._vc_clamp_rb.toggled.connect(
            lambda checked: self.clamp_mode_changed.emit("voltage_clamp") if checked else None
        )
        clamp_layout.addWidget(self._cc_clamp_rb)
        clamp_layout.addWidget(self._vc_clamp_rb)
        self._trial_rb.toggled.connect(
            lambda checked: self._clamp_mode_box.setVisible(not checked)
        )
        root.addWidget(self._clamp_mode_box)

        # --- Save settings (directory) ---
        save_box = QGroupBox("Data Recording")
        save_layout = QVBoxLayout(save_box)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Directory:"))
        self._dir_edit = QLineEdit(self._save_dir)
        self._dir_edit.setReadOnly(True)
        dir_row.addWidget(self._dir_edit, stretch=1)
        self._browse_btn = QPushButton("Browse")
        self._browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(self._browse_btn)
        save_layout.addLayout(dir_row)

        root.addWidget(save_box)

        # --- Subject metadata ---
        meta_box = QGroupBox("Subject Metadata")
        meta_layout = QFormLayout(meta_box)
        meta_layout.setRowWrapPolicy(QFormLayout.DontWrapRows)
        meta_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._expt_id_edit       = QLineEdit()
        self._expt_id_edit.setPlaceholderText("e.g. frexxx")
        self._genotype_edit      = QLineEdit()
        self._genotype_edit.setPlaceholderText("e.g. gal4-uas")
        self._age_edit           = QLineEdit()
        self._age_edit.setPlaceholderText("e.g. 4")
        self._sex_combo          = QComboBox()
        self._sex_combo.addItems(["not specified", "M", "F"])
        self._cell_type_edit     = QLineEdit()
        self._cell_type_edit.setPlaceholderText("e.g. dvmn")
        self._notes_edit         = QPlainTextEdit()
        self._notes_edit.setPlaceholderText("e.g. experiment notes")
        self._notes_edit.setMaximumHeight(60)

        meta_layout.addRow("Experiment ID:", self._expt_id_edit)
        meta_layout.addRow("Genotype:",      self._genotype_edit)
        meta_layout.addRow("Age:",           self._age_edit)
        meta_layout.addRow("Sex:",           self._sex_combo)
        meta_layout.addRow("Target cell type:", self._cell_type_edit)
        meta_layout.addRow("Notes:",         self._notes_edit)

        root.addWidget(meta_box)
        root.addStretch()

    # ------------------------------------------------------------------
    # UI construction — protocol widget
    # ------------------------------------------------------------------

    def _build_protocol_widget(self) -> None:
        """Build the protocol sub-widget: dropdown, builder, run, and stop buttons."""
        root = QVBoxLayout(self._protocol_widget)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        proto_box = QGroupBox("Protocol")
        proto_layout = QVBoxLayout(proto_box)

        # Dropdown + refresh + builder row
        proto_row = QHBoxLayout()
        self._protocol_combo = QComboBox()
        self._protocol_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._protocol_combo.setPlaceholderText("Select saved protocol…")
        self._protocol_combo.activated.connect(self._on_protocol_selected)

        self._refresh_protocols_btn = QPushButton("↻")
        self._refresh_protocols_btn.setFixedWidth(30)
        self._refresh_protocols_btn.setToolTip("Refresh protocol list")
        self._refresh_protocols_btn.clicked.connect(self._scan_protocol_folder)

        self._open_builder_btn = QPushButton("Open Protocol Builder…")
        self._open_builder_btn.clicked.connect(self.open_protocol_builder_requested)

        proto_row.addWidget(self._protocol_combo, stretch=1)
        proto_row.addWidget(self._refresh_protocols_btn)
        proto_row.addWidget(self._open_builder_btn)
        proto_layout.addLayout(proto_row)

        # Run / Stop Protocol buttons
        run_stop_row = QHBoxLayout()
        self._run_protocol_btn = QPushButton("Run Protocol")
        self._run_protocol_btn.setEnabled(False)
        self._run_protocol_btn.clicked.connect(self.run_protocol_requested)

        self._stop_protocol_btn = QPushButton("Stop Protocol")
        self._stop_protocol_btn.setEnabled(False)
        self._stop_protocol_btn.clicked.connect(self.stop_protocol_requested)

        run_stop_row.addWidget(self._run_protocol_btn)
        run_stop_row.addWidget(self._stop_protocol_btn)
        proto_layout.addLayout(run_stop_row)

        root.addWidget(proto_box)
        root.addStretch()

        # Populate the protocol dropdown on startup
        self._scan_protocol_folder()

    # ------------------------------------------------------------------
    # UI construction — recording bar
    # ------------------------------------------------------------------

    def _build_recording_bar(self) -> None:
        """Build the recording bar: Start/Stop, Record/Stop Recording, and status label."""
        root = QHBoxLayout(self._recording_bar)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        self._start_btn = QPushButton("Start")
        self._stop_btn  = QPushButton("Stop")
        self._start_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._stop_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._stop_btn.setEnabled(False)
        self._start_btn.clicked.connect(self.start_requested)
        self._stop_btn.clicked.connect(self.stop_requested)

        self._record_btn   = QPushButton("Record")
        self._stop_rec_btn = QPushButton("Stop Recording")
        self._record_btn.setEnabled(False)
        self._stop_rec_btn.setEnabled(False)
        self._record_btn.clicked.connect(self._on_record)
        self._stop_rec_btn.clicked.connect(self.stop_record_requested)

        self._status_lbl = QLabel("Ready")
        self._status_lbl.setWordWrap(True)

        root.addWidget(self._start_btn)
        root.addWidget(self._stop_btn)
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
        """Emit ``protocol_selected`` with the full path of the chosen file."""
        path = self._protocol_combo.itemData(index)
        if path:
            self.protocol_selected.emit(path)

    def _browse_dir(self) -> None:
        """Open a directory chooser and update the save directory."""
        path = QFileDialog.getExistingDirectory(
            self, "Select Save Directory", self._save_dir
        )
        if path:
            self._save_dir = path
            self._dir_edit.setText(path)

    def _on_record(self) -> None:
        """Emit ``record_requested`` with the current save dir and metadata."""
        self.record_requested.emit(self._save_dir, self.get_metadata())

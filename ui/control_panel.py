"""
ControlPanel — acquisition mode selector, start/stop, and recording controls.

Split into two placeable widgets:
    settings_widget:     mode toggle, start/stop, save dir, prefix
    recording_bar:       record / stop-recording buttons + status label
"""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class ControlPanel(QWidget):
    """
    Acquisition control panel.

    Signals:
        start_requested():               user clicked Start
        stop_requested():                user clicked Stop
        record_requested(str, str):      (save_dir, prefix)
        stop_record_requested():         user clicked Stop Recording
        mode_changed(str):               "continuous" | "trial"  (future)
    """

    start_requested      = Signal()
    stop_requested       = Signal()
    record_requested     = Signal(str, str)   # save_dir, prefix
    stop_record_requested = Signal()
    mode_changed         = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._save_dir = str(Path.home() / "ephys_data")

        # Build sub-widgets (they have no parent yet — MainWindow will place them)
        self._settings_widget = QWidget()
        self._recording_bar   = QWidget()
        self._build_settings()
        self._build_recording_bar()

        # This widget itself is invisible — MainWindow uses settings_widget
        # and recording_bar directly.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

    # ------------------------------------------------------------------
    # Accessors for the two sub-widgets
    # ------------------------------------------------------------------

    @property
    def settings_widget(self) -> QWidget:
        """Mode selector + Start/Stop + save directory + prefix."""
        return self._settings_widget

    @property
    def recording_bar(self) -> QWidget:
        """Record / Stop Recording buttons + status label."""
        return self._recording_bar

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_running(self, running: bool) -> None:
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        if not running:
            self.set_recording(False)

    def set_stopping(self) -> None:
        """Disable both Start and Stop while the post-trigger guard delay runs."""
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)

    def set_recording(self, recording: bool) -> None:
        self._record_btn.setEnabled(not recording)
        self._stop_rec_btn.setEnabled(recording)

    def set_status(self, msg: str) -> None:
        self._status_lbl.setText(msg)

    def enable_record_button(self, enabled: bool) -> None:
        self._record_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    # UI construction — settings widget
    # ------------------------------------------------------------------

    def _build_settings(self) -> None:
        root = QVBoxLayout(self._settings_widget)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        # --- Mode selector ---
        mode_box = QGroupBox("Acquisition Mode")
        mode_layout = QHBoxLayout(mode_box)
        self._continuous_rb = QRadioButton("Continuous")
        self._trial_rb      = QRadioButton("Trial-based")
        self._continuous_rb.setChecked(True)
        self._trial_rb.setEnabled(False)   # placeholder — not yet implemented
        self._continuous_rb.toggled.connect(
            lambda checked: self.mode_changed.emit("continuous") if checked else None
        )
        self._trial_rb.toggled.connect(
            lambda checked: self.mode_changed.emit("trial") if checked else None
        )
        mode_layout.addWidget(self._continuous_rb)
        mode_layout.addWidget(self._trial_rb)
        root.addWidget(mode_box)

        # --- Start / Stop ---
        acq_box = QGroupBox("Acquisition")
        acq_layout = QHBoxLayout(acq_box)
        self._start_btn = QPushButton("Start")
        self._stop_btn  = QPushButton("Stop")
        self._start_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._stop_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._stop_btn.setEnabled(False)
        self._start_btn.clicked.connect(self.start_requested)
        self._stop_btn.clicked.connect(self.stop_requested)
        acq_layout.addWidget(self._start_btn)
        acq_layout.addWidget(self._stop_btn)
        root.addWidget(acq_box)

        # --- Save settings (directory + prefix) ---
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

        prefix_row = QHBoxLayout()
        prefix_row.addWidget(QLabel("Prefix:"))
        self._prefix_edit = QLineEdit("ephys")
        prefix_row.addWidget(self._prefix_edit, stretch=1)
        save_layout.addLayout(prefix_row)

        root.addWidget(save_box)
        root.addStretch()

    # ------------------------------------------------------------------
    # UI construction — recording bar (always visible at bottom)
    # ------------------------------------------------------------------

    def _build_recording_bar(self) -> None:
        root = QHBoxLayout(self._recording_bar)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        self._record_btn   = QPushButton("Record")
        self._stop_rec_btn = QPushButton("Stop Recording")
        self._record_btn.setEnabled(False)
        self._stop_rec_btn.setEnabled(False)
        self._record_btn.clicked.connect(self._on_record)
        self._stop_rec_btn.clicked.connect(self.stop_record_requested)

        self._status_lbl = QLabel("Ready")
        self._status_lbl.setWordWrap(True)

        root.addWidget(self._record_btn)
        root.addWidget(self._stop_rec_btn)
        root.addWidget(self._status_lbl, stretch=1)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select Save Directory", self._save_dir
        )
        if path:
            self._save_dir = path
            self._dir_edit.setText(path)

    def _on_record(self) -> None:
        self.record_requested.emit(self._save_dir, self._prefix_edit.text() or "ephys")

"""
MainWindow — top-level Qt window that assembles all panels.

Layout::

    QSplitter (horizontal, 65 / 35 split)
    ├── Left:  LiveTracePanel  (5 rolling AI traces at 20 kHz)
    └── Right: QWidget (vertical)
                ├── QTabWidget
                │   ├── Tab "Acquisition": mode selector, start/stop,
                │   │       data recording, TTL settings, channel toggles
                │   └── Tab "Experiment":  camera preview, stimulus panel,
                │           y-axis range controls
                └── Recording bar (always visible): record/stop + status label

Signal wiring summary
---------------------
- :class:`~ui.control_panel.ControlPanel` signals → acquisition controller
  methods (start, stop, record, stop_record, mode_changed).
- :class:`~ui.camera_panel.CameraPanel` ``ttl_config_changed`` → both
  :class:`~acquisition.continuous_mode.ContinuousAcquisition` and
  :class:`~acquisition.trial_mode.TrialAcquisition` (kept in sync).
- :class:`~ui.stimulus_panel.StimulusPanel` signals → continuous acquisition
  ao0 control (apply / clear).
- Acquisition ``started`` / ``stopped`` / ``error_occurred`` → UI state
  updates (button enables, status label).
- Trial signals (``trial_started``, ``protocol_finished``, etc.) → status label.

Developer notes
---------------
Both acquisition controllers are always instantiated; only the **active** one
is started when the user clicks Start.  The ring buffer from
:class:`~acquisition.continuous_mode.ContinuousAcquisition` is shared by
both modes — :class:`~acquisition.trial_mode.TrialAcquisition` pushes chunks
directly into it so the trace panel always shows live data regardless of mode.

:class:`~ui.protocol_builder.ProtocolBuilderDialog` is created lazily on
first use and kept alive (hidden) between uses to preserve edits.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config import AI_CHANNELS

from acquisition.continuous_mode import ContinuousAcquisition
from acquisition.trial_mode import TrialAcquisition
from acquisition.trial_protocol import protocol_from_dict
from ui.camera_panel import CameraPanel
from ui.control_panel import ControlPanel
from ui.protocol_builder import ProtocolBuilderDialog
from ui.stimulus_panel import StimulusPanel
from ui.trace_panel import ChannelYControls, LiveTracePanel


class MainWindow(QMainWindow):
    """Top-level application window for the ephys acquisition GUI.

    Assembles all sub-panels and wires signals between UI components and
    the two acquisition back-ends (continuous and trial-based).

    Attributes:
        _acq (ContinuousAcquisition): Continuous acquisition back-end.
            Also owns the ring buffer used by trial mode.
        _trial_acq (TrialAcquisition): Trial-based acquisition back-end.
        _active_mode (str): Currently selected mode — ``"continuous"`` or
            ``"trial"``.
        _trace_panel (LiveTracePanel): Left-panel rolling trace display.
        _camera_panel (CameraPanel): Camera preview and TTL settings widget.
        _stim_panel (StimulusPanel): Quick staircase stimulus builder.
        _ctrl_panel (ControlPanel): Mode selector, start/stop, recording bar.
        _protocol_builder (ProtocolBuilderDialog | None): Protocol editor
            dialog, created lazily on first use.
        _channel_cbs (list[QCheckBox]): One checkbox per AI channel for
            toggling trace visibility.
        _y_controls (list[ChannelYControls]): Y-range spinboxes for each trace.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Ephys Acquisition")
        self.resize(1600, 900)

        # --- Acquisition back-ends ---
        self._acq = ContinuousAcquisition(self)
        self._acq.started.connect(self._on_acq_started)
        self._acq.stopped.connect(self._on_acq_stopped)
        self._acq.error_occurred.connect(self._on_error)
        self._acq.recording_started.connect(self._on_recording_started)
        self._acq.recording_stopped.connect(self._on_recording_stopped)

        self._trial_acq = TrialAcquisition(self)
        self._trial_acq.started.connect(self._on_acq_started)
        self._trial_acq.stopped.connect(self._on_acq_stopped)
        self._trial_acq.error_occurred.connect(self._on_error)
        self._trial_acq.trial_started.connect(self._on_trial_started)
        self._trial_acq.trial_finished.connect(self._on_trial_finished)
        self._trial_acq.protocol_finished.connect(self._on_protocol_finished)
        self._trial_acq.protocol_cancelled.connect(self._on_protocol_cancelled)

        self._active_mode = "continuous"

        # --- Panels ---
        self._trace_panel  = LiveTracePanel()
        self._camera_panel = CameraPanel()
        self._stim_panel   = StimulusPanel()
        self._ctrl_panel   = ControlPanel()

        # Ring buffer shared between both modes (trace panel always shows data)
        self._trace_panel.set_ring_buffer(self._acq.ring_buffer)

        # Camera frames: continuous mode → panel preview
        self._acq.connect_frame_callback(self._camera_panel.update_frame)

        # Trial mode: frames → panel preview; AI chunks → shared ring buffer
        self._trial_acq.connect_frame_callback(self._camera_panel.update_frame)
        self._trial_acq.connect_data_callback(self._acq.ring_buffer.push)

        # --- Protocol builder dialog (created lazily) ---
        self._protocol_builder: ProtocolBuilderDialog | None = None

        # --- Signal wiring: control panel → acquisition ---
        self._ctrl_panel.start_requested.connect(self._on_start)
        self._ctrl_panel.stop_requested.connect(self._on_stop)
        self._ctrl_panel.record_requested.connect(self._on_record)
        self._ctrl_panel.stop_record_requested.connect(self._on_stop_record)
        self._ctrl_panel.mode_changed.connect(self._on_mode_changed)
        self._ctrl_panel.open_protocol_builder_requested.connect(
            self._on_open_protocol_builder
        )

        self._camera_panel.ttl_config_changed.connect(self._on_ttl_changed)
        self._stim_panel.stimulus_applied.connect(self._acq.apply_stimulus_waveform)
        self._stim_panel.stimulus_cleared.connect(self._acq.clear_stimulus)

        # --- Build tab content ---
        tabs = QTabWidget()
        tabs.addTab(self._build_acquisition_tab(), "Acquisition")
        tabs.addTab(self._build_experiment_tab(), "Experiment")

        # --- Right panel: tabs + always-visible recording bar ---
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(tabs, stretch=1)
        right_layout.addWidget(self._ctrl_panel.recording_bar)

        # --- Main splitter ---
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(self._trace_panel)
        main_splitter.addWidget(right_widget)
        main_splitter.setStretchFactor(0, 65)
        main_splitter.setStretchFactor(1, 35)

        self.setCentralWidget(main_splitter)

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------

    def _build_acquisition_tab(self) -> QWidget:
        """Build Tab 1: acquisition mode, start/stop, recording settings, channel toggles.

        Returns:
            A :class:`QScrollArea` wrapping the tab content so it is
            scrollable if the window is resized to be very small.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        layout.addWidget(self._ctrl_panel.settings_widget)
        layout.addWidget(self._camera_panel.ttl_widget)

        channels_box = QGroupBox("Channels to Display")
        ch_layout = QVBoxLayout(channels_box)
        ch_layout.setSpacing(2)
        self._channel_cbs: list[QCheckBox] = []
        for i, (name, _, _, _, units) in enumerate(AI_CHANNELS):
            cb = QCheckBox(f"{name} ({units})")
            cb.setChecked(True)
            cb.toggled.connect(lambda checked, idx=i: self._toggle_channel(idx, checked))
            ch_layout.addWidget(cb)
            self._channel_cbs.append(cb)
        ch_layout.addStretch()
        layout.addWidget(channels_box)

        layout.addStretch()

        scroll.setWidget(container)
        return scroll

    def _build_experiment_tab(self) -> QWidget:
        """Build Tab 2: camera preview, stimulus panel, y-axis range controls.

        Returns:
            A :class:`QSplitter` (vertical) so the user can resize the
            camera preview, stimulus panel, and y-range sections.
        """
        y_ranges_box = QGroupBox("Y Ranges")
        y_layout = QVBoxLayout(y_ranges_box)
        y_layout.setSpacing(2)
        self._y_controls: list[ChannelYControls] = []
        for i, plot in enumerate(self._trace_panel.plots):
            ctrl = ChannelYControls(i, plot)
            y_layout.addWidget(ctrl)
            self._y_controls.append(ctrl)
        y_layout.addStretch()

        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(4, 4, 4, 4)
        bottom_layout.setSpacing(6)
        bottom_layout.addWidget(y_ranges_box)
        bottom_layout.addStretch()

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self._camera_panel.preview_widget)
        splitter.addWidget(self._stim_panel)
        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 0)

        return splitter

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _on_mode_changed(self, mode: str) -> None:
        """Update the active acquisition mode.

        Args:
            mode: ``"continuous"`` or ``"trial"``.
        """
        self._active_mode = mode

    # ------------------------------------------------------------------
    # Acquisition slots
    # ------------------------------------------------------------------

    def _toggle_channel(self, index: int, visible: bool) -> None:
        """Show or hide a channel's plot widget in the trace panel.

        Args:
            index: 0-based channel index matching :data:`~config.AI_CHANNELS`.
            visible: ``True`` to show the plot.
        """
        self._trace_panel.plot_widgets[index].setVisible(visible)

    def _on_start(self) -> None:
        """Handle the Start button: start the active acquisition back-end."""
        self._ctrl_panel.set_status("Starting acquisition…")
        if self._active_mode == "trial":
            self._trial_acq.start()
        else:
            self._acq.start()

    def _on_stop(self) -> None:
        """Handle the Stop button: cancel any protocol and stop the active back-end."""
        self._ctrl_panel.set_stopping()
        self._ctrl_panel.set_status("Stopping…")
        if self._active_mode == "trial":
            if self._trial_acq.is_protocol_active:
                self._trial_acq.cancel_protocol()
            self._trial_acq.stop()
        else:
            self._acq.stop()

    def _on_record(self, save_dir: str, metadata: dict) -> None:
        """Handle the Record button: open the HDF5 file and start TTL.

        Args:
            save_dir: Root directory from the control panel.
            metadata: Subject info dict from :meth:`~ui.control_panel.ControlPanel.get_metadata`.
        """
        try:
            self._acq.start_recording(save_dir, metadata)
        except Exception as exc:
            self._on_error(str(exc))

    def _on_stop_record(self) -> None:
        """Handle Stop Recording: stop TTL and start the guard delay."""
        self._ctrl_panel.set_status("Stopping camera triggers…")
        self._acq.stop_recording()

    def _on_ttl_changed(self, frame_rate_hz: float, exposure_ms: float) -> None:
        """Propagate TTL parameter changes to both acquisition back-ends.

        Args:
            frame_rate_hz: New camera frame rate in Hz.
            exposure_ms: New camera exposure duration in ms.
        """
        self._acq.set_ttl_config(frame_rate_hz, exposure_ms)
        self._trial_acq.set_ttl_config(frame_rate_hz, exposure_ms)
        self._ctrl_panel.set_status(
            f"TTL updated: {frame_rate_hz:.2f} Hz, {exposure_ms:.2f} ms exposure"
        )

    # ------------------------------------------------------------------
    # Protocol builder
    # ------------------------------------------------------------------

    def _on_open_protocol_builder(self) -> None:
        """Show the protocol builder dialog, creating it lazily on first call."""
        if self._protocol_builder is None:
            self._protocol_builder = ProtocolBuilderDialog(self)
            self._protocol_builder.protocol_run_requested.connect(
                self._on_run_protocol
            )
        self._protocol_builder.set_save_dir(self._ctrl_panel.save_dir)
        self._protocol_builder.show()
        self._protocol_builder.raise_()
        self._protocol_builder.activateWindow()

    def _on_run_protocol(self, protocol_dict: dict) -> None:
        """Receive a serialised protocol from the builder and start a trial run.

        Deserialises the protocol, starts the trial acquisition back-end if
        not already running, and calls ``run_protocol``.

        Args:
            protocol_dict: Dict from
                :class:`~ui.protocol_builder.ProtocolBuilderDialog`, containing
                the serialised protocol fields plus ``"save_dir"``.
        """
        try:
            save_dir = protocol_dict.pop("save_dir", self._ctrl_panel.save_dir)
            protocol = protocol_from_dict(protocol_dict)
            metadata = self._ctrl_panel.get_metadata()

            if not self._trial_acq.is_running:
                self._trial_acq.start()

            self._trial_acq.run_protocol(protocol, save_dir, metadata)
            n = len(protocol.stimuli) * protocol.repeats_per_stimulus
            self._ctrl_panel.set_status(
                f"Protocol '{protocol.name}' running — {n} trials total…"
            )
        except Exception as exc:
            self._on_error(str(exc))

    # ------------------------------------------------------------------
    # Acquisition state callbacks
    # ------------------------------------------------------------------

    def _on_acq_started(self) -> None:
        """Handle the ``started`` signal: enable UI controls for the running state."""
        self._ctrl_panel.set_running(True)
        if self._active_mode == "continuous":
            self._ctrl_panel.enable_record_button(True)
            self._ctrl_panel.set_status("Acquiring — camera armed, waiting for Record")
        else:
            self._ctrl_panel.set_status(
                "Acquisition started — open Protocol Builder to run a protocol"
            )

    def _on_acq_stopped(self) -> None:
        """Handle the ``stopped`` signal: disable all acquisition UI controls."""
        self._ctrl_panel.set_running(False)
        self._ctrl_panel.enable_record_button(False)
        self._ctrl_panel.set_status("Stopped.")

    def _on_recording_started(self, folder: Path) -> None:
        """Handle ``recording_started``: update the recording bar UI.

        Args:
            folder: Experiment folder path emitted by
                :class:`~acquisition.continuous_mode.ContinuousAcquisition`.
        """
        self._ctrl_panel.set_recording(True)
        self._ctrl_panel.set_status(f"Recording + camera → {folder.name}/")

    def _on_recording_stopped(self, n_samples: int) -> None:
        """Handle ``recording_stopped``: show sample count and re-enable Record.

        Args:
            n_samples: Total samples saved, as emitted by
                :class:`~acquisition.continuous_mode.ContinuousAcquisition`.
        """
        self._ctrl_panel.set_recording(False)
        self._ctrl_panel.set_status(
            f"Recording stopped. {n_samples:,} samples saved."
        )
        if self._acq.is_running:
            self._ctrl_panel.enable_record_button(True)

    # ------------------------------------------------------------------
    # Trial mode callbacks
    # ------------------------------------------------------------------

    def _on_trial_started(self, trial_idx: int, total: int) -> None:
        """Handle ``trial_started``: update the status label.

        Args:
            trial_idx: 0-based index of the trial that just started.
            total: Total number of trials in the run.
        """
        self._ctrl_panel.set_status(f"Trial {trial_idx + 1} / {total} running…")

    def _on_trial_finished(self, trial_idx: int, total: int) -> None:
        """Handle ``trial_finished``: show saved trial count and ITI status.

        Args:
            trial_idx: 0-based index of the trial that just finished.
            total: Total number of trials in the run.
        """
        self._ctrl_panel.set_status(
            f"Trial {trial_idx + 1} / {total} saved. ITI…"
        )

    def _on_protocol_finished(self, path: Path) -> None:
        """Handle ``protocol_finished``: show the save path and stop the back-end.

        Args:
            path: Path to the completed HDF5 file.
        """
        self._ctrl_panel.set_status(f"Protocol complete. Saved: {path.name}")
        self._trial_acq.stop()

    def _on_protocol_cancelled(self, n_completed: int) -> None:
        """Handle ``protocol_cancelled``: show how many trials were completed.

        Args:
            n_completed: Number of trials saved before cancellation.
        """
        self._ctrl_panel.set_status(f"Protocol cancelled after {n_completed} trial(s).")

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def _on_error(self, msg: str) -> None:
        """Handle an acquisition error: reset UI and show a modal error dialog.

        Args:
            msg: Human-readable error message from a worker or acquisition
                controller.
        """
        self._ctrl_panel.set_running(False)
        self._ctrl_panel.set_status(f"Error: {msg}")
        QMessageBox.critical(self, "Acquisition Error", msg)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """Stop all running acquisition back-ends before closing the window.

        Args:
            event: Qt close event.  Always accepted.
        """
        if self._trial_acq.is_running:
            self._trial_acq.stop()
        if self._acq.is_running:
            self._acq.stop()
        event.accept()

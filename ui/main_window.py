"""
MainWindow — top-level Qt window that assembles all panels.

Layout:
    QSplitter (horizontal)
    ├── Left:  LiveTracePanel  (5 rolling AI traces)
    └── Right: QWidget (vertical)
                ├── QTabWidget
                │   ├── Tab "Acquisition": mode, start/stop, data recording, TTL settings
                │   └── Tab "Experiment":  camera preview, stimulus, y ranges, channels
                └── Recording bar (always visible): record/stop buttons + status
"""

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
from ui.camera_panel import CameraPanel
from ui.control_panel import ControlPanel
from ui.stimulus_panel import StimulusPanel
from ui.trace_panel import ChannelYControls, LiveTracePanel


class MainWindow(QMainWindow):
    """Main application window for ephys acquisition."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ephys Acquisition")
        self.resize(1600, 900)

        # --- Acquisition back-end ---
        self._acq = ContinuousAcquisition(self)
        self._acq.started.connect(self._on_acq_started)
        self._acq.stopped.connect(self._on_acq_stopped)
        self._acq.error_occurred.connect(self._on_error)
        self._acq.recording_started.connect(self._on_recording_started)
        self._acq.recording_stopped.connect(self._on_recording_stopped)

        # --- Panels ---
        self._trace_panel   = LiveTracePanel()
        self._camera_panel  = CameraPanel()
        self._stim_panel    = StimulusPanel()
        self._ctrl_panel    = ControlPanel()

        # Wire ring buffer to trace display
        self._trace_panel.set_ring_buffer(self._acq.ring_buffer)

        # Wire camera frames to camera panel
        self._acq.connect_frame_callback(self._camera_panel.update_frame)

        # --- Signal wiring ---
        self._ctrl_panel.start_requested.connect(self._on_start)
        self._ctrl_panel.stop_requested.connect(self._on_stop)
        self._ctrl_panel.record_requested.connect(self._on_record)
        self._ctrl_panel.stop_record_requested.connect(self._on_stop_record)

        self._camera_panel.ttl_config_changed.connect(self._on_ttl_changed)

        self._stim_panel.stimulus_applied.connect(self._acq.apply_stimulus_waveform)
        self._stim_panel.stimulus_cleared.connect(self._acq.clear_stimulus)

        # Keep stimulus panel in sync with TTL settings
        self._camera_panel.ttl_config_changed.connect(self._stim_panel.set_ttl_params)

        # --- Build right-panel tabs ---
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
        """Tab 1: acquisition mode, start/stop, data recording, TTL settings, channels."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        layout.addWidget(self._ctrl_panel.settings_widget)
        layout.addWidget(self._camera_panel.ttl_widget)

        # Channels to Display
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
        """Tab 2: camera preview, stimulus params/preview, y ranges."""
        # Y Ranges
        y_ranges_box = QGroupBox("Y Ranges")
        y_layout = QVBoxLayout(y_ranges_box)
        y_layout.setSpacing(2)
        self._y_controls: list[ChannelYControls] = []
        for i, plot in enumerate(self._trace_panel.plots):
            ctrl = ChannelYControls(i, plot)
            y_layout.addWidget(ctrl)
            self._y_controls.append(ctrl)
        y_layout.addStretch()

        # Y Ranges in a scrollable container (fixed height, doesn't need splitter space)
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(4, 4, 4, 4)
        bottom_layout.setSpacing(6)
        bottom_layout.addWidget(y_ranges_box)
        bottom_layout.addStretch()

        # Vertical splitter so camera preview and stimulus panel are resizable
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self._camera_panel.preview_widget)
        splitter.addWidget(self._stim_panel)
        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 0)

        return splitter

    # ------------------------------------------------------------------
    # Acquisition slots
    # ------------------------------------------------------------------

    def _toggle_channel(self, index: int, visible: bool) -> None:
        self._trace_panel.plot_widgets[index].setVisible(visible)

    def _on_start(self) -> None:
        self._ctrl_panel.set_status("Starting acquisition\u2026")
        self._acq.start()

    def _on_stop(self) -> None:
        self._ctrl_panel.set_stopping()
        self._ctrl_panel.set_status("Stopping\u2026")
        self._acq.stop()

    def _on_record(self, save_dir: str, prefix: str) -> None:
        try:
            self._acq.start_recording(save_dir, prefix)
            self._ctrl_panel.set_status("Starting camera triggers\u2026")
        except Exception as exc:
            self._on_error(str(exc))

    def _on_stop_record(self) -> None:
        self._ctrl_panel.set_status("Stopping camera triggers\u2026")
        self._acq.stop_recording()

    def _on_ttl_changed(self, frame_rate_hz: float, exposure_ms: float) -> None:
        self._acq.set_ttl_config(frame_rate_hz, exposure_ms)
        self._ctrl_panel.set_status(
            f"TTL updated: {frame_rate_hz:.2f} Hz, {exposure_ms:.2f} ms exposure"
        )

    # ------------------------------------------------------------------
    # Acquisition state callbacks
    # ------------------------------------------------------------------

    def _on_acq_started(self) -> None:
        self._ctrl_panel.set_running(True)
        self._ctrl_panel.enable_record_button(True)
        self._ctrl_panel.set_status("Acquiring (no camera triggers)")

    def _on_acq_stopped(self) -> None:
        self._ctrl_panel.set_running(False)
        self._ctrl_panel.enable_record_button(False)
        self._ctrl_panel.set_status("Stopped.")

    def _on_recording_started(self, path: Path) -> None:
        self._ctrl_panel.set_recording(True)
        self._ctrl_panel.set_status(f"Recording + camera \u2192 {path.name}")

    def _on_recording_stopped(self, n_samples: int) -> None:
        self._ctrl_panel.set_recording(False)
        self._ctrl_panel.set_status(
            f"Recording stopped. {n_samples:,} samples saved."
        )
        # Re-enable record button if acquisition is still running
        if self._acq.is_running:
            self._ctrl_panel.enable_record_button(True)

    def _on_error(self, msg: str) -> None:
        self._ctrl_panel.set_running(False)
        self._ctrl_panel.set_status(f"Error: {msg}")
        QMessageBox.critical(self, "Acquisition Error", msg)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        # Immediate teardown — no guard delay since the process is exiting.
        if self._acq.is_recording:
            self._acq._teardown_camera()
            self._acq._close_recording()
        if self._acq.is_running:
            self._acq.stop()
        event.accept()

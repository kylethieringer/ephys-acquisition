"""
MainWindow — top-level Qt window that assembles all panels.

Layout (redesigned)::

    QMainWindow
    └── central widget (vertical)
        ├── TopChromeBar  (session label + mode/clamp pills + status badge)
        ├── QHBoxLayout
        │   ├── Sidebar (icon rail: Acquire / Protocol / Camera / Channels / Setup)
        │   └── QVBoxLayout
        │       ├── QStackedWidget
        │       │   ├── AcquirePage
        │       │   │   └── QSplitter (horizontal)
        │       │   │       ├── LiveTracePanel
        │       │   │       └── right column (vertical)
        │       │   │           ├── Camera preview (top, ~280 px)
        │       │   │           └── QScrollArea
        │       │   │               └── Subject / Protocol / Stimulus / Channels
        │       │   ├── ProtocolPage
        │       │   ├── CameraPage
        │       │   ├── ChannelsPage
        │       │   └── SetupPage
        │       └── Recording bar (always visible, bottom)

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
  updates (button enables, status label, top-chrome status badge).
- Trial signals (``trial_started``, ``protocol_finished``, etc.) → status label.

Developer notes
---------------
Both acquisition controllers are always instantiated; only the **active** one
is started when the user clicks Start.  The ring buffer from
:class:`~acquisition.continuous_mode.ContinuousAcquisition` is shared by
both modes.

:class:`~ui.protocol_builder.ProtocolBuilderDialog` is created lazily on
first use and kept alive (hidden) between uses to preserve edits.

The camera preview widget is re-parented between the Acquire page (pinned
top-right) and the full Camera page on page switch so only one
``pg.ImageView`` instance exists.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config import AI_CHANNELS, AI_CHANNELS_VC, AI_Y_DEFAULTS, AI_Y_DEFAULTS_VC

from acquisition.continuous_mode import ContinuousAcquisition
from acquisition.trial_mode import TrialAcquisition
from acquisition.trial_protocol import protocol_from_dict
from ui.camera_panel import CameraPanel
from ui.control_panel import ControlPanel
from ui.protocol_builder import ProtocolBuilderDialog
from ui.stimulus_panel import StimulusPanel
from ui.trace_panel import ChannelYControls, LiveTracePanel
from ui.widgets import Sidebar, TopChromeBar


SIDEBAR_ITEMS = [
    ("acquire",  "wave",   "Acquire"),
    ("protocol", "flask",  "Protocol"),
    ("camera",   "camera", "Camera"),
    ("channels", "dot",    "Channels"),
    ("setup",    "gear",   "Setup"),
]


class MainWindow(QMainWindow):
    """Top-level application window for the ephys acquisition GUI."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Ephys Acquisition")
        self.resize(1600, 900)

        # --- Acquisition back-ends ---
        self._acq = ContinuousAcquisition(self)
        self._trial_acq = TrialAcquisition(self)
        self._active_mode       = "continuous"
        self._pending_protocol: dict | None = None

        # --- Panels ---
        self._trace_panel  = LiveTracePanel()
        self._camera_panel = CameraPanel()
        self._stim_panel   = StimulusPanel()
        self._ctrl_panel   = ControlPanel()

        self._protocol_builder: ProtocolBuilderDialog | None = None
        self._channel_cbs: list[QCheckBox] = []
        self._y_controls:  list[ChannelYControls] = []

        # --- Top chrome ---
        self._chrome = TopChromeBar()

        # --- Sidebar + stacked pages ---
        self._sidebar = Sidebar(SIDEBAR_ITEMS)
        self._stack   = QStackedWidget()

        self._page_acquire  = self._build_acquire_page()
        self._page_protocol = self._build_protocol_page()
        self._page_camera   = self._build_camera_page()
        self._page_channels = self._build_channels_page()
        self._page_setup    = self._build_setup_page()

        self._page_index = {
            "acquire":  self._stack.addWidget(self._page_acquire),
            "protocol": self._stack.addWidget(self._page_protocol),
            "camera":   self._stack.addWidget(self._page_camera),
            "channels": self._stack.addWidget(self._page_channels),
            "setup":    self._stack.addWidget(self._page_setup),
        }

        # --- Central layout ---
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._chrome)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._sidebar)

        workspace = QVBoxLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(0)
        workspace.addWidget(self._stack, stretch=1)

        action_bar_wrap = QFrame()
        action_bar_wrap.setProperty("chrome", True)
        action_bar_wrap.setFrameShape(QFrame.NoFrame)
        abl = QVBoxLayout(action_bar_wrap)
        abl.setContentsMargins(0, 0, 0, 0)
        abl.addWidget(self._ctrl_panel.recording_bar)
        workspace.addWidget(action_bar_wrap)

        body.addLayout(workspace, stretch=1)
        root.addLayout(body, stretch=1)

        self.setCentralWidget(central)

        # --- Wire signals ---
        self._wire_acquisition_signals()
        self._wire_control_signals()
        self._wire_chrome_and_sidebar()

    # ------------------------------------------------------------------
    # Signal wiring (called once from __init__)
    # ------------------------------------------------------------------

    def _wire_acquisition_signals(self) -> None:
        """Connect acquisition back-end signals to UI slots."""
        self._acq.started.connect(self._on_acq_started)
        self._acq.stopped.connect(self._on_acq_stopped)
        self._acq.error_occurred.connect(self._on_error)
        self._acq.recording_started.connect(self._on_recording_started)
        self._acq.recording_stopped.connect(self._on_recording_stopped)
        self._acq.conversion_status.connect(self._ctrl_panel.set_status)
        self._acq.protocol_finished.connect(self._on_continuous_protocol_finished)
        self._acq.protocol_cancelled.connect(self._on_continuous_protocol_cancelled)

        self._trial_acq.started.connect(self._on_acq_started)
        self._trial_acq.stopped.connect(self._on_acq_stopped)
        self._trial_acq.error_occurred.connect(self._on_error)
        self._trial_acq.trial_started.connect(self._on_trial_started)
        self._trial_acq.trial_finished.connect(self._on_trial_finished)
        self._trial_acq.protocol_finished.connect(self._on_protocol_finished)
        self._trial_acq.protocol_cancelled.connect(self._on_protocol_cancelled)

        self._trace_panel.set_ring_buffer(self._acq.ring_buffer)

        self._acq.connect_frame_callback(self._camera_panel.update_frame)
        self._trial_acq.connect_frame_callback(self._camera_panel.update_frame)
        self._trial_acq.connect_data_callback(self._acq.ring_buffer.push)

    def _wire_control_signals(self) -> None:
        """Connect control panel and peripheral widget signals."""
        self._ctrl_panel.start_requested.connect(self._on_start)
        self._ctrl_panel.stop_requested.connect(self._on_stop)
        self._ctrl_panel.record_requested.connect(self._on_record)
        self._ctrl_panel.stop_record_requested.connect(self._on_stop_record)
        self._ctrl_panel.mode_changed.connect(self._on_mode_changed)
        self._ctrl_panel.clamp_mode_changed.connect(self._on_clamp_mode_changed)
        self._ctrl_panel.open_protocol_builder_requested.connect(
            self._on_open_protocol_builder
        )
        self._ctrl_panel.run_protocol_requested.connect(self._on_start_protocol)
        self._ctrl_panel.stop_protocol_requested.connect(self._on_stop_protocol)
        self._ctrl_panel.protocol_selected.connect(self._on_protocol_file_selected)

        self._camera_panel.ttl_config_changed.connect(self._on_ttl_changed)
        self._stim_panel.stimulus_applied.connect(self._acq.apply_stimulus_waveform)
        self._stim_panel.stimulus_cleared.connect(self._acq.clear_stimulus)

    def _wire_chrome_and_sidebar(self) -> None:
        """Wire top-chrome pills / status badge and sidebar page switching."""
        # Sidebar → QStackedWidget page switching
        self._sidebar.page_changed.connect(self._on_page_changed)

        # Top-chrome pills ↔ control-panel pills (two-way sync).  Using
        # separate PillToggle instances lets us place them in different
        # parent widgets without parenting conflicts.
        self._chrome.mode_pill.changed.connect(self._ctrl_panel.mode_pill.set_value)
        self._chrome.mode_pill.changed.connect(self._ctrl_panel.mode_changed.emit)
        self._chrome.mode_pill.changed.connect(
            lambda v: self._chrome.clamp_pill.setVisible(v == "continuous")
        )
        self._chrome.clamp_pill.changed.connect(self._ctrl_panel.clamp_pill.set_value)
        self._chrome.clamp_pill.changed.connect(self._ctrl_panel.clamp_mode_changed.emit)

        # Session label ← control-panel Experiment ID field
        self._ctrl_panel.expt_id_changed.connect(self._chrome.set_session_label)

        # Status badge ← control-panel status text (and acquisition state).
        # The text feeds the fallback label; state transitions below drive
        # the LED color.
        self._ctrl_panel.status_text_changed.connect(self._chrome.status_badge.set_text)

    # ------------------------------------------------------------------
    # Page builders
    # ------------------------------------------------------------------

    def _build_acquire_page(self) -> QWidget:
        """Build the Acquire page: traces on the left, camera-pinned right column."""
        page = QWidget()
        page_layout = QHBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # --- Left: traces ---
        splitter.addWidget(self._trace_panel)

        # --- Right: camera on top + scrollable controls below ---
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Camera mount (re-parentable between this page and the Camera page)
        self._acquire_camera_mount = QFrame()
        self._acquire_camera_mount.setFixedHeight(300)
        self._acquire_camera_mount.setFrameShape(QFrame.NoFrame)
        cm_layout = QVBoxLayout(self._acquire_camera_mount)
        cm_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._acquire_camera_mount)

        # Scrollable controls
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        layout.addWidget(self._ctrl_panel.subject_card)
        layout.addWidget(self._ctrl_panel.protocol_widget)
        # StimulusPanel shown only in continuous mode (toggled by _on_mode_changed)
        self._stim_panel_wrap = QFrame()
        sw_layout = QVBoxLayout(self._stim_panel_wrap)
        sw_layout.setContentsMargins(0, 0, 0, 0)
        sw_layout.addWidget(self._stim_panel)
        layout.addWidget(self._stim_panel_wrap)

        layout.addWidget(self._build_channels_groupbox())

        layout.addStretch(1)
        scroll.setWidget(container)
        right_layout.addWidget(scroll, stretch=1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 65)
        splitter.setStretchFactor(1, 35)
        splitter.setSizes([1000, 540])

        page_layout.addWidget(splitter)

        # Initial camera mount → this page
        self._mount_camera(self._acquire_camera_mount)

        return page

    def _build_channels_groupbox(self) -> QGroupBox:
        """Build the channels/Y-range groupbox used inside the Acquire page."""
        box = QGroupBox("Channels")
        v = QVBoxLayout(box)
        v.setContentsMargins(10, 14, 10, 10)
        v.setSpacing(4)

        for i, (name, _, _, _, units) in enumerate(AI_CHANNELS):
            row = QHBoxLayout()
            row.setSpacing(6)
            cb = QCheckBox(f"{name} ({units})")
            cb.setChecked(True)
            cb.toggled.connect(lambda checked, idx=i: self._toggle_channel(idx, checked))
            self._channel_cbs.append(cb)
            ctrl = ChannelYControls(i, self._trace_panel.plots[i])
            self._y_controls.append(ctrl)

            row.addWidget(cb, stretch=1)
            v.addLayout(row)
            v.addWidget(ctrl)

        return box

    def _build_protocol_page(self) -> QWidget:
        """Full-page protocol view: reuses the control panel's protocol widget."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QLabel("Protocol")
        header.setStyleSheet("font-size: 14pt; font-weight: 600; color: #e7ebf0;")
        layout.addWidget(header)

        hint = QLabel(
            "Pick a saved protocol below or open the full Builder to compose a new one. "
            "Run and Stop also live on the Acquire page."
        )
        hint.setProperty("tier", "secondary")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._protocol_page_mount = QFrame()
        self._protocol_page_mount.setFrameShape(QFrame.NoFrame)
        pm = QVBoxLayout(self._protocol_page_mount)
        pm.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._protocol_page_mount)
        layout.addStretch(1)

        return page

    def _build_camera_page(self) -> QWidget:
        """Full-screen camera view + TTL controls beneath."""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        # Preview mount (camera preview is re-parented here when this page shows)
        self._camera_page_mount = QFrame()
        self._camera_page_mount.setFrameShape(QFrame.NoFrame)
        cm = QVBoxLayout(self._camera_page_mount)
        cm.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._camera_page_mount, stretch=3)

        # TTL controls on the right
        side = QVBoxLayout()
        side.setSpacing(10)
        side.addWidget(self._camera_panel.ttl_widget)
        side.addStretch(1)

        side_wrap = QWidget()
        side_wrap.setLayout(side)
        side_wrap.setFixedWidth(320)
        layout.addWidget(side_wrap)

        return page

    def _build_channels_page(self) -> QWidget:
        """Full-page channel list and Y-range controls.

        Note: the Acquire page owns the per-channel ``QCheckBox`` / ``ChannelYControls``
        widgets; duplicating them here would diverge state. Instead, this page
        shows a summary and directs the user to the Acquire page.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QLabel("Channels")
        header.setStyleSheet("font-size: 14pt; font-weight: 600; color: #e7ebf0;")
        layout.addWidget(header)

        hint = QLabel(
            "Channel visibility and Y-range controls live in the right column "
            "of the Acquire page so they're adjustable while viewing traces."
        )
        hint.setProperty("tier", "secondary")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Static summary of the channel configuration from config.py
        summary = QGroupBox("Configured channels")
        sl = QVBoxLayout(summary)
        sl.setContentsMargins(10, 14, 10, 10)
        for i, (name, ai, _, _, units) in enumerate(AI_CHANNELS):
            y_min, y_max = AI_Y_DEFAULTS[i]
            row = QLabel(
                f"{i}.  {name}   ({ai}, {units})   Y default: {y_min} … {y_max} {units}"
            )
            row.setProperty("mono", True)
            row.setStyleSheet("font-family: 'JetBrains Mono', 'Consolas', monospace;")
            sl.addWidget(row)
        layout.addWidget(summary)
        layout.addStretch(1)
        return page

    def _build_setup_page(self) -> QWidget:
        """Setup page: save-directory picker, TTL (camera) settings."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QLabel("Setup")
        header.setStyleSheet("font-size: 14pt; font-weight: 600; color: #e7ebf0;")
        layout.addWidget(header)

        layout.addWidget(self._ctrl_panel.recording_settings)

        # TTL widget (camera trigger settings) mounts here when the Setup page shows.
        self._setup_ttl_mount = QFrame()
        tm = QVBoxLayout(self._setup_ttl_mount)
        tm.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._setup_ttl_mount)

        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------
    # Widget re-parenting helpers
    # ------------------------------------------------------------------

    def _mount_camera(self, frame: QFrame) -> None:
        """Re-parent the camera preview widget into ``frame``."""
        prev = self._camera_panel.preview_widget
        # Remove from current parent's layout, if any
        if prev.parent() is not None:
            parent_layout = prev.parent().layout()
            if parent_layout is not None:
                parent_layout.removeWidget(prev)
        # Add to new mount
        target_layout = frame.layout()
        if target_layout is None:
            target_layout = QVBoxLayout(frame)
            target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.addWidget(prev)
        prev.setVisible(True)

    def _mount_protocol_widget(self, frame: QFrame) -> None:
        w = self._ctrl_panel.protocol_widget
        if w.parent() is not None and w.parent().layout() is not None:
            w.parent().layout().removeWidget(w)
        frame.layout().addWidget(w)
        w.setVisible(True)

    def _mount_ttl(self, frame: QFrame) -> None:
        w = self._camera_panel.ttl_widget
        if w.parent() is not None and w.parent().layout() is not None:
            w.parent().layout().removeWidget(w)
        frame.layout().addWidget(w)
        w.setVisible(True)

    # ------------------------------------------------------------------
    # Page switching
    # ------------------------------------------------------------------

    def _on_page_changed(self, key: str) -> None:
        """Switch the stacked widget and re-parent shared panels as needed."""
        idx = self._page_index.get(key)
        if idx is None:
            return
        self._stack.setCurrentIndex(idx)

        if key == "acquire":
            self._mount_camera(self._acquire_camera_mount)
        elif key == "camera":
            self._mount_camera(self._camera_page_mount)
            self._mount_ttl(self._camera_page_mount)
        elif key == "setup":
            self._mount_ttl(self._setup_ttl_mount)
        elif key == "protocol":
            self._mount_protocol_widget(self._protocol_page_mount)
        else:  # channels — nothing to re-parent
            pass

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _on_mode_changed(self, mode: str) -> None:
        """Update the active acquisition mode and toggle StimulusPanel visibility."""
        self._active_mode = mode
        # StimulusPanel is continuous-only; hide it in trial mode
        self._stim_panel_wrap.setVisible(mode == "continuous")
        # Keep chrome and control-panel pills in sync (changed signal comes
        # from whichever pill the user clicked).
        self._chrome.mode_pill.set_value(mode)
        self._ctrl_panel.mode_pill.set_value(mode)

    def _on_clamp_mode_changed(self, mode: str) -> None:
        """Propagate a clamp mode change to the trace panel, channel list, and acquisition."""
        channels   = AI_CHANNELS_VC  if mode == "voltage_clamp" else AI_CHANNELS
        y_defaults = AI_Y_DEFAULTS_VC if mode == "voltage_clamp" else AI_Y_DEFAULTS
        self._trace_panel.set_clamp_mode(mode)
        for i, ((name, _, _, _, units), (y_min, y_max)) in enumerate(
            zip(channels, y_defaults)
        ):
            if i < len(self._channel_cbs):
                self._channel_cbs[i].setText(f"{name} ({units})")
            if i < len(self._y_controls):
                self._y_controls[i].update_channel(name, units, y_min, y_max)
        self._stim_panel.set_clamp_mode(mode)
        self._acq.set_clamp_mode(mode)
        self._chrome.clamp_pill.set_value(mode)
        self._ctrl_panel.clamp_pill.set_value(mode)

    # ------------------------------------------------------------------
    # Acquisition slots
    # ------------------------------------------------------------------

    def _toggle_channel(self, index: int, visible: bool) -> None:
        self._trace_panel.toggle_channel(index, visible)

    def _on_start(self) -> None:
        self._ctrl_panel.set_status("Starting acquisition…")
        try:
            if self._active_mode == "trial":
                self._trial_acq.start()
            else:
                self._acq.start()
        except Exception as exc:
            self._ctrl_panel.set_status(f"Error: {exc}")
            QMessageBox.critical(self, "Cannot Start Acquisition", str(exc))

    def _on_stop(self) -> None:
        self._ctrl_panel.set_stopping()
        self._ctrl_panel.set_status("Stopping…")
        self._chrome.status_badge.set_state("stopping")
        if self._active_mode == "trial":
            if self._trial_acq.is_protocol_active:
                self._trial_acq.cancel_protocol()
            self._trial_acq.stop()
        else:
            self._acq.stop()

    def _check_metadata(self) -> bool:
        missing = self._ctrl_panel.validate_metadata()
        if not missing:
            return True
        fields = ", ".join(missing)
        result = QMessageBox.warning(
            self,
            "Missing Metadata",
            f"The following metadata fields are empty:\n{fields}\n\n"
            "Do you want to continue anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return result == QMessageBox.StandardButton.Yes

    def _on_record(self, save_dir: str, metadata: dict) -> None:
        if not self._check_metadata():
            return
        try:
            self._acq.start_recording(save_dir, metadata)
        except Exception as exc:
            self._on_error(str(exc))

    def _on_stop_record(self) -> None:
        self._ctrl_panel.set_status("Stopping camera triggers…")
        self._acq.stop_recording()

    def _on_ttl_changed(self, frame_rate_hz: float, exposure_ms: float) -> None:
        self._acq.set_ttl_config(frame_rate_hz, exposure_ms)
        self._trial_acq.set_ttl_config(frame_rate_hz, exposure_ms)
        self._ctrl_panel.set_status(
            f"TTL updated: {frame_rate_hz:.2f} Hz, {exposure_ms:.2f} ms exposure"
        )

    # ------------------------------------------------------------------
    # Protocol builder
    # ------------------------------------------------------------------

    def _on_open_protocol_builder(self) -> None:
        if self._protocol_builder is None:
            self._protocol_builder = ProtocolBuilderDialog(self)
            self._protocol_builder.protocol_run_requested.connect(
                self._on_run_protocol
            )
        self._protocol_builder.set_save_dir(self._ctrl_panel.save_dir)
        self._protocol_builder.show()
        self._protocol_builder.raise_()
        self._protocol_builder.activateWindow()

    def _apply_channel_defs(self, mode: str) -> None:
        channel_defs = AI_CHANNELS_VC  if mode == "voltage_clamp" else AI_CHANNELS
        y_defaults   = AI_Y_DEFAULTS_VC if mode == "voltage_clamp" else AI_Y_DEFAULTS
        self._trace_panel.set_clamp_mode(mode)
        for i, ctrl in enumerate(self._y_controls):
            name, _, _, _, units = channel_defs[i]
            y_min, y_max = y_defaults[i]
            ctrl.update_channel(name, units, y_min, y_max)

    def _on_run_protocol(self, protocol_dict: dict) -> None:
        self._pending_protocol = dict(protocol_dict)
        name = protocol_dict.get("name", "protocol")
        self._ctrl_panel.enable_run_protocol_button(True)
        self._ctrl_panel.set_status(
            f"Protocol '{name}' ready — click 'Run Protocol' to start"
        )

    def _on_protocol_file_selected(self, path: str) -> None:
        import json as _json
        try:
            with open(path) as f:
                data = _json.load(f)
            protocol = protocol_from_dict(data)
            self._pending_protocol = {**data, "save_dir": self._ctrl_panel.save_dir}
            self._ctrl_panel.enable_run_protocol_button(True)
            self._ctrl_panel.set_status(
                f"Protocol '{protocol.name}' loaded — click 'Run Protocol' to start"
            )
        except Exception as exc:
            self._ctrl_panel.set_status(f"Failed to load protocol: {exc}")

    def _on_start_protocol(self) -> None:
        if self._pending_protocol is None:
            return
        if not self._check_metadata():
            return
        try:
            protocol_dict = dict(self._pending_protocol)
            save_dir = protocol_dict.pop("save_dir", self._ctrl_panel.save_dir)
            protocol = protocol_from_dict(protocol_dict)
            metadata = self._ctrl_panel.get_metadata()

            self._apply_channel_defs(protocol.clamp_mode)

            if self._active_mode == "continuous":
                if not self._acq.is_running:
                    self._acq.start()
                self._acq.start_recording(save_dir, metadata)
                self._acq.start_protocol(protocol)
                n = len(protocol.stimuli) * protocol.repeats_per_stimulus
                self._ctrl_panel.set_status(
                    f"Protocol '{protocol.name}' running (continuous) — {n} trials…"
                )
            else:
                if not self._trial_acq.is_running:
                    self._trial_acq.start()
                self._trial_acq.run_protocol(protocol, save_dir, metadata)
                n = len(protocol.stimuli) * protocol.repeats_per_stimulus
                self._ctrl_panel.set_status(
                    f"Protocol '{protocol.name}' running — {n} trials total…"
                )
            self._ctrl_panel.enable_run_protocol_button(False)
            self._ctrl_panel.enable_stop_protocol_button(True)
            self._chrome.status_badge.set_state("protocol")
        except Exception as exc:
            self._on_error(str(exc))

    def _on_stop_protocol(self) -> None:
        self._ctrl_panel.enable_stop_protocol_button(False)
        if self._active_mode == "continuous":
            self._acq.cancel_protocol()
        else:
            self._trial_acq.cancel_protocol()

    # ------------------------------------------------------------------
    # Acquisition state callbacks
    # ------------------------------------------------------------------

    def _on_acq_started(self) -> None:
        self._ctrl_panel.set_running(True)
        self._chrome.status_badge.set_state("acquiring")
        if self._active_mode == "continuous":
            self._ctrl_panel.enable_record_button(True)
            self._ctrl_panel.set_status("Acquiring — camera armed, waiting for Record")
        else:
            self._ctrl_panel.set_status(
                "Acquisition started — open Protocol Builder to run a protocol"
            )

    def _on_acq_stopped(self) -> None:
        self._ctrl_panel.set_running(False)
        self._ctrl_panel.enable_record_button(False)
        self._ctrl_panel.enable_run_protocol_button(False)
        self._ctrl_panel.enable_stop_protocol_button(False)
        self._ctrl_panel.set_status("Stopped.")
        self._chrome.status_badge.set_state("idle")

    def _on_recording_started(self, folder: Path) -> None:
        self._ctrl_panel.set_recording(True)
        self._ctrl_panel.set_status(f"Recording + camera → {folder.name}/")
        self._chrome.status_badge.set_state("recording")

    def _on_recording_stopped(self, n_samples: int) -> None:
        self._ctrl_panel.set_recording(False)
        self._ctrl_panel.set_status(
            f"Recording stopped. {n_samples:,} samples saved."
        )
        if self._acq.is_running:
            self._ctrl_panel.enable_record_button(True)
            self._chrome.status_badge.set_state("acquiring")
        else:
            self._chrome.status_badge.set_state("idle")

    # ------------------------------------------------------------------
    # Trial mode callbacks
    # ------------------------------------------------------------------

    def _on_trial_started(self, trial_idx: int, total: int) -> None:
        self._ctrl_panel.set_status(f"Trial {trial_idx + 1} / {total} running…")

    def _on_trial_finished(self, trial_idx: int, total: int) -> None:
        self._ctrl_panel.set_status(
            f"Trial {trial_idx + 1} / {total} saved. ITI…"
        )

    def _on_protocol_finished(self, path: Path) -> None:
        self._ctrl_panel.set_status(f"Protocol complete. Saved: {path.name}")
        self._ctrl_panel.enable_stop_protocol_button(False)
        self._apply_channel_defs("current_clamp")
        self._trial_acq.stop()

    def _on_continuous_protocol_finished(self) -> None:
        self._ctrl_panel.set_status("Continuous protocol complete — recording continues.")
        self._ctrl_panel.enable_stop_protocol_button(False)
        if self._pending_protocol is not None:
            self._ctrl_panel.enable_run_protocol_button(True)
        self._chrome.status_badge.set_state("recording")

    def _on_continuous_protocol_cancelled(self) -> None:
        self._ctrl_panel.set_status("Protocol cancelled — recording continues.")
        self._ctrl_panel.enable_stop_protocol_button(False)
        if self._pending_protocol is not None:
            self._ctrl_panel.enable_run_protocol_button(True)
        self._chrome.status_badge.set_state("recording")

    def _on_protocol_cancelled(self, n_completed: int) -> None:
        self._ctrl_panel.set_status(f"Protocol cancelled after {n_completed} trial(s).")
        self._ctrl_panel.enable_stop_protocol_button(False)
        self._apply_channel_defs("current_clamp")
        if self._pending_protocol is not None:
            self._ctrl_panel.enable_run_protocol_button(True)

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def _on_error(self, msg: str) -> None:
        self._ctrl_panel.set_running(False)
        self._ctrl_panel.set_status(f"Error: {msg}")
        self._chrome.status_badge.set_state("error")
        QMessageBox.critical(self, "Acquisition Error", msg)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._trial_acq.is_running:
            self._trial_acq.stop()
        if self._acq.is_running:
            self._acq.stop()
        event.accept()

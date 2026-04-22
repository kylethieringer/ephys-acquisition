"""
MainWindow — top-level Qt window that assembles all panels.

Layout::

    QMainWindow
    └── central widget (vertical)
        ├── TopChromeBar  (session label + mode/clamp pills + status badge)
        ├── QHBoxLayout
        │   ├── Sidebar (icon rail: Acquire / Protocol / Channels / Setup)
        │   └── QVBoxLayout
        │       ├── QStackedWidget
        │       │   ├── AcquirePage  (traces + camera + subject/protocol/stim)
        │       │   ├── ProtocolPage (saved-list + inline ProtocolBuilderPanel)
        │       │   ├── ChannelsPage (per-channel table)
        │       │   └── SetupPage    (2x2 card grid: DAQ / Mapping / Save / Camera)
        │       └── Recording bar (always visible, bottom)

Developer notes
---------------
Both acquisition controllers are always instantiated; only the **active** one
is started when the user clicks Start. The ring buffer from
:class:`~acquisition.continuous_mode.ContinuousAcquisition` is shared by
both modes.

The per-channel QCheckBox + ChannelYControls widgets live on the
**Channels** page (only page that owns them). ``_on_clamp_mode_changed``
mutates those widgets in place so switching pages does not lose state.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config import (
    AI_CHANNELS,
    AI_CHANNELS_VC,
    AI_Y_DEFAULTS,
    AI_Y_DEFAULTS_VC,
    AO_COMMAND_CH,
    CHUNK_SIZE,
    CTR_CHANNEL,
    CTR_OUT_TERMINAL,
    DEVICE_NAME,
    SAMPLE_RATE,
    TRACE_COLORS,
)

from acquisition.continuous_mode import ContinuousAcquisition
from acquisition.trial_mode import TrialAcquisition
from acquisition.trial_protocol import protocol_from_dict
from ui.camera_panel import CameraPanel
from ui.control_panel import ControlPanel
from ui.protocol_builder import ProtocolBuilderPanel
from ui.stimulus_panel import StimulusPanel
from ui.trace_panel import ChannelYControls, LiveTracePanel
from ui.widgets import Sidebar, TopChromeBar


SIDEBAR_ITEMS = [
    ("acquire",  "wave",   "Acquire"),
    ("protocol", "flask",  "Protocol"),
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
        self._active_mode = "continuous"
        self._pending_protocol: dict | None = None

        # --- Panels ---
        self._trace_panel = LiveTracePanel()
        self._camera_panel = CameraPanel()
        self._stim_panel = StimulusPanel()
        self._ctrl_panel = ControlPanel()
        self._protocol_builder = ProtocolBuilderPanel()

        self._channel_cbs: list[QCheckBox] = []
        self._y_controls: list[ChannelYControls] = []

        # --- Top chrome ---
        self._chrome = TopChromeBar()

        # --- Sidebar + stacked pages ---
        self._sidebar = Sidebar(SIDEBAR_ITEMS)
        self._stack = QStackedWidget()

        self._page_acquire = self._build_acquire_page()
        self._page_protocol = self._build_protocol_page()
        self._page_channels = self._build_channels_page()
        self._page_setup = self._build_setup_page()

        self._page_index = {
            "acquire":  self._stack.addWidget(self._page_acquire),
            "protocol": self._stack.addWidget(self._page_protocol),
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

        self._wire_acquisition_signals()
        self._wire_control_signals()
        self._wire_chrome_and_sidebar()
        self._wire_protocol_builder()

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _wire_acquisition_signals(self) -> None:
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
        self._sidebar.page_changed.connect(self._on_page_changed)

        self._chrome.mode_pill.changed.connect(self._ctrl_panel.mode_pill.set_value)
        self._chrome.mode_pill.changed.connect(self._ctrl_panel.mode_changed.emit)
        self._chrome.mode_pill.changed.connect(
            lambda v: self._chrome.clamp_pill.setVisible(v == "continuous")
        )
        self._chrome.clamp_pill.changed.connect(self._ctrl_panel.clamp_pill.set_value)
        self._chrome.clamp_pill.changed.connect(self._ctrl_panel.clamp_mode_changed.emit)

        self._ctrl_panel.expt_id_changed.connect(self._chrome.set_session_label)
        self._ctrl_panel.status_text_changed.connect(self._chrome.status_badge.set_text)

    def _wire_protocol_builder(self) -> None:
        self._protocol_builder.protocol_run_requested.connect(self._on_run_protocol)
        self._protocol_builder.set_save_dir(self._ctrl_panel.save_dir)

    # ------------------------------------------------------------------
    # Page builders
    # ------------------------------------------------------------------

    def _build_acquire_page(self) -> QWidget:
        """Acquire: traces (left) + camera-pinned right column (subject / protocol / stim)."""
        page = QWidget()
        page_layout = QHBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._trace_panel)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._acquire_camera_mount = QFrame()
        self._acquire_camera_mount.setFixedHeight(300)
        self._acquire_camera_mount.setFrameShape(QFrame.NoFrame)
        cm_layout = QVBoxLayout(self._acquire_camera_mount)
        cm_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._acquire_camera_mount)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        layout.addWidget(self._ctrl_panel.subject_card)
        layout.addWidget(self._ctrl_panel.protocol_widget)

        self._stim_panel_wrap = QFrame()
        sw_layout = QVBoxLayout(self._stim_panel_wrap)
        sw_layout.setContentsMargins(0, 0, 0, 0)
        sw_layout.addWidget(self._stim_panel)
        layout.addWidget(self._stim_panel_wrap)

        layout.addStretch(1)
        scroll.setWidget(container)
        right_layout.addWidget(scroll, stretch=1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 65)
        splitter.setStretchFactor(1, 35)
        splitter.setSizes([1000, 540])

        page_layout.addWidget(splitter)

        self._mount_camera(self._acquire_camera_mount)
        return page

    def _build_protocol_page(self) -> QWidget:
        """Protocol: saved-list (left) + embedded ProtocolBuilderPanel (right)."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QLabel("Protocol library")
        header.setStyleSheet("font-size: 14pt; font-weight: 600; color: #e7ebf0;")
        layout.addWidget(header)

        subtitle = QLabel(
            "Define reusable stimulus sequences. Pick one from the list or "
            "compose a new one and click 'Load Protocol' to stage it for Acquire."
        )
        subtitle.setProperty("tier", "secondary")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # --- Left: saved protocols list ---
        left = QFrame()
        left.setProperty("card", True)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(6)

        left_header = QLabel("Saved protocols")
        left_header.setProperty("tier", "tiny")
        left_layout.addWidget(left_header)

        self._proto_filter = QLineEdit()
        self._proto_filter.setPlaceholderText("Filter protocols…")
        self._proto_filter.textChanged.connect(self._refresh_protocol_list)
        left_layout.addWidget(self._proto_filter)

        self._proto_list = QListWidget()
        self._proto_list.itemDoubleClicked.connect(self._on_saved_protocol_picked)
        left_layout.addWidget(self._proto_list, stretch=1)

        btn_row = QHBoxLayout()
        self._proto_refresh_btn = QPushButton("Refresh")
        self._proto_refresh_btn.clicked.connect(self._refresh_protocol_list)
        self._proto_new_btn = QPushButton("New")
        self._proto_new_btn.setProperty("accent", "primary")
        self._proto_new_btn.clicked.connect(self._protocol_builder.clear)
        btn_row.addWidget(self._proto_refresh_btn)
        btn_row.addWidget(self._proto_new_btn)
        left_layout.addLayout(btn_row)

        splitter.addWidget(left)

        # --- Right: embedded ProtocolBuilderPanel ---
        right = QFrame()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._protocol_builder)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([320, 900])

        layout.addWidget(splitter, stretch=1)

        self._refresh_protocol_list()
        return page

    def _build_channels_page(self) -> QWidget:
        """Channels: full-page per-channel table (owns QCheckBox + ChannelYControls)."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QLabel("Channels")
        header.setStyleSheet("font-size: 14pt; font-weight: 600; color: #e7ebf0;")
        layout.addWidget(header)

        subtitle = QLabel("Display, save, and axis settings per channel.")
        subtitle.setProperty("tier", "secondary")
        layout.addWidget(subtitle)

        card = QFrame()
        card.setProperty("card", True)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        # Column widths: color | port | name/units | Y-min | Y-max | Auto | Save
        headers = ["", "Port", "Signal (units)", "Y min", "Y max", "Auto", "Save"]
        for col, text in enumerate(headers):
            lbl = QLabel(text)
            lbl.setProperty("tier", "tiny")
            grid.addWidget(lbl, 0, col)

        for i, (name, ai, _, _, units) in enumerate(AI_CHANNELS):
            row = i + 1

            swatch = QFrame()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(
                f"background-color: {TRACE_COLORS[i]}; border-radius: 3px;"
            )
            grid.addWidget(swatch, row, 0)

            port_lbl = QLabel(ai.upper())
            port_lbl.setProperty("mono", True)
            port_lbl.setStyleSheet(
                "font-family: 'JetBrains Mono', 'Consolas', monospace;"
                " color: #a4afc1; font-weight: 600;"
            )
            grid.addWidget(port_lbl, row, 1)

            cb = QCheckBox(f"{name} ({units})")
            cb.setChecked(True)
            cb.toggled.connect(lambda checked, idx=i: self._toggle_channel(idx, checked))
            self._channel_cbs.append(cb)
            grid.addWidget(cb, row, 2)

            ctrl = ChannelYControls(i, self._trace_panel.plots[i])
            ctrl.label_widget.setVisible(False)  # name shown in col 2
            self._y_controls.append(ctrl)
            grid.addWidget(ctrl.min_spin, row, 3)
            grid.addWidget(ctrl.max_spin, row, 4)
            grid.addWidget(ctrl.auto_cb, row, 5)

            save_cb = QCheckBox()
            save_cb.setChecked(True)
            save_cb.setEnabled(False)
            save_cb.setToolTip("All channels are saved by default.")
            grid.addWidget(save_cb, row, 6)

        grid.setColumnStretch(2, 1)
        card_layout.addLayout(grid)
        layout.addWidget(card)
        layout.addStretch(1)

        return page

    def _build_setup_page(self) -> QWidget:
        """Setup: 2x2 card grid — DAQ device / channel mapping / save dir / camera TTL."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QLabel("Setup")
        header.setStyleSheet("font-size: 14pt; font-weight: 600; color: #e7ebf0;")
        layout.addWidget(header)

        subtitle = QLabel("Hardware config, sample rate, channel mapping, save location.")
        subtitle.setProperty("tier", "secondary")
        layout.addWidget(subtitle)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        grid.addWidget(self._build_setup_daq_card(),     0, 0)
        grid.addWidget(self._build_setup_mapping_card(), 0, 1)
        grid.addWidget(self._build_setup_save_card(),    1, 0)
        grid.addWidget(self._build_setup_camera_card(),  1, 1)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        layout.addLayout(grid)
        layout.addStretch(1)
        return page

    def _build_setup_daq_card(self) -> QWidget:
        box = QGroupBox("DAQ Device")
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 16, 12, 12)
        v.setSpacing(6)

        device_row = QHBoxLayout()
        led = QFrame()
        led.setFixedSize(10, 10)
        led.setProperty("led", "ok")
        device_row.addWidget(led)
        dev_lbl = QLabel(DEVICE_NAME)
        dev_lbl.setProperty("mono", True)
        dev_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono', 'Consolas', monospace;"
            " font-weight: 600; color: #e7ebf0;"
        )
        device_row.addWidget(dev_lbl)
        device_row.addStretch()
        v.addLayout(device_row)

        def _field(label: str, value: str) -> QHBoxLayout:
            row = QHBoxLayout()
            l = QLabel(label)
            l.setProperty("tier", "secondary")
            l.setFixedWidth(110)
            val = QLabel(value)
            val.setProperty("mono", True)
            val.setStyleSheet(
                "font-family: 'JetBrains Mono', 'Consolas', monospace;"
            )
            row.addWidget(l)
            row.addWidget(val)
            row.addStretch()
            return row

        v.addLayout(_field("Sample rate", f"{SAMPLE_RATE} Hz"))
        v.addLayout(_field("Chunk size",  f"{CHUNK_SIZE} samples"))
        v.addLayout(_field("Counter",     f"{CTR_CHANNEL} -> {CTR_OUT_TERMINAL}"))
        v.addLayout(_field("AO command",  AO_COMMAND_CH))

        v.addStretch()
        return box

    def _build_setup_mapping_card(self) -> QWidget:
        box = QGroupBox("Channel Mapping")
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 16, 12, 12)
        v.setSpacing(6)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        for col, text in enumerate(["Port", "Signal", "Scale", "Units"]):
            lbl = QLabel(text)
            lbl.setProperty("tier", "tiny")
            grid.addWidget(lbl, 0, col)

        def _mono(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "font-family: 'JetBrains Mono', 'Consolas', monospace;"
            )
            return lbl

        row_idx = 1
        for name, ai, _, scale, units in AI_CHANNELS:
            port = _mono(ai.upper())
            port.setStyleSheet(
                "font-family: 'JetBrains Mono', 'Consolas', monospace;"
                " color: #a4afc1; font-weight: 600;"
            )
            grid.addWidget(port, row_idx, 0)
            grid.addWidget(_mono(name), row_idx, 1)
            grid.addWidget(_mono(f"×{scale:g}"), row_idx, 2)
            grid.addWidget(_mono(units), row_idx, 3)
            row_idx += 1

        # AO rows
        ao_cmd = _mono(AO_COMMAND_CH.upper())
        ao_cmd.setStyleSheet(
            "font-family: 'JetBrains Mono', 'Consolas', monospace;"
            " color: #a4afc1; font-weight: 600;"
        )
        grid.addWidget(ao_cmd, row_idx, 0)
        grid.addWidget(_mono("AmpCmd (out)"), row_idx, 1)
        grid.addWidget(_mono("-"), row_idx, 2)
        grid.addWidget(_mono("V"), row_idx, 3)
        row_idx += 1

        ctr_lbl = _mono(CTR_OUT_TERMINAL)
        ctr_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono', 'Consolas', monospace;"
            " color: #a4afc1; font-weight: 600;"
        )
        grid.addWidget(ctr_lbl, row_idx, 0)
        grid.addWidget(_mono("Camera TTL"), row_idx, 1)
        grid.addWidget(_mono("-"), row_idx, 2)
        grid.addWidget(_mono("V"), row_idx, 3)

        grid.setColumnStretch(1, 1)
        v.addLayout(grid)
        v.addStretch()
        return box

    def _build_setup_save_card(self) -> QWidget:
        """Host the existing recording-settings groupbox inside a card frame."""
        box = QGroupBox("Data Save Location")
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 16, 12, 12)
        v.addWidget(self._ctrl_panel.recording_settings)
        v.addStretch()
        return box

    def _build_setup_camera_card(self) -> QWidget:
        """Host the camera TTL widget inside a card frame."""
        self._setup_camera_card = QGroupBox("Camera")
        v = QVBoxLayout(self._setup_camera_card)
        v.setContentsMargins(12, 16, 12, 12)
        self._setup_ttl_mount = QFrame()
        tm = QVBoxLayout(self._setup_ttl_mount)
        tm.setContentsMargins(0, 0, 0, 0)
        tm.addWidget(self._camera_panel.ttl_widget)
        v.addWidget(self._setup_ttl_mount)
        v.addStretch()
        return self._setup_camera_card

    # ------------------------------------------------------------------
    # Widget re-parenting helpers
    # ------------------------------------------------------------------

    def _mount_camera(self, frame: QFrame) -> None:
        prev = self._camera_panel.preview_widget
        if prev.parent() is not None:
            parent_layout = prev.parent().layout()
            if parent_layout is not None:
                parent_layout.removeWidget(prev)
        target_layout = frame.layout()
        if target_layout is None:
            target_layout = QVBoxLayout(frame)
            target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.addWidget(prev)
        prev.setVisible(True)

    # ------------------------------------------------------------------
    # Page switching
    # ------------------------------------------------------------------

    def _on_page_changed(self, key: str) -> None:
        idx = self._page_index.get(key)
        if idx is None:
            return
        self._stack.setCurrentIndex(idx)
        # Camera preview is pinned to Acquire; no re-parenting needed
        # since we removed the Camera page.

    # ------------------------------------------------------------------
    # Protocol list (Protocol page)
    # ------------------------------------------------------------------

    def _refresh_protocol_list(self) -> None:
        filter_text = self._proto_filter.text().lower() if hasattr(self, "_proto_filter") else ""
        self._proto_list.clear()
        for stem, path in self._ctrl_panel.scan_protocols():
            if filter_text and filter_text not in stem.lower():
                continue
            item = QListWidgetItem(stem)
            item.setData(Qt.UserRole, path)
            self._proto_list.addItem(item)
        # Keep the control-panel's combobox in sync
        self._ctrl_panel._scan_protocol_folder()

    def _on_saved_protocol_picked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self._protocol_builder.load_protocol_from_file(path)

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _on_mode_changed(self, mode: str) -> None:
        self._active_mode = mode
        self._stim_panel_wrap.setVisible(mode == "continuous")
        self._chrome.mode_pill.set_value(mode)
        self._ctrl_panel.mode_pill.set_value(mode)

    def _on_clamp_mode_changed(self, mode: str) -> None:
        channels = AI_CHANNELS_VC if mode == "voltage_clamp" else AI_CHANNELS
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
        """'Open Builder' button now jumps to the Protocol sidebar tab."""
        self._protocol_builder.set_save_dir(self._ctrl_panel.save_dir)
        self._sidebar.set_current("protocol")
        self._on_page_changed("protocol")

    def _apply_channel_defs(self, mode: str) -> None:
        channel_defs = AI_CHANNELS_VC if mode == "voltage_clamp" else AI_CHANNELS
        y_defaults = AI_Y_DEFAULTS_VC if mode == "voltage_clamp" else AI_Y_DEFAULTS
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
                "Acquisition started — open Protocol tab to run a protocol"
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

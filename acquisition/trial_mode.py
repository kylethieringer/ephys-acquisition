"""
TrialAcquisition — orchestrates DAQWorker, CameraWorker, and TrialHDF5Saver
for trial-based acquisition.

Lifecycle:
    start()            → DAQ + camera workers start (same as ContinuousAcquisition)
    run_protocol(...)  → builds trial order, enters state machine
    cancel_protocol()  → graceful cancel after the current trial finishes
    stop()             → cancel any active run, then shut down workers

State machine (driven by sample counting in _on_ai_chunk, GUI thread):

    IDLE
        run_protocol() called
        ↓
    ITI  (inter-trial interval — AO waveform pre-loaded, TTL off)
        iti_samples counted
        ↓
    PRE  (TTL fires immediately at entry; counting pre-baseline samples)
        pre_samples counted
        ↓
    TRIAL (counting stim + post samples, accumulating data buffer)
        (pre + stim + post) total counted
        → TTL stops, trial data saved
        ↓
    ITI or DONE

Camera TTL is active for the FULL trial window: pre + stim + post.

All public methods are called from the GUI thread.
Data accumulation and state transitions happen in _on_ai_chunk (GUI thread
via Qt AutoConnection from DAQWorker), so no locking is needed on state.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal

from config import (
    AI_CHANNELS,
    AI_CHANNELS_VC,
    DEFAULT_EXPOSURE_MS,
    DEFAULT_FRAME_RATE_HZ,
    N_AI_CHANNELS,
    SAMPLE_RATE,
)
from acquisition.trial_protocol import (
    TrialProtocol,
    build_trial_order,
)
from acquisition.trial_saver import TrialHDF5Saver
from acquisition.trial_waveforms import build_trial_waveform
from hardware.camera_worker import CameraWorker
from hardware.daq_worker import DAQWorker


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _ms_to_samples(ms: float) -> int:
    return max(0, int(ms / 1000.0 * SAMPLE_RATE))


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

_S_IDLE  = "idle"
_S_ITI   = "iti"
_S_PRE   = "pre"
_S_TRIAL = "trial"
_S_DONE  = "done"


class TrialAcquisition(QObject):
    """
    High-level controller for trial-based acquisition.

    Signals:
        started():                workers are running
        stopped():                workers have shut down
        error_occurred(str):      forwarded from workers
        trial_started(int, int):  (trial_index, total_trials)
        trial_finished(int, int): (trial_index, total_trials)
        protocol_finished(Path):  all trials complete; path = HDF5 file
        protocol_cancelled(int):  run cancelled; int = n_trials_completed
        progress_updated(int, int): (current_trial, total_trials)
    """

    started            = Signal()
    stopped            = Signal()
    error_occurred     = Signal(str)
    trial_started      = Signal(int, int)
    trial_finished     = Signal(int, int)
    protocol_finished  = Signal(object)   # Path
    protocol_cancelled = Signal(int)
    progress_updated   = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._daq_worker:    DAQWorker    | None = None
        self._camera_worker: CameraWorker | None = None
        self._is_running = False

        # Hardware settings (mirror ContinuousAcquisition defaults)
        self._frame_rate_hz = DEFAULT_FRAME_RATE_HZ
        self._exposure_ms   = DEFAULT_EXPOSURE_MS

        # Display callbacks (wired by MainWindow)
        self._on_new_data  = None   # callable(np.ndarray)
        self._on_new_frame = None   # callable(np.ndarray)

        # Protocol run state
        self._protocol:    TrialProtocol | None = None
        self._trial_order: list[int]             = []
        self._saver        = TrialHDF5Saver()
        self._subject_metadata: dict             = {}

        # State machine
        self._state          = _S_IDLE
        self._trial_pos      = 0          # position in _trial_order
        self._sample_counter = 0          # samples counted in current state

        # Per-trial sample thresholds (set when waveform is loaded)
        self._pre_samples   = 0
        self._total_samples = 0           # pre + stim + post
        self._iti_samples   = 0

        # Data accumulation buffer for current trial
        self._trial_buf:    np.ndarray | None = None
        self._buf_ptr       = 0

        # Graceful cancel flag
        self._cancel_requested = False

    # ------------------------------------------------------------------
    # Property accessors
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def is_protocol_active(self) -> bool:
        return self._state not in (_S_IDLE, _S_DONE)

    # ------------------------------------------------------------------
    # Connect display callbacks
    # ------------------------------------------------------------------

    def connect_data_callback(self, slot) -> None:
        self._on_new_data = slot

    def connect_frame_callback(self, slot) -> None:
        self._on_new_frame = slot

    # ------------------------------------------------------------------
    # Camera TTL settings (forwarded from MainWindow/CameraPanel)
    # ------------------------------------------------------------------

    def set_ttl_config(self, frame_rate_hz: float, exposure_ms: float) -> None:
        self._frame_rate_hz = frame_rate_hz
        self._exposure_ms   = exposure_ms
        if self._daq_worker is not None:
            self._daq_worker.set_ttl_config(frame_rate_hz, exposure_ms)

    # ------------------------------------------------------------------
    # Acquisition lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start DAQ + camera workers (mirrors ContinuousAcquisition.start)."""
        if self._is_running:
            return

        self._daq_worker = DAQWorker(self._frame_rate_hz, self._exposure_ms)
        self._daq_worker.data_ready.connect(self._on_ai_chunk)
        self._daq_worker.error_occurred.connect(self._handle_error)
        self._daq_worker.start()

        self._camera_worker = CameraWorker(self._exposure_ms)
        self._camera_worker.frame_ready.connect(self._on_camera_frame)
        self._camera_worker.error_occurred.connect(self._handle_camera_error)
        self._camera_worker.start()

        self._is_running = True
        self.started.emit()

    def stop(self) -> None:
        """Cancel any running protocol and stop hardware."""
        if not self._is_running:
            return

        # Force-close any open recording
        if self._saver.is_open:
            self._saver.close()
        self._state = _S_IDLE

        # Ensure TTL is off
        if self._daq_worker is not None:
            self._daq_worker.stop_ttl()
            self._daq_worker.clear_stimulus_waveform()

        self._teardown_camera()

        if self._daq_worker is not None:
            self._daq_worker.stop()
            self._daq_worker.wait(5000)
            self._daq_worker = None

        self._is_running = False
        self.stopped.emit()

    # ------------------------------------------------------------------
    # Protocol control
    # ------------------------------------------------------------------

    def run_protocol(
        self,
        protocol: TrialProtocol,
        save_dir: str | Path,
        subject_metadata: dict,
    ) -> None:
        """
        Start a protocol run.  Workers must be started first via start().
        """
        if not self._is_running or not protocol.stimuli:
            return

        self._protocol         = protocol
        self._subject_metadata = subject_metadata
        self._trial_order      = build_trial_order(protocol)
        self._cancel_requested = False
        self._trial_pos        = 0

        # Choose channel definitions based on clamp mode
        channel_defs = (
            AI_CHANNELS_VC
            if protocol.clamp_mode == "voltage_clamp"
            else AI_CHANNELS
        )

        # Open HDF5 file
        self._saver.open(
            save_dir          = save_dir,
            protocol          = protocol,
            trial_order       = self._trial_order,
            subject_metadata  = subject_metadata,
            channel_defs      = channel_defs,
        )

        self._iti_samples = _ms_to_samples(protocol.iti_ms)

        # Enter ITI state: pre-load waveform for first trial, then count ITI
        self._enter_iti()

    def cancel_protocol(self) -> None:
        """
        Request a graceful cancel.  The current trial (if any) finishes
        saving before the run stops.  No data is lost.
        """
        self._cancel_requested = True

    # ------------------------------------------------------------------
    # State machine — all called from GUI thread via _on_ai_chunk
    # ------------------------------------------------------------------

    def _enter_iti(self) -> None:
        """Start counting the inter-trial interval. AO remains at zero (silent)."""
        self._state          = _S_ITI
        self._sample_counter = 0

    def _enter_pre(self) -> None:
        """Start the pre-baseline window: fire TTL, begin accumulating data."""
        stim_idx = self._trial_order[self._trial_pos]
        stim_def = self._protocol.stimuli[stim_idx]

        # Build waveform: [zeros×pre | hyperpol | staircase | zeros×post]
        waveform = build_trial_waveform(stim_def, self._protocol)
        self._total_samples = len(waveform)
        self._pre_samples   = _ms_to_samples(self._protocol.pre_ms)

        # Allocate trial data buffer
        self._trial_buf = np.empty(
            (N_AI_CHANNELS, self._total_samples), dtype=np.float64
        )
        self._buf_ptr = 0

        if self._daq_worker is not None:
            # Load waveform before TTL so AO is ready when camera starts.
            # The waveform begins with pre_ms of zeros so the NI task rebuild
            # (~5-10 ms) completes well before any current is output.
            self._daq_worker.set_stimulus_waveform(waveform.reshape(1, -1))
            # TTL fires at trial start (covers pre-baseline + stim + post)
            self._daq_worker.start_ttl()

        self._state          = _S_PRE
        self._sample_counter = 0

        total = len(self._trial_order)
        self.trial_started.emit(self._trial_pos, total)
        self.progress_updated.emit(self._trial_pos, total)

    def _on_trial_window_start(self) -> None:
        """PRE threshold reached — transition to TRIAL state (no hardware change)."""
        self._state          = _S_TRIAL
        self._sample_counter = 0

    def _on_trial_end(self) -> None:
        """TRIAL+POST threshold reached — stop TTL, save data, advance."""
        if self._daq_worker is not None:
            self._daq_worker.stop_ttl()
            self._daq_worker.clear_stimulus_waveform()

        # Save trial data
        stim_idx = self._trial_order[self._trial_pos]
        stim_def = self._protocol.stimuli[stim_idx]
        onset_iso = datetime.datetime.now().isoformat()

        self._saver.begin_trial(
            trial_index    = self._trial_pos,
            stimulus_index = stim_idx,
            stimulus_name  = stim_def.name,
            onset_time     = onset_iso,
            n_samples      = self._total_samples,
        )
        if self._trial_buf is not None:
            self._saver.write_trial(self._trial_pos, self._trial_buf)

        total = len(self._trial_order)
        self.trial_finished.emit(self._trial_pos, total)
        self._trial_pos += 1

        self._advance()

    def _advance(self) -> None:
        """Move to next trial or finish the run."""
        total = len(self._trial_order)

        if self._cancel_requested or self._trial_pos >= total:
            # Done
            self._state = _S_DONE
            path = self._saver.close()
            if self._cancel_requested:
                self.protocol_cancelled.emit(self._trial_pos)
            else:
                if path is not None:
                    self.protocol_finished.emit(path)
                else:
                    self.protocol_finished.emit(Path())
        else:
            self._enter_iti()

    # ------------------------------------------------------------------
    # Data ingestion — GUI thread (Qt AutoConnection)
    # ------------------------------------------------------------------

    def _on_ai_chunk(self, chunk: np.ndarray) -> None:
        """
        Called ~100 Hz with each 200-sample AI chunk.
        Drives the state machine via sample counting.
        Simultaneously feeds the display ring buffer.
        """
        # Push to display regardless of state
        if self._on_new_data is not None:
            self._on_new_data(chunk)

        if self._state == _S_IDLE or self._state == _S_DONE:
            return

        n = chunk.shape[1]

        if self._state in (_S_PRE, _S_TRIAL):
            # Accumulate data into trial buffer
            if self._trial_buf is not None:
                space = self._total_samples - self._buf_ptr
                take  = min(n, space)
                if take > 0:
                    self._trial_buf[:, self._buf_ptr : self._buf_ptr + take] = chunk[:, :take]
                    self._buf_ptr += take

        self._sample_counter += n
        self._check_state_transition()

    def _check_state_transition(self) -> None:
        """Evaluate whether a state boundary has been crossed."""
        if self._state == _S_ITI:
            if self._sample_counter >= self._iti_samples:
                self._sample_counter -= self._iti_samples
                self._enter_pre()

        elif self._state == _S_PRE:
            if self._sample_counter >= self._pre_samples:
                self._sample_counter -= self._pre_samples
                self._on_trial_window_start()

        elif self._state == _S_TRIAL:
            stim_post = self._total_samples - self._pre_samples
            if self._sample_counter >= stim_post:
                self._sample_counter -= stim_post
                self._on_trial_end()

    # ------------------------------------------------------------------
    # Camera frames
    # ------------------------------------------------------------------

    def _on_camera_frame(self, frame: np.ndarray) -> None:
        if self._on_new_frame is not None:
            self._on_new_frame(frame)

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def _handle_error(self, msg: str) -> None:
        if self._saver.is_open:
            self._saver.close()
        self._state = _S_IDLE
        self._teardown_camera()
        if self._daq_worker is not None:
            self._daq_worker.stop()
            self._daq_worker.wait(5000)
            self._daq_worker = None
        self._is_running = False
        self.error_occurred.emit(f"DAQ error: {msg}")

    def _handle_camera_error(self, msg: str) -> None:
        self.error_occurred.emit(f"Camera error: {msg}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _teardown_camera(self) -> None:
        if self._camera_worker is not None:
            self._camera_worker.stop()
            self._camera_worker.wait(3000)
            self._camera_worker = None

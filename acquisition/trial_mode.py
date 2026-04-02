"""
TrialAcquisition — orchestrates DAQWorker, CameraWorker, and TrialSaver
for trial-based acquisition.

Lifecycle::

    start()            → DAQ + camera workers start (same as ContinuousAcquisition)
    run_protocol(...)  → builds trial order, enters state machine
    cancel_protocol()  → graceful cancel after the current trial finishes
    stop()             → cancel any active run, then shut down workers

State machine
-------------
The state machine advances by counting AI samples.  It runs entirely in the
GUI thread via the ``_on_ai_chunk`` Qt slot (connected with ``AutoConnection``
from DAQWorker's ``data_ready`` signal), so no locking is needed on state
variables.

States and transitions::

    _S_IDLE
        │  run_protocol() called
        ▼
    _S_ITI  (inter-trial interval — AO silent, TTL off)
        │  iti_samples counted
        ▼
    _S_PRE  (pre-baseline — TTL fires, data accumulates)
        │  pre_samples counted
        ▼
    _S_TRIAL  (stimulus + post — data accumulates)
        │  (total_samples − pre_samples) counted
        │  → TTL stops, trial saved
        ▼
    _S_ITI  ← repeat until all trials done, or cancel requested
        │  all trials done / cancel
        ▼
    _S_DONE

Camera TTL is active for the **full** trial window: pre + stim + post.
The AO waveform is pre-loaded during the ITI so the task rebuild (~5–10 ms)
completes well before any current is output.

Developer notes
---------------
All public methods are called from the GUI thread.  Data accumulation and
state transitions happen in ``_on_ai_chunk`` (GUI thread), so no locking
is required for state variables.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QObject, Signal

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from config import (
    AI_CHANNELS,
    AI_CHANNELS_VC,
    DEFAULT_EXPOSURE_MS,
    DEFAULT_FRAME_RATE_HZ,
    N_AI_CHANNELS,
    SAMPLE_RATE,
)
from utils.stimulus_generator import get_actual_frame_rate
from acquisition.trial_protocol import (
    TrialProtocol,
    build_trial_order,
)
from acquisition.trial_saver import TrialSaver
from acquisition.trial_waveforms import build_trial_waveform
from hardware.camera_worker import CameraWorker
from hardware.daq_worker import DAQWorker


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _ms_to_samples(ms: float) -> int:
    """Convert a duration in ms to an integer sample count at :data:`~config.SAMPLE_RATE`.

    Args:
        ms: Duration in ms.

    Returns:
        Sample count, clamped to a minimum of 0.
    """
    return max(0, int(ms / 1000.0 * SAMPLE_RATE))


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

_S_IDLE  = "idle"
"""No protocol is active.  Workers may or may not be running."""

_S_ITI   = "iti"
"""Inter-trial interval.  AO is silent; TTL is off; counting ITI samples."""

_S_PRE   = "pre"
"""Pre-stimulus baseline.  TTL fires; data is accumulating into the trial buffer."""

_S_TRIAL = "trial"
"""Stimulus + post-stimulus.  Continuing to accumulate; AO waveform active."""

_S_DONE  = "done"
"""Protocol finished or cancelled.  HDF5 file has been closed."""


class TrialAcquisition(QObject):
    """High-level controller for trial-based acquisition.

    Owns and coordinates a :class:`~hardware.daq_worker.DAQWorker`,
    :class:`~hardware.camera_worker.CameraWorker`, and
    :class:`~acquisition.trial_saver.TrialSaver`.

    Signals:
        started(): Emitted when the DAQ and camera workers are running.
        stopped(): Emitted after all workers have shut down.
        error_occurred(str): Forwarded from workers.
        trial_started(int, int): Emitted at the beginning of each trial.
            Arguments are ``(trial_index, total_trials)`` (0-based index).
        trial_finished(int, int): Emitted after each trial's data is saved.
            Arguments are ``(trial_index, total_trials)``.
        protocol_finished(object): Emitted when all trials complete.
            Argument is a ``pathlib.Path`` to the HDF5 file.
        protocol_cancelled(int): Emitted when :meth:`cancel_protocol` is
            honoured.  Argument is the number of trials completed before
            cancellation.
        progress_updated(int, int): Emitted at the start of each trial with
            ``(current_trial_pos, total_trials)`` for UI progress displays.

    Attributes:
        _daq_worker (DAQWorker | None): AI/AO worker thread.
        _camera_worker (CameraWorker | None): Camera capture thread.
        _is_running (bool): Workers are running.
        _frame_rate_hz (float): Active camera frame rate in Hz.
        _exposure_ms (float): Active camera exposure in ms.
        _on_new_data: Display callback for AI chunks.
        _on_new_frame: Display callback for camera frames.
        _protocol (TrialProtocol | None): Active protocol.
        _trial_order (list[int]): Shuffled stimulus index sequence.
        _saver (TrialSaver): Trial data saver.
        _subject_metadata (dict): Subject info for the HDF5 file.
        _state (str): Current state machine state (one of ``_S_*`` constants).
        _trial_pos (int): Current position in ``_trial_order`` (0-based).
        _sample_counter (int): Samples counted in the current state.
        _pre_samples (int): Threshold sample count for the PRE → TRIAL transition.
        _total_samples (int): Total waveform length in samples (pre + stim + post).
        _iti_samples (int): Threshold sample count for the ITI → PRE transition.
        _trial_buf (NDArray | None): Pre-allocated accumulation buffer for
            the current trial, shape ``(N_AI_CHANNELS, total_samples)``.
        _buf_ptr (int): Write pointer into ``_trial_buf``.
        _cancel_requested (bool): ``True`` after :meth:`cancel_protocol` is called.
    """

    started            = Signal()
    stopped            = Signal()
    error_occurred     = Signal(str)
    trial_started      = Signal(int, int)
    trial_finished     = Signal(int, int)
    protocol_finished  = Signal(object)   # Path
    protocol_cancelled = Signal(int)
    progress_updated   = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._daq_worker:    DAQWorker    | None = None
        self._camera_worker: CameraWorker | None = None
        self._is_running = False

        self._frame_rate_hz = DEFAULT_FRAME_RATE_HZ
        self._exposure_ms   = DEFAULT_EXPOSURE_MS

        self._on_new_data  = None
        self._on_new_frame = None

        self._protocol:         TrialProtocol | None = None
        self._trial_order:      list[int]             = []
        self._saver             = TrialSaver()
        self._subject_metadata: dict                  = {}

        self._state:          str                = _S_IDLE
        self._trial_pos:      int                = 0
        self._sample_counter: int                = 0

        self._pre_samples:   int = 0
        self._total_samples: int = 0
        self._iti_samples:   int = 0

        self._trial_buf: NDArray[np.float64] | None = None
        self._buf_ptr:   int                        = 0

        self._cancel_requested = False

        self._video_writer = None
        self._video_path:   Path | None = None
        self._video_folder: Path | None = None

        self._metadata_path: Path | None = None
        self._metadata:      dict | None = None

    # ------------------------------------------------------------------
    # Property accessors
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """``True`` if the DAQ and camera workers are running."""
        return self._is_running

    @property
    def is_protocol_active(self) -> bool:
        """``True`` if a protocol run is in progress (not in IDLE or DONE)."""
        return self._state not in (_S_IDLE, _S_DONE)

    # ------------------------------------------------------------------
    # Connect display callbacks
    # ------------------------------------------------------------------

    def connect_data_callback(self, slot) -> None:
        """Register a callback to receive each AI chunk for the trace panel.

        Args:
            slot: Callable accepting a ``(N_AI_CHANNELS, CHUNK_SIZE)``
                float64 array in Volts.
        """
        self._on_new_data = slot

    def connect_frame_callback(self, slot) -> None:
        """Register a callback to receive each camera frame for preview.

        Args:
            slot: Callable accepting a ``(H, W)`` or ``(H, W, 3)`` array.
        """
        self._on_new_frame = slot

    # ------------------------------------------------------------------
    # Camera TTL settings
    # ------------------------------------------------------------------

    def set_ttl_config(self, frame_rate_hz: float, exposure_ms: float) -> None:
        """Update the camera TTL frame rate and exposure.

        Stores the new values and forwards them to the DAQ worker if running.

        Args:
            frame_rate_hz: New camera frame rate in Hz.
            exposure_ms: New camera exposure duration in ms.
        """
        self._frame_rate_hz = frame_rate_hz
        self._exposure_ms   = exposure_ms
        if self._daq_worker is not None:
            self._daq_worker.set_ttl_config(frame_rate_hz, exposure_ms)

    # ------------------------------------------------------------------
    # Acquisition lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the DAQ and camera workers.

        Mirrors :meth:`~acquisition.continuous_mode.ContinuousAcquisition.start`.
        No-op if already running.
        """
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
        """Cancel any running protocol and stop all hardware workers.

        Force-closes the HDF5 file if open, ensures the TTL is off, then
        tears down the camera and DAQ worker threads.  No-op if not running.
        """
        if not self._is_running:
            return

        if self._video_writer is not None:
            self._video_writer.release()
            self._video_writer = None

        self._finalise_trial_metadata_json(self._trial_pos)

        if self._saver.is_open:
            self._saver.close()
        self._state = _S_IDLE

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
        """Start a protocol run.

        Creates the HDF5 file, builds the shuffled trial order, and enters
        the state machine at the ITI state.

        Workers must be started first via :meth:`start`.  No-op if not
        running or if ``protocol.stimuli`` is empty.

        Args:
            protocol: :class:`~acquisition.trial_protocol.TrialProtocol`
                defining stimuli, timing, and clamp mode.
            save_dir: Root directory for saving the HDF5 file.
            subject_metadata: Subject information dict (same keys as
                :meth:`~acquisition.data_saver.ContinuousSaver.open`).
        """
        if not self._is_running or not protocol.stimuli:
            return

        self._protocol         = protocol
        self._subject_metadata = subject_metadata
        self._trial_order      = build_trial_order(protocol)
        self._cancel_requested = False
        self._trial_pos        = 0

        channel_defs = (
            AI_CHANNELS_VC
            if protocol.clamp_mode == "voltage_clamp"
            else AI_CHANNELS
        )

        self._saver.open(
            save_dir          = save_dir,
            protocol          = protocol,
            trial_order       = self._trial_order,
            subject_metadata  = subject_metadata,
            channel_defs      = channel_defs,
        )
        self._video_folder = self._saver.folder
        self._write_trial_metadata_json(protocol, channel_defs)

        self._iti_samples = _ms_to_samples(protocol.iti_ms)
        self._enter_iti()

    def cancel_protocol(self) -> None:
        """Request a graceful cancel of the running protocol.

        The current trial (if any) finishes saving before the run stops.
        No data from completed trials is lost.  ``protocol_cancelled`` is
        emitted with the count of completed trials.
        """
        self._cancel_requested = True

    # ------------------------------------------------------------------
    # State machine — all called from GUI thread via _on_ai_chunk
    # ------------------------------------------------------------------

    def _enter_iti(self) -> None:
        """Enter the ITI state: AO silent, TTL off, counting ITI samples.

        Does not issue any hardware commands — the TTL and AO are already
        off from the previous trial end (or from startup).
        """
        self._state          = _S_ITI
        self._sample_counter = 0

    def _enter_pre(self) -> None:
        """Enter the PRE state: load waveform, fire TTL, start accumulating data.

        Sequence:

        1. Look up the next stimulus in ``_trial_order``.
        2. Build the full AO waveform (pre zeros + hyperpol + staircase + post zeros).
        3. Pre-allocate the trial data buffer.
        4. Load the waveform into the AO task (rebuilds the nidaqmx task).
        5. Fire the camera TTL.
        6. Reset the sample counter and emit ``trial_started``.

        The waveform is loaded *before* the TTL fires so the nidaqmx task
        rebuild (~5–10 ms) completes before the camera starts capturing.
        """
        stim_idx = self._trial_order[self._trial_pos]
        stim_def = self._protocol.stimuli[stim_idx]

        waveform = build_trial_waveform(stim_def, self._protocol)
        self._total_samples = len(waveform)
        self._pre_samples   = _ms_to_samples(self._protocol.pre_ms)

        self._trial_buf = np.empty(
            (N_AI_CHANNELS, self._total_samples), dtype=np.float64
        )
        self._buf_ptr = 0

        # Prepare video writer for this trial (opened lazily on first frame)
        self._video_writer = None
        if self._video_folder is not None and self._saver.path is not None:
            self._video_path = self._video_folder / f"{self._saver.path.stem}_{self._trial_pos + 1:03d}.avi"
        else:
            self._video_path = None

        if self._daq_worker is not None:
            # Baseline trials keep AO at 0 V — no waveform needed (AO is already
            # silent from the previous trial's clear_stimulus_waveform call, or
            # from the initial zero-output at DAQ startup).  Rebuilding the AO
            # task with a zeros array is unnecessary and can interfere with the
            # TTL counter start, so we skip it for baseline.
            if stim_def.type != "baseline":
                self._daq_worker.set_stimulus_waveform(waveform.reshape(1, -1))
            self._daq_worker.start_ttl()

        self._state          = _S_PRE
        self._sample_counter = 0

        total = len(self._trial_order)
        self.trial_started.emit(self._trial_pos, total)
        self.progress_updated.emit(self._trial_pos, total)

    def _on_trial_window_start(self) -> None:
        """Transition from PRE to TRIAL state (no hardware change).

        Called when the pre-baseline sample threshold is reached.
        Resets the counter so ``_check_state_transition`` can measure the
        remaining (stim + post) samples.
        """
        self._state          = _S_TRIAL
        self._sample_counter = 0

    def _on_trial_end(self) -> None:
        """Handle the end of a trial: stop TTL, save data, advance the state machine.

        Sequence:

        1. Stop the camera TTL.
        2. Clear the AO waveform (revert ao0 to zeros).
        3. Pre-create the HDF5 group (``begin_trial``).
        4. Write the accumulated buffer (``write_trial``).
        5. Emit ``trial_finished``.
        6. Increment ``_trial_pos`` and call :meth:`_advance`.
        """
        if self._daq_worker is not None:
            self._daq_worker.stop_ttl()
            self._daq_worker.clear_stimulus_waveform()

        # Close the video writer for this trial before saving HDF5 metadata
        if self._video_writer is not None:
            self._video_writer.release()
            self._video_writer = None

        stim_idx = self._trial_order[self._trial_pos]
        stim_def = self._protocol.stimuli[stim_idx]
        onset_iso = datetime.datetime.now().isoformat()

        video_filename = self._video_path.name if self._video_path is not None else ""
        if self._trial_buf is not None:
            self._saver.write_trial(
                trial_index    = self._trial_pos,
                stimulus_index = stim_idx,
                stimulus_name  = stim_def.name,
                onset_time     = onset_iso,
                data           = self._trial_buf,
                video_filename = video_filename,
            )

        total = len(self._trial_order)
        self.trial_finished.emit(self._trial_pos, total)
        self._trial_pos += 1

        self._advance()

    def _advance(self) -> None:
        """Move to the next trial or finish the run.

        If a cancel was requested or all trials are done, closes the HDF5
        file and emits the appropriate completion signal.  Otherwise enters
        the next ITI.
        """
        total = len(self._trial_order)

        if self._cancel_requested or self._trial_pos >= total:
            self._state = _S_DONE
            self._finalise_trial_metadata_json(self._trial_pos)
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

    def _on_ai_chunk(self, chunk: NDArray[np.float64]) -> None:
        """Receive an AI data chunk and drive the state machine (~100 Hz).

        Forwards the chunk to the display callback unconditionally, then
        accumulates data into the trial buffer if in PRE or TRIAL state,
        and calls :meth:`_check_state_transition` to advance the state.

        Args:
            chunk: ``(N_AI_CHANNELS, CHUNK_SIZE)`` float64 array in Volts.
        """
        if self._on_new_data is not None:
            self._on_new_data(chunk)

        if self._state == _S_IDLE or self._state == _S_DONE:
            return

        n = chunk.shape[1]

        if self._state in (_S_PRE, _S_TRIAL):
            if self._trial_buf is not None:
                space = self._total_samples - self._buf_ptr
                take  = min(n, space)
                if take > 0:
                    self._trial_buf[:, self._buf_ptr : self._buf_ptr + take] = chunk[:, :take]
                    self._buf_ptr += take

        self._sample_counter += n
        self._check_state_transition()

    def _check_state_transition(self) -> None:
        """Evaluate whether a state boundary has been crossed.

        Checks if the current sample counter has reached the threshold for
        the current state and calls the appropriate transition method.
        The counter is decremented by the threshold (not zeroed) to preserve
        any overshoot for the next state.
        """
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

    def _on_camera_frame(self, frame: NDArray) -> None:
        """Write camera frames to the trial video file and forward for display.

        Frames are written only during PRE and TRIAL states (i.e. while the
        TTL is active and data is accumulating).  The video writer is opened
        lazily on the first frame of each trial.

        Args:
            frame: ``(H, W)`` or ``(H, W, 3)`` camera frame array.
        """
        if self._state in (_S_PRE, _S_TRIAL):
            self._write_video_frame(frame)
        if self._on_new_frame is not None:
            self._on_new_frame(frame)

    def _write_video_frame(self, frame: NDArray) -> None:
        """Lazily open a cv2.VideoWriter on the first frame of a trial, then write.

        ``uint16`` frames are downsampled to ``uint8`` by right-shifting 8 bits.
        Colour frames are converted from RGB (Basler) to BGR (OpenCV).

        Args:
            frame: Camera frame array from the CameraWorker.
        """
        if not HAS_CV2 or self._video_path is None:
            return

        if self._video_writer is None:
            h, w = frame.shape[:2]
            is_color = frame.ndim == 3
            fps = get_actual_frame_rate(self._frame_rate_hz)
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            self._video_writer = cv2.VideoWriter(
                str(self._video_path), fourcc, fps, (w, h), isColor=is_color
            )

        if not self._video_writer.isOpened():
            return

        if frame.dtype == np.uint16:
            frame = (frame >> 8).astype(np.uint8)
        elif frame.dtype != np.uint8:
            frame = frame.astype(np.uint8)

        if frame.ndim == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        self._video_writer.write(frame)

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def _handle_error(self, msg: str) -> None:
        """Handle a fatal DAQ error during a trial run.

        Closes the HDF5 file (partial data is preserved), resets the state
        machine, and tears down all workers.

        Args:
            msg: Human-readable error message from the DAQ worker.
        """
        if self._video_writer is not None:
            self._video_writer.release()
            self._video_writer = None

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
        """Forward a non-fatal camera error to the UI.

        Args:
            msg: Human-readable error message from the camera worker.
        """
        self.error_occurred.emit(f"Camera error: {msg}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Metadata JSON sidecar
    # ------------------------------------------------------------------

    def _write_trial_metadata_json(
        self,
        protocol: TrialProtocol,
        channel_defs: list,
    ) -> None:
        """Write the initial ``metadata.json`` sidecar for a trial protocol run.

        Called once at the start of each protocol run.  ``end_time`` and
        ``n_trials_completed`` are ``null`` and filled in by
        :meth:`_finalise_trial_metadata_json` when the run ends.

        Args:
            protocol: The active :class:`~acquisition.trial_protocol.TrialProtocol`.
            channel_defs: Channel definition list (CC or VC) used for this run.
        """
        if self._video_folder is None or self._saver.path is None:
            return

        self._metadata_path = self._video_folder / (self._saver.path.stem + "_metadata.json")

        n_total = len(self._trial_order)
        self._metadata = {
            "subject": self._subject_metadata,
            "protocol": {
                "name":                 protocol.name,
                "clamp_mode":           protocol.clamp_mode,
                "pre_ms":               protocol.pre_ms,
                "post_ms":              protocol.post_ms,
                "iti_ms":               protocol.iti_ms,
                "repeats_per_stimulus": protocol.repeats_per_stimulus,
                "n_stimuli":            len(protocol.stimuli),
                "n_trials_total":       n_total,
            },
            "start_time":        datetime.datetime.now().isoformat(),
            "end_time":          None,
            "n_trials_completed": None,
            "sample_rate_hz":    SAMPLE_RATE,
            "channels": [
                {
                    "name":             ch[0],
                    "ni_channel":       ch[1],
                    "terminal_config":  ch[2],
                    "display_scale":    ch[3],
                    "units":            ch[4],
                }
                for ch in channel_defs
            ],
            "camera": {
                "frame_rate_hz": self._frame_rate_hz,
                "exposure_ms":   self._exposure_ms,
            },
            "files": {
                "ephys_h5":  self._saver.path.name,
                "ephys_bin": self._saver.path.with_suffix(".bin").name,
                "videos":    [f"{self._saver.path.stem}_{i + 1:03d}.avi" for i in range(n_total)],
            },
        }
        self._metadata_path.write_text(json.dumps(self._metadata, indent=2))

    def _finalise_trial_metadata_json(self, n_completed: int) -> None:
        """Update ``metadata.json`` with the run end time and completed trial count.

        Called just before the HDF5 file is closed at the end of a run
        (whether completed or cancelled).

        Args:
            n_completed: Number of trials that were saved before the run ended.
        """
        if self._metadata is None or self._metadata_path is None:
            return
        self._metadata["end_time"]           = datetime.datetime.now().isoformat()
        self._metadata["n_trials_completed"] = n_completed
        self._metadata_path.write_text(json.dumps(self._metadata, indent=2))
        self._metadata      = None
        self._metadata_path = None

    def _teardown_camera(self) -> None:
        """Stop and destroy the camera worker, blocking up to 3 seconds."""
        if self._camera_worker is not None:
            self._camera_worker.stop()
            self._camera_worker.wait(3000)
            self._camera_worker = None

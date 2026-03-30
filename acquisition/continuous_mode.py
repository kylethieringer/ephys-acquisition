"""
ContinuousAcquisition — orchestrates DAQWorker, CameraWorker, RingBuffer,
and HDF5Saver for the continuous acquisition mode.

Lifecycle:
    Start  → DAQ AI begins (traces visible), AO silent, no camera
    Record → TTL enabled, camera starts after guard delay, HDF5 opened
    Stop Recording → TTL suppressed, camera stopped, HDF5 closed after guard
    Stop   → DAQ shut down

All public methods are called from the GUI thread.
"""

from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from config import CAMERA_GUARD_DELAY_MS, DEFAULT_EXPOSURE_MS, DEFAULT_FRAME_RATE_HZ
from acquisition.data_buffer import RingBuffer
from acquisition.data_saver import HDF5Saver
from hardware.daq_worker import DAQWorker
from hardware.camera_worker import CameraWorker


class ContinuousAcquisition(QObject):
    """
    High-level controller for continuous acquisition.

    Signals:
        started():            emitted when DAQ worker is running
        stopped():            emitted after DAQ worker has cleanly stopped
        error_occurred(str):  forwarded from workers
        recording_started(Path):  emitted when camera is running and HDF5 is open
        recording_stopped(int):   emitted with total samples saved
    """

    started            = Signal()
    stopped            = Signal()
    error_occurred     = Signal(str)
    recording_started  = Signal(object)   # Path
    recording_stopped  = Signal(int)      # n_samples_saved

    def __init__(self, parent=None):
        super().__init__(parent)

        self._ring_buffer     = RingBuffer()
        self._saver           = HDF5Saver()
        self._daq_worker:    DAQWorker    | None = None
        self._camera_worker: CameraWorker | None = None
        self._is_running      = False
        self._is_recording    = False
        self._is_stopping     = False   # True during post-trigger guard delay

        # Pending recording params (set before guard delay, consumed after)
        self._pending_save_dir: str | None = None
        self._pending_prefix:   str | None = None

        # Current hardware settings (kept here so we can re-apply on restart)
        self._frame_rate_hz = DEFAULT_FRAME_RATE_HZ
        self._exposure_ms   = DEFAULT_EXPOSURE_MS

        # Callbacks registered by UI panels
        self._on_new_data   = None   # callable(np.ndarray) for ring buffer push + display
        self._on_new_frame  = None   # callable(np.ndarray) for camera display

    # ------------------------------------------------------------------
    # Property accessors
    # ------------------------------------------------------------------

    @property
    def ring_buffer(self) -> RingBuffer:
        return self._ring_buffer

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    # ------------------------------------------------------------------
    # Connect UI callbacks
    # ------------------------------------------------------------------

    def connect_data_callback(self, slot) -> None:
        """Slot receives raw AI chunk (np.ndarray shape=(N_AI, CHUNK_SIZE))."""
        self._on_new_data = slot

    def connect_frame_callback(self, slot) -> None:
        """Slot receives camera frame (np.ndarray HxW or HxWx3)."""
        self._on_new_frame = slot

    # ------------------------------------------------------------------
    # Acquisition control (DAQ only — no camera, no TTL)
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._is_running or self._is_stopping:
            return

        self._ring_buffer.reset()

        # Start DAQ worker with AO silent (no TTL, no command current)
        self._daq_worker = DAQWorker(self._frame_rate_hz, self._exposure_ms)
        self._daq_worker.data_ready.connect(self._on_ai_chunk)
        self._daq_worker.error_occurred.connect(self._handle_error)
        self._daq_worker.start()

        self._is_running = True
        self.started.emit()

    def stop(self) -> None:
        if not self._is_running:
            return

        # If recording, stop recording first (synchronous camera teardown,
        # but skip the guard delay since we're shutting everything down)
        if self._is_recording:
            self._teardown_camera()
            self._close_recording()

        self._is_stopping = True

        if self._daq_worker is not None:
            self._daq_worker.stop()
            self._daq_worker.wait(5000)
            self._daq_worker = None

        self._is_running  = False
        self._is_stopping = False
        self.stopped.emit()

    # ------------------------------------------------------------------
    # Recording control (TTL + camera + HDF5)
    # ------------------------------------------------------------------

    def start_recording(self, save_dir: str | Path, prefix: str = "ephys") -> None:
        if self._is_recording or not self._is_running:
            return

        # Open HDF5 file immediately so data is captured from the start
        path = self._saver.open(save_dir, prefix)
        self._is_recording = True

        # Enable TTL on AO1 (pre-trigger guard: TTL runs for CAMERA_GUARD_DELAY_MS
        # before the camera starts grabbing, ensuring a clean baseline)
        if self._daq_worker is not None:
            self._daq_worker.clear_stimulus_waveform()

        # After guard delay, start camera
        self._pending_recording_path = path
        QTimer.singleShot(CAMERA_GUARD_DELAY_MS, self._start_camera_after_guard)

    def _start_camera_after_guard(self) -> None:
        """Called after the pre-trigger guard delay."""
        if not self._is_recording:
            return   # recording was stopped before the delay elapsed

        self._camera_worker = CameraWorker(self._exposure_ms)
        self._camera_worker.frame_ready.connect(self._on_camera_frame)
        self._camera_worker.error_occurred.connect(self._handle_camera_error)
        self._camera_worker.start()
        self.recording_started.emit(self._pending_recording_path)

    def stop_recording(self) -> None:
        if not self._is_recording:
            return

        # Suppress TTL so camera stops receiving triggers
        if self._daq_worker is not None:
            self._daq_worker.suppress_ttl()

        # Stop camera worker immediately
        self._teardown_camera()

        # After guard delay, close HDF5 (captures trailing exposure signals)
        QTimer.singleShot(CAMERA_GUARD_DELAY_MS, self._finish_stop_recording)

    def _teardown_camera(self) -> None:
        """Stop and destroy the camera worker."""
        if self._camera_worker is not None:
            self._camera_worker.stop()
            self._camera_worker.wait(3000)
            self._camera_worker = None

    def _finish_stop_recording(self) -> None:
        """Called after the post-trigger guard delay."""
        self._close_recording()

    def _close_recording(self) -> None:
        n = self._saver.n_saved
        self._saver.close()
        self._is_recording = False
        self.recording_stopped.emit(n)

    # ------------------------------------------------------------------
    # TTL / stimulus control
    # ------------------------------------------------------------------

    def set_ttl_config(self, frame_rate_hz: float, exposure_ms: float) -> None:
        self._frame_rate_hz = frame_rate_hz
        self._exposure_ms   = exposure_ms
        if self._daq_worker is not None:
            self._daq_worker.set_ttl_config(frame_rate_hz, exposure_ms)

    def apply_stimulus_waveform(self, ao_2xN: np.ndarray) -> None:
        """Send a combined 2×N AO waveform to the DAQ worker."""
        if self._daq_worker is not None:
            self._daq_worker.set_stimulus_waveform(ao_2xN)

    def clear_stimulus(self) -> None:
        if self._daq_worker is not None:
            self._daq_worker.clear_stimulus_waveform()

    # ------------------------------------------------------------------
    # Internal slots (GUI thread via Qt AutoConnection)
    # ------------------------------------------------------------------

    def _on_ai_chunk(self, chunk: np.ndarray) -> None:
        self._ring_buffer.push(chunk)
        if self._is_recording:
            self._saver.append(chunk)
        if self._on_new_data is not None:
            self._on_new_data(chunk)

    def _on_camera_frame(self, frame: np.ndarray) -> None:
        if self._on_new_frame is not None:
            self._on_new_frame(frame)

    def _handle_error(self, msg: str) -> None:
        # Immediate teardown (no guard delay) — hardware is already in a bad state.
        if self._is_recording:
            self._teardown_camera()
            self._close_recording()

        if self._daq_worker is not None:
            self._daq_worker.stop()
            self._daq_worker.wait(5000)
            self._daq_worker = None

        self._is_running  = False
        self._is_stopping = False
        self.error_occurred.emit(f"DAQ error: {msg}")

    def _handle_camera_error(self, msg: str) -> None:
        # Camera errors are non-fatal for the main acquisition loop
        self.error_occurred.emit(f"Camera error: {msg}")

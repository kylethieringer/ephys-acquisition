"""
ContinuousAcquisition — orchestrates DAQWorker, CameraWorker, RingBuffer,
and ContinuousSaver for the continuous acquisition mode.

Lifecycle
---------
::

    Start         → DAQ AI begins (traces visible); AO silent;
                    camera opens in hardware-triggered mode
                    (no TTL yet — no frames arrive)
    Record        → TTL fires; camera captures every triggered frame;
                    HDF5 file and video file open
    Stop Recording → TTL ceases; after guard delay HDF5 + video close;
                    camera stays open and armed for next recording
    Stop          → camera closed; DAQ shut down

Recording guard delay
---------------------
After ``stop_ttl()`` the camera may still deliver a final frame triggered by
the last TTL pulse.  :data:`~config.CAMERA_GUARD_DELAY_MS` milliseconds of
extra HDF5 recording captures the trailing exposure-return signal on AI3
before the file is closed.

Developer notes
---------------
All public methods are called from the GUI thread.  ``_on_ai_chunk`` and
``_on_camera_frame`` are Qt slots connected with ``AutoConnection`` so they
execute in the GUI thread even though the signals are emitted from worker
threads.  No locking is needed for the saver or ring buffer.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QObject, QTimer, Signal

from config import (
    AI_CHANNELS,
    AI_CHANNELS_VC,
    CAMERA_GUARD_DELAY_MS,
    DEFAULT_EXPOSURE_MS,
    DEFAULT_FRAME_RATE_HZ,
    SAMPLE_RATE,
)
from acquisition.data_buffer import RingBuffer
from acquisition.data_saver import ContinuousSaver
from acquisition.continuous_protocol_runner import ContinuousProtocolRunner
from acquisition.trial_protocol import protocol_to_dict
from hardware.daq_worker import DAQWorker
from hardware.camera_worker import CameraWorker
from hardware.camera_config import HAS_PYPYLON, check_camera_available
from utils.stimulus_generator import get_actual_frame_rate

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


class ContinuousAcquisition(QObject):
    """High-level controller for continuous acquisition mode.

    Owns and coordinates a :class:`~hardware.daq_worker.DAQWorker`,
    :class:`~hardware.camera_worker.CameraWorker`,
    :class:`~acquisition.data_buffer.RingBuffer`, and
    :class:`~acquisition.data_saver.ContinuousSaver`.

    Signals:
        started(): Emitted when both the DAQ and camera workers are running.
        stopped(): Emitted after all workers have shut down cleanly.
        error_occurred(str): Forwarded from workers; the acquisition loop
            has already been stopped when this fires.
        recording_started(object): Emitted when TTL is live and the HDF5
            file is open.  Argument is a ``pathlib.Path`` to the experiment
            folder.
        recording_stopped(int): Emitted after the HDF5 file is closed.
            Argument is the total number of samples saved.

    Attributes:
        _ring_buffer (RingBuffer): Rolling 5-second display buffer shared
            with the trace panel.
        _saver (ContinuousSaver): Handles binary recording and HDF5 conversion.
        _daq_worker (DAQWorker | None): AI/AO worker thread.
        _camera_worker (CameraWorker | None): Camera capture thread.
        _is_running (bool): ``True`` between :meth:`start` and :meth:`stop`.
        _is_recording (bool): ``True`` between :meth:`start_recording` and
            :meth:`stop_recording`.
        _frame_rate_hz (float): Active camera frame rate in Hz.
        _exposure_ms (float): Active camera exposure in ms.
        _video_writer: ``cv2.VideoWriter`` instance, or ``None``.
        _video_path (Path | None): Path to the video file being written.
        _metadata_path (Path | None): Path to the metadata JSON sidecar.
        _metadata (dict | None): In-memory metadata dict; written on open and
            updated (end_time, duration) on close.
        _on_new_data: Callback receiving each AI chunk for the trace panel.
        _on_new_frame: Callback receiving each camera frame for the preview.
    """

    started             = Signal()
    stopped             = Signal()
    error_occurred      = Signal(str)
    recording_started   = Signal(object)   # Path — experiment folder
    recording_stopped   = Signal(int)      # n_samples_saved
    conversion_status   = Signal(str)      # status message for the UI
    protocol_finished   = Signal()         # continuous protocol run complete
    protocol_cancelled  = Signal()         # protocol stopped before completion

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._ring_buffer        = RingBuffer()
        self._saver              = ContinuousSaver()
        self._conversion_worker  = None
        self._protocol_runner:   ContinuousProtocolRunner | None = None
        self._daq_worker:    DAQWorker    | None = None
        self._camera_worker: CameraWorker | None = None
        self._is_running      = False
        self._is_recording    = False

        self._clamp_mode    = "current_clamp"
        self._frame_rate_hz = DEFAULT_FRAME_RATE_HZ
        self._exposure_ms   = DEFAULT_EXPOSURE_MS

        self._video_writer = None
        self._video_path:    Path | None = None
        self._metadata_path: Path | None = None
        self._metadata:      dict | None = None

        self._on_new_data  = None
        self._on_new_frame = None

    # ------------------------------------------------------------------
    # Property accessors
    # ------------------------------------------------------------------

    @property
    def ring_buffer(self) -> RingBuffer:
        """Rolling display buffer shared with the live trace panel."""
        return self._ring_buffer

    @property
    def is_running(self) -> bool:
        """``True`` if the DAQ and camera workers are running."""
        return self._is_running

    @property
    def is_recording(self) -> bool:
        """``True`` if an HDF5 recording is in progress."""
        return self._is_recording

    def set_clamp_mode(self, mode: str) -> None:
        """Set the clamp mode used when writing recording metadata.

        Must be called before :meth:`start_recording`.  Has no effect on the
        hardware — the DAQ records the same physical channels regardless of
        mode; only the metadata channel descriptions change.

        Args:
            mode: ``"current_clamp"`` or ``"voltage_clamp"``.
        """
        self._clamp_mode = mode

    # ------------------------------------------------------------------
    # Connect UI callbacks
    # ------------------------------------------------------------------

    def connect_data_callback(self, slot) -> None:
        """Register a callback to receive each AI chunk for display.

        Args:
            slot: Callable that accepts a ``numpy.ndarray`` of shape
                ``(N_AI_CHANNELS, CHUNK_SIZE)`` in raw Volts.  Called once
                per chunk (~100 Hz) from the GUI thread.
        """
        self._on_new_data = slot

    def connect_frame_callback(self, slot) -> None:
        """Register a callback to receive each camera frame for preview.

        Args:
            slot: Callable that accepts a ``numpy.ndarray`` of shape
                ``(H, W)`` (grayscale) or ``(H, W, 3)`` (colour).
                Called from the GUI thread via Qt AutoConnection.
        """
        self._on_new_frame = slot

    # ------------------------------------------------------------------
    # Acquisition control
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the DAQ and camera workers.

        After this call:

        - AI acquisition is running at 20 kHz (traces are visible).
        - The camera is open in hardware-triggered mode but receives no
          frames until :meth:`start_recording` fires the TTL.
        - AO output is silent (0 V on ao0).

        No-op if already running.

        Raises:
            RuntimeError: if the camera cannot be detected or is already
                open in another program.
        """
        if self._is_running:
            return

        if HAS_PYPYLON:
            check_camera_available()   # raises RuntimeError if camera unusable

        self._ring_buffer.reset()

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
        """Stop all workers and close any open recording.

        If recording is in progress the file is closed immediately without
        the guard delay.  No-op if not running.
        """
        if not self._is_running:
            return

        if self._is_recording:
            self._close_recording()

        self._teardown_camera()

        if self._daq_worker is not None:
            self._daq_worker.stop()
            self._daq_worker.wait(5000)
            self._daq_worker = None

        self._is_running = False
        self.stopped.emit()

    # ------------------------------------------------------------------
    # Recording control (TTL + HDF5 + video)
    # ------------------------------------------------------------------

    def start_recording(
        self,
        save_dir: str | Path,
        metadata: dict | None = None,
    ) -> None:
        """Open the HDF5 file and start the camera TTL.

        Requires the workers to be running (:meth:`start` called first).
        No-op if already recording or not running.

        Args:
            save_dir: Root directory for saving.  The experiment subfolder
                is created automatically by :class:`~acquisition.data_saver.ContinuousSaver`.
            metadata: Subject metadata dict (see
                :meth:`~acquisition.data_saver.ContinuousSaver.open` for expected keys).
                Defaults to a minimal ``{"expt_id": "ephys"}`` dict.

        Note:
            The ``recording_started`` signal is emitted with the experiment
            folder path after this call returns.
        """
        if self._is_recording or not self._is_running:
            return

        if metadata is None:
            metadata = {"expt_id": "ephys", "genotype": "", "age": "", "sex": "Unknown", "targeted_cell_type": ""}

        channels = AI_CHANNELS_VC if self._clamp_mode == "voltage_clamp" else AI_CHANNELS
        h5_path = self._saver.open(save_dir, metadata, channel_defs=channels)
        folder = self._saver.folder
        self._video_path = folder / (h5_path.stem + ".avi")
        self._metadata_path = folder / (h5_path.stem + "_metadata.json")
        self._write_metadata_json(metadata, h5_path, self._video_path)
        self._is_recording = True

        if self._daq_worker is not None:
            self._daq_worker.start_ttl()

        self.recording_started.emit(folder)

    def stop_recording(self) -> None:
        """Stop the camera TTL and close the recording after a guard delay.

        The TTL counter is stopped immediately.  The HDF5 and video files
        remain open for :data:`~config.CAMERA_GUARD_DELAY_MS` milliseconds
        to capture any trailing signals, then closed via
        :meth:`_finish_stop_recording`.  No-op if not recording.
        """
        if not self._is_recording:
            return

        if self._daq_worker is not None:
            self._daq_worker.stop_ttl()

        QTimer.singleShot(CAMERA_GUARD_DELAY_MS, self._finish_stop_recording)

    def _finish_stop_recording(self) -> None:
        """Close recording files after the guard delay has elapsed."""
        self._close_recording()

    def _write_metadata_json(
        self,
        subject_metadata: dict,
        h5_path: Path,
        video_path: Path,
    ) -> None:
        """Write the initial ``metadata.json`` sidecar file.

        ``end_time``, ``duration_samples``, and ``duration_seconds`` are
        ``None`` at creation and filled in by :meth:`_close_recording`.

        Args:
            subject_metadata: Subject information dict.
            h5_path: Path to the HDF5 file being recorded.
            video_path: Path to the video file being recorded.
        """
        channels = AI_CHANNELS_VC if self._clamp_mode == "voltage_clamp" else AI_CHANNELS
        self._metadata = {
            "subject": subject_metadata,
            "start_time": datetime.datetime.now().isoformat(),
            "end_time": None,
            "duration_samples": None,
            "duration_seconds": None,
            "clamp_mode": self._clamp_mode,
            "sample_rate_hz": SAMPLE_RATE,
            "channels": [
                {
                    "name": ch[0],
                    "ni_channel": ch[1],
                    "terminal_config": ch[2],
                    "display_scale": ch[3],
                    "units": ch[4],
                }
                for ch in channels
            ],
            "camera": {
                "frame_rate_hz": self._frame_rate_hz,
                "exposure_ms": self._exposure_ms,
            },
            "files": {
                "ephys_h5": h5_path.name,
                "ephys_bin": self._saver.path.with_suffix(".bin").name,
                "video": video_path.name,
            },
            "protocols": [],
        }
        self._metadata_path.write_text(json.dumps(self._metadata, indent=2))

    def _close_recording(self) -> None:
        """Release the video writer, close the binary file, start HDF5 conversion."""
        if self._video_writer is not None:
            self._video_writer.release()
            self._video_writer = None

        n = self._saver.n_saved

        if self._metadata is not None and self._metadata_path is not None:
            self._metadata["end_time"] = datetime.datetime.now().isoformat()
            self._metadata["duration_samples"] = n
            self._metadata["duration_seconds"] = round(n / SAMPLE_RATE, 3)
            self._metadata_path.write_text(json.dumps(self._metadata, indent=2))
            self._metadata = None
            self._metadata_path = None

        self._is_recording = False
        self.recording_stopped.emit(n)

        worker = self._saver.close()
        if worker is not None:
            self._conversion_worker = worker
            self.conversion_status.emit("Converting to HDF5...")
            worker.conversion_done.connect(self._on_conversion_done)
            worker.conversion_failed.connect(self._on_conversion_failed)
            worker.start()

    def _on_conversion_done(self, path: str) -> None:
        """Handle successful HDF5 conversion."""
        from pathlib import Path as _Path
        fname = _Path(path).name
        self.conversion_status.emit(f"Saved: {fname}")

    def _on_conversion_failed(self, msg: str) -> None:
        """Handle failed HDF5 conversion (binary file preserved)."""
        self.conversion_status.emit(f"HDF5 conversion failed — raw .bin preserved. {msg}")

    # ------------------------------------------------------------------
    # TTL / stimulus control
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

    def apply_stimulus_waveform(self, ao0: NDArray[np.float64]) -> None:
        """Send a 1-D ao0 command-current waveform to the DAQ worker.

        Args:
            ao0: 1-D float64 array of ao0 voltages in V.  Reshaped to
                ``(1, N)`` before passing to the worker.  Convert pA to V
                using :data:`~config.AO_PA_PER_VOLT` beforehand.
        """
        if self._daq_worker is not None:
            self._daq_worker.set_stimulus_waveform(ao0.reshape(1, -1))

    def clear_stimulus(self) -> None:
        """Revert ao0 to zero (silent) on the DAQ worker."""
        if self._daq_worker is not None:
            self._daq_worker.clear_stimulus_waveform()

    def cancel_protocol(self) -> None:
        """Cancel the running continuous protocol without stopping the recording.

        Clears the protocol runner immediately (no more events will fire) and
        zeros the AO output.  The recording continues uninterrupted.
        No-op if no protocol is active.
        """
        if self._protocol_runner is None:
            return
        self._protocol_runner = None
        self.clear_stimulus()
        self.protocol_cancelled.emit()

    def start_protocol(self, protocol) -> None:
        """Start running a protocol within the current continuous recording.

        Must be called after :meth:`start_recording`.  Builds the event
        timeline and anchors it to the current ``n_saved`` position so
        stimulus events fire at the correct sample offsets.

        When all events have fired, ``protocol_finished`` is emitted.
        Recording continues until :meth:`stop_recording` is called.

        Args:
            protocol: A :class:`~acquisition.trial_protocol.TrialProtocol`
                to run.
        """
        if not self._is_recording:
            return
        runner = ContinuousProtocolRunner(protocol)
        start_sample = self._saver.n_saved
        runner.start(start_sample)
        self._protocol_runner = runner

        if self._metadata is not None and self._metadata_path is not None:
            self._metadata.setdefault("protocols", []).append({
                "start_time":   datetime.datetime.now().isoformat(),
                "start_sample": start_sample,
                "protocol":     protocol_to_dict(protocol),
            })
            self._metadata_path.write_text(json.dumps(self._metadata, indent=2))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _teardown_camera(self) -> None:
        """Stop and destroy the camera worker, blocking up to 3 seconds."""
        if self._camera_worker is not None:
            self._camera_worker.stop()
            self._camera_worker.wait(3000)
            self._camera_worker = None

    # ------------------------------------------------------------------
    # Internal slots (GUI thread via Qt AutoConnection)
    # ------------------------------------------------------------------

    def _on_ai_chunk(self, chunk: NDArray[np.float64]) -> None:
        """Handle an incoming AI data chunk (~100 Hz).

        Pushes data to the ring buffer, appends to the HDF5 saver if
        recording, and forwards to the display callback.

        Args:
            chunk: ``(N_AI_CHANNELS, CHUNK_SIZE)`` float64 array in Volts.
        """
        self._ring_buffer.push(chunk)
        if self._is_recording:
            self._saver.append(chunk)
            self._advance_protocol_runner()
        if self._on_new_data is not None:
            self._on_new_data(chunk)

    def _advance_protocol_runner(self) -> None:
        """Fire any pending protocol events and auto-stop when done."""
        if self._protocol_runner is None:
            return

        fired = self._protocol_runner.advance(self._saver.n_saved)
        for ev in fired:
            if ev.action == "apply" and ev.waveform is not None:
                self.apply_stimulus_waveform(ev.waveform)
            elif ev.action == "clear":
                self.clear_stimulus()
            self._saver.log_event(
                sample_idx = self._saver.n_saved,
                event_type = ev.action,
                stim_name  = ev.stim_name,
                stim_idx   = ev.stim_idx,
            )

        if self._protocol_runner.is_done():
            self._protocol_runner = None
            self.protocol_finished.emit()

    def _on_camera_frame(self, frame: NDArray) -> None:
        """Handle an incoming camera frame.

        Writes to the video file if recording and forwards to the preview
        callback.

        Args:
            frame: ``(H, W)`` uint8/uint16 (grayscale) or ``(H, W, 3)``
                array.
        """
        if self._is_recording:
            self._write_video_frame(frame)
        if self._on_new_frame is not None:
            self._on_new_frame(frame)

    def _write_video_frame(self, frame: NDArray) -> None:
        """Lazily open a ``cv2.VideoWriter`` on the first frame, then write.

        The writer is created on the first call using the frame dimensions
        and the actual (rounded) frame rate.  Subsequent calls write directly.

        ``uint16`` frames from high-bit-depth cameras are downsampled to
        ``uint8`` by right-shifting 8 bits.  Colour frames are converted
        from RGB (Basler convention) to BGR (OpenCV convention).

        Args:
            frame: Camera frame array.  May be uint8, uint16, or other
                integer dtype.  Colour frames must have 3 channels.

        Note:
            No-op if OpenCV (cv2) is not installed or ``_video_path`` is
            ``None``.
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

    def _handle_error(self, msg: str) -> None:
        """Handle a fatal DAQ error by tearing down all acquisition state.

        Closes the recording (if active), stops the camera, and shuts down
        the DAQ worker before emitting ``error_occurred``.

        Args:
            msg: Human-readable error message from the worker thread.
        """
        if self._is_recording:
            self._close_recording()
        self._teardown_camera()

        if self._daq_worker is not None:
            self._daq_worker.stop()
            self._daq_worker.wait(5000)
            self._daq_worker = None

        self._is_running = False
        self.error_occurred.emit(f"DAQ error: {msg}")

    def _handle_camera_error(self, msg: str) -> None:
        """Handle a fatal camera error by tearing down all acquisition state.

        Closes the recording (if active), stops both workers, and emits
        ``error_occurred``.  This mirrors :meth:`_handle_error` so that a
        camera failure during acquisition stops the experiment cleanly rather
        than silently continuing without frame capture.

        Args:
            msg: Human-readable error message from the camera worker.
        """
        if self._is_recording:
            self._close_recording()
        self._teardown_camera()

        if self._daq_worker is not None:
            self._daq_worker.stop()
            self._daq_worker.wait(5000)
            self._daq_worker = None

        self._is_running = False
        self.error_occurred.emit(f"Camera error: {msg}")

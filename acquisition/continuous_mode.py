"""
ContinuousAcquisition — orchestrates DAQWorker, CameraWorker, RingBuffer,
and HDF5Saver for the continuous acquisition mode.

Lifecycle:
    Start         → DAQ AI begins (traces visible), AO silent;
                    camera opens in hardware-triggered mode (no TTL yet, no frames)
    Record        → TTL fires immediately; camera captures every triggered frame;
                    HDF5 and video file open
    Stop Recording → TTL suppressed; after guard delay HDF5 + video are closed;
                    camera stays open and armed for next recording
    Stop          → camera closed, DAQ shut down

All public methods are called from the GUI thread.
"""

import datetime
import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from config import AI_CHANNELS, CAMERA_GUARD_DELAY_MS, DEFAULT_EXPOSURE_MS, DEFAULT_FRAME_RATE_HZ, SAMPLE_RATE
from acquisition.data_buffer import RingBuffer
from acquisition.data_saver import HDF5Saver
from hardware.daq_worker import DAQWorker
from hardware.camera_worker import CameraWorker
from utils.stimulus_generator import get_actual_frame_rate

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


class ContinuousAcquisition(QObject):
    """
    High-level controller for continuous acquisition.

    Signals:
        started():            emitted when DAQ + camera workers are running
        stopped():            emitted after workers have cleanly stopped
        error_occurred(str):  forwarded from workers
        recording_started(Path):  emitted when TTL is live and HDF5 is open
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

        # Current hardware settings (kept here so we can re-apply on restart)
        self._frame_rate_hz = DEFAULT_FRAME_RATE_HZ
        self._exposure_ms   = DEFAULT_EXPOSURE_MS

        # Video recording
        self._video_writer = None   # cv2.VideoWriter | None
        self._video_path: Path | None = None

        # Metadata JSON (written on record start, updated on stop)
        self._metadata_path: Path | None = None
        self._metadata: dict | None = None

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
    # Acquisition control
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._is_running:
            return

        self._ring_buffer.reset()

        # Start DAQ with AO silent (no TTL, no command current)
        self._daq_worker = DAQWorker(self._frame_rate_hz, self._exposure_ms)
        self._daq_worker.data_ready.connect(self._on_ai_chunk)
        self._daq_worker.error_occurred.connect(self._handle_error)
        self._daq_worker.start()

        # Open camera immediately so it is armed and ready before Record is pressed.
        # In hardware-triggered mode no frames arrive until TTL fires.
        self._camera_worker = CameraWorker(self._exposure_ms)
        self._camera_worker.frame_ready.connect(self._on_camera_frame)
        self._camera_worker.error_occurred.connect(self._handle_camera_error)
        self._camera_worker.start()

        self._is_running = True
        self.started.emit()

    def stop(self) -> None:
        if not self._is_running:
            return

        # If recording, close files immediately (no guard delay — we're shutting down)
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

    def start_recording(self, save_dir: str | Path, metadata: dict | None = None) -> None:
        if self._is_recording or not self._is_running:
            return

        if metadata is None:
            metadata = {"expt_id": "ephys", "genotype": "", "age": "", "sex": "Unknown", "targeted_cell_type": ""}

        # Open HDF5 file — saver creates the experiment folder
        h5_path = self._saver.open(save_dir, metadata)
        folder = self._saver.folder
        self._video_path = folder / (h5_path.stem + ".avi")
        self._metadata_path = folder / "metadata.json"
        self._write_metadata_json(metadata, h5_path, self._video_path)
        self._is_recording = True

        # Start counter TTL — camera is already armed and will catch every frame
        if self._daq_worker is not None:
            self._daq_worker.start_ttl()

        self.recording_started.emit(folder)

    def stop_recording(self) -> None:
        if not self._is_recording:
            return

        # Stop counter TTL so no further triggers are sent
        if self._daq_worker is not None:
            self._daq_worker.stop_ttl()

        # Guard delay: keeps HDF5 open long enough to record the trailing
        # exposure signals on AI3 after the last TTL pulse.
        QTimer.singleShot(CAMERA_GUARD_DELAY_MS, self._finish_stop_recording)

    def _finish_stop_recording(self) -> None:
        self._close_recording()

    def _write_metadata_json(self, subject_metadata: dict, h5_path: Path, video_path: Path) -> None:
        """Write initial metadata.json; end_time/duration filled in on close."""
        self._metadata = {
            "subject": subject_metadata,
            "start_time": datetime.datetime.now().isoformat(),
            "end_time": None,
            "duration_samples": None,
            "duration_seconds": None,
            "sample_rate_hz": SAMPLE_RATE,
            "channels": [
                {
                    "name": ch[0],
                    "ni_channel": ch[1],
                    "terminal_config": ch[2],
                    "display_scale": ch[3],
                    "units": ch[4],
                }
                for ch in AI_CHANNELS
            ],
            "camera": {
                "frame_rate_hz": self._frame_rate_hz,
                "exposure_ms": self._exposure_ms,
            },
            "files": {
                "ephys_h5": h5_path.name,
                "video": video_path.name,
            },
        }
        self._metadata_path.write_text(json.dumps(self._metadata, indent=2))

    def _close_recording(self) -> None:
        """Release video writer, close HDF5, finalise metadata JSON."""
        if self._video_writer is not None:
            self._video_writer.release()
            self._video_writer = None

        n = self._saver.n_saved
        self._saver.close()

        if self._metadata is not None and self._metadata_path is not None:
            self._metadata["end_time"] = datetime.datetime.now().isoformat()
            self._metadata["duration_samples"] = n
            self._metadata["duration_seconds"] = round(n / SAMPLE_RATE, 3)
            self._metadata_path.write_text(json.dumps(self._metadata, indent=2))
            self._metadata = None
            self._metadata_path = None

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

    def apply_stimulus_waveform(self, ao0: np.ndarray) -> None:
        """Send a 1-D ao0 command-current waveform (Volts) to the DAQ worker."""
        if self._daq_worker is not None:
            self._daq_worker.set_stimulus_waveform(ao0.reshape(1, -1))

    def clear_stimulus(self) -> None:
        if self._daq_worker is not None:
            self._daq_worker.clear_stimulus_waveform()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _teardown_camera(self) -> None:
        """Stop and destroy the camera worker."""
        if self._camera_worker is not None:
            self._camera_worker.stop()
            self._camera_worker.wait(3000)
            self._camera_worker = None

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
        if self._is_recording:
            self._write_video_frame(frame)
        if self._on_new_frame is not None:
            self._on_new_frame(frame)

    def _write_video_frame(self, frame: np.ndarray) -> None:
        """Lazily open a cv2.VideoWriter on the first frame, then write."""
        if not HAS_CV2 or self._video_path is None:
            return

        if self._video_writer is None:
            h, w = frame.shape[:2]
            is_color = frame.ndim == 3
            fps = get_actual_frame_rate(self._frame_rate_hz)
            fourcc = cv2.VideoWriter_fourcc(*"XVID")
            self._video_writer = cv2.VideoWriter(
                str(self._video_path), fourcc, fps, (w, h), isColor=is_color
            )

        if not self._video_writer.isOpened():
            return

        # Convert to uint8 if needed (Basler cameras may output uint16)
        if frame.dtype == np.uint16:
            frame = (frame >> 8).astype(np.uint8)
        elif frame.dtype != np.uint8:
            frame = frame.astype(np.uint8)

        # OpenCV expects BGR for color frames
        if frame.ndim == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        self._video_writer.write(frame)

    def _handle_error(self, msg: str) -> None:
        # Immediate teardown — hardware is already in a bad state
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
        # Camera errors are non-fatal for the main acquisition loop
        self.error_occurred.emit(f"Camera error: {msg}")

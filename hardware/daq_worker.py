"""
DAQWorker — QThread that runs continuous AI acquisition and AO output.

Lifecycle:
    worker = DAQWorker(frame_rate_hz, exposure_ms)
    worker.data_ready.connect(some_slot)
    worker.start()          # launches run() in a new thread
    ...
    worker.stop()
    worker.wait()

Thread safety:
    data_ready is emitted from the worker thread.  Qt AutoConnection
    (the default) queues the signal for delivery in the receiver's
    thread — do NOT change to DirectConnection or you will get races.
"""

import threading
import numpy as np

from PySide6.QtCore import QThread, Signal

from config import CHUNK_SIZE, N_AI_CHANNELS, SAMPLE_RATE, DEFAULT_FRAME_RATE_HZ, DEFAULT_EXPOSURE_MS
from utils.stimulus_generator import generate_ttl_period

try:
    from hardware.daq_config import (
        HAS_NIDAQMX,
        build_ai_task,
        build_ao_task,
        make_reader,
        make_writer,
    )
    import nidaqmx.errors
except ImportError:
    HAS_NIDAQMX = False


_NO_CHANGE = object()   # sentinel: no pending waveform update
_CLEAR     = object()   # sentinel: revert ao0 to zeros (TTL continues)
_SUPPRESS  = object()   # sentinel: silence both AO channels (no TTL)


class DAQWorker(QThread):
    """
    Continuously reads AI and writes AO on a NI PCIe-6323.

    Signals:
        data_ready(np.ndarray): shape (N_AI_CHANNELS, CHUNK_SIZE) raw Volts
        error_occurred(str):    human-readable error message
    """

    data_ready     = Signal(object)   # np.ndarray
    error_occurred = Signal(str)

    def __init__(
        self,
        frame_rate_hz: float = DEFAULT_FRAME_RATE_HZ,
        exposure_ms:   float = DEFAULT_EXPOSURE_MS,
        parent=None,
    ):
        super().__init__(parent)
        self._running       = False
        self._frame_rate    = frame_rate_hz
        self._exposure_ms   = exposure_ms

        # Pending waveform update (set from GUI thread, consumed in worker thread)
        self._lock              = threading.Lock()
        self._pending_waveform  = _NO_CHANGE  # (2, N) float64, _CLEAR, or _NO_CHANGE
        self._pending_ttl_cfg   = None         # (frame_rate, exposure_ms) tuple or None

    # ------------------------------------------------------------------
    # Public slots / methods (called from GUI thread)
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._running = False

    def set_stimulus_waveform(self, ao_2xN: np.ndarray) -> None:
        """
        Schedule a new combined AO waveform (shape 2×N).
        Row 0 = ao0 (command current in V), row 1 = ao1 (TTL in V).
        Applied at the next loop iteration.
        """
        with self._lock:
            self._pending_waveform = ao_2xN.copy()

    def clear_stimulus_waveform(self) -> None:
        """Revert ao0 to zero (TTL continues uninterrupted)."""
        with self._lock:
            self._pending_waveform = _CLEAR

    def suppress_ttl(self) -> None:
        """Silence both AO channels (no TTL, no command current)."""
        with self._lock:
            self._pending_waveform = _SUPPRESS

    def set_ttl_config(self, frame_rate_hz: float, exposure_ms: float) -> None:
        """Update TTL parameters (takes effect at next iteration)."""
        with self._lock:
            self._pending_ttl_cfg = (frame_rate_hz, exposure_ms)

    # ------------------------------------------------------------------
    # QThread.run — executes in the worker thread
    # ------------------------------------------------------------------

    @staticmethod
    def _rebuild_ao(old_ao_task, new_wf: np.ndarray):
        """
        Stop and destroy the old AO task, build a new one sized for
        *new_wf*, write the waveform, and start the new task.

        Returns (new_ao_task, new_writer).
        """
        if old_ao_task is not None:
            try:
                old_ao_task.stop()
                old_ao_task.close()
            except Exception:
                pass

        n_wf = new_wf.shape[1]
        new_ao_task = build_ao_task(n_wf)
        new_ao_task.out_stream.auto_start = False
        new_writer = make_writer(new_ao_task)
        new_writer.write_many_sample(new_wf)
        new_ao_task.start()
        return new_ao_task, new_writer

    def run(self) -> None:
        if not HAS_NIDAQMX:
            self.error_occurred.emit(
                "nidaqmx not installed. Install it with: pip install nidaqmx"
            )
            return

        self._running = True
        ai_task = ao_task = None

        try:
            # Start with both AO channels silent; ContinuousAcquisition
            # enables TTL after the pre-trigger guard delay.
            n_wf          = max(1, int(SAMPLE_RATE / self._frame_rate))
            initial_wf    = np.zeros((2, n_wf), dtype=np.float64)

            ai_task = build_ai_task()
            ao_task = build_ao_task(n_wf)
            ao_task.out_stream.auto_start = False

            writer = make_writer(ao_task)
            writer.write_many_sample(initial_wf)

            reader = make_reader(ai_task)
            ai_buf = np.zeros((N_AI_CHANNELS, CHUNK_SIZE), dtype=np.float64)

            # AO must start before AI (AO waits for AI clock)
            ao_task.start()
            ai_task.start()

            # Track whether we're in "custom waveform" mode
            using_custom_waveform = False

            while self._running:
                # -- Check for pending changes (from GUI thread) --
                pending_wf  = _NO_CHANGE
                pending_ttl = None
                with self._lock:
                    pending_wf  = self._pending_waveform
                    pending_ttl = self._pending_ttl_cfg
                    self._pending_waveform = _NO_CHANGE
                    self._pending_ttl_cfg  = None

                if pending_ttl is not None:
                    self._frame_rate  = pending_ttl[0]
                    self._exposure_ms = pending_ttl[1]
                    using_custom_waveform = False  # reset to default TTL on reconfigure

                if pending_wf is _SUPPRESS:
                    # Silence both AO channels (no TTL, no command)
                    n_silent = max(1, int(SAMPLE_RATE / self._frame_rate))
                    ao_task, writer = self._rebuild_ao(
                        ao_task, np.zeros((2, n_silent), dtype=np.float64))
                    using_custom_waveform = False

                elif pending_wf is _CLEAR:
                    # Revert to default TTL-only waveform
                    ttl_period = generate_ttl_period(self._frame_rate, self._exposure_ms)
                    new_wf = np.vstack([np.zeros(len(ttl_period), dtype=np.float64),
                                        ttl_period])
                    ao_task, writer = self._rebuild_ao(ao_task, new_wf)
                    using_custom_waveform = False

                elif pending_wf is not _NO_CHANGE:
                    # Load new stimulus waveform; rebuild AO task if buffer size changed
                    ao_task, writer = self._rebuild_ao(ao_task, pending_wf)
                    using_custom_waveform = True

                # -- Read AI --
                reader.read_many_sample(ai_buf, CHUNK_SIZE, timeout=2.0)
                self.data_ready.emit(ai_buf.copy())

        except Exception as exc:
            if self._running:   # suppress errors that occur during intentional shutdown
                self.error_occurred.emit(str(exc))
        finally:
            # Stop AO (slave) first, then AI (clock master) to avoid
            # AO hanging while waiting for a clock that will never come.
            for task in (ao_task, ai_task):
                if task is not None:
                    try:
                        task.stop()
                        task.close()
                    except Exception:
                        pass
            self._running = False

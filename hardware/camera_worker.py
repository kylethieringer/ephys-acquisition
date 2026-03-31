"""
CameraWorker — QThread that grabs frames from a Basler camera.

Uses GrabStrategy_LatestImageOnly so the preview always shows the
most recent frame; older frames are discarded if the GUI is slow.

Lifecycle:
    worker = CameraWorker(exposure_ms=10.0)
    worker.frame_ready.connect(some_slot)
    worker.start()
    ...
    worker.stop()
    worker.wait()
"""

import numpy as np

from PySide6.QtCore import QThread, Signal

from config import DEFAULT_EXPOSURE_MS

try:
    from hardware.camera_config import HAS_PYPYLON, open_camera, set_exposure
    if HAS_PYPYLON:
        from pypylon import pylon
except ImportError:
    HAS_PYPYLON = False


class CameraWorker(QThread):
    """
    Grabs hardware-triggered frames and emits them as numpy arrays.

    Signals:
        frame_ready(np.ndarray): HxW (mono) or HxWx3 (color) uint8/uint16 array
        error_occurred(str):     human-readable error message
    """

    frame_ready    = Signal(object)   # np.ndarray
    error_occurred = Signal(str)

    GRAB_TIMEOUT_MS = 1000   # long timeout — frames arrive only when TTL fires

    def __init__(self, exposure_ms: float = DEFAULT_EXPOSURE_MS, parent=None):
        super().__init__(parent)
        self._running     = False
        self._exposure_ms = exposure_ms

    # ------------------------------------------------------------------
    # Public API (called from GUI thread)
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # QThread.run — executes in the worker thread
    # ------------------------------------------------------------------

    def run(self) -> None:
        if not HAS_PYPYLON:
            self.error_occurred.emit(
                "pypylon not installed. Install it with: pip install pypylon"
            )
            return

        cam = None
        try:
            cam = open_camera(self._exposure_ms)
            self._running = True

            cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

            while self._running and cam.IsGrabbing():
                result = cam.RetrieveResult(
                    self.GRAB_TIMEOUT_MS,
                    pylon.TimeoutHandling_Return,
                )
                # IsValid() must be checked before any property access —
                # TimeoutHandling_Return gives a null GrabResultPtr on timeout.
                if not result.IsValid():
                    continue

                try:
                    if result.GrabSucceeded():
                        img = result.Array
                        self.frame_ready.emit(img.copy())
                finally:
                    result.Release()

        except Exception as exc:
            if self._running:
                self.error_occurred.emit(str(exc))
        finally:
            if cam is not None:
                try:
                    cam.StopGrabbing()
                except Exception:
                    pass
                try:
                    cam.Close()
                except Exception:
                    pass
            self._running = False

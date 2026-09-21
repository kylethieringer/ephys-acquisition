"""Tests for the "no camera frames after Record" warning in continuous mode.

A recording whose camera never delivers a frame writes no .avi, and until
this warning existed the GUI said nothing (no video 2026-09-18 → 09-21 went
unnoticed).  These tests drive the real :class:`ContinuousAcquisition`
recording path without DAQ or camera hardware: the workers are never started,
frames are delivered by calling the camera slot directly, and the check
timeout is shortened so the tests run in well under a second.
"""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

import acquisition.continuous_mode as continuous_mode
from acquisition.continuous_mode import ContinuousAcquisition

TIMEOUT_MS = 100


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QCoreApplication([])


def pump(ms: int) -> None:
    """Run the Qt event loop for ``ms`` milliseconds so timers can fire."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture
def acq(qapp, monkeypatch):
    monkeypatch.setattr(continuous_mode, "NO_FRAME_WARNING_MS", TIMEOUT_MS)
    monkeypatch.setattr(continuous_mode, "CAMERA_GUARD_DELAY_MS", 0)
    a = ContinuousAcquisition()
    a._is_running = True  # stands in for start(), which needs the DAQ and camera

    # Keep the real file close but drop the HDF5 conversion thread (and the
    # QC it schedules): slow, and irrelevant to the frame check.
    real_close = a._saver.close
    monkeypatch.setattr(a._saver, "close", lambda: (real_close(), None)[1])

    a.warnings = []
    a.warning_occurred.connect(a.warnings.append)
    yield a
    if a.is_recording:
        a.stop_recording()
        pump(10)


FRAME = np.zeros((600, 800), dtype=np.uint8)


def test_warns_when_no_frames_arrive(acq, tmp_path):
    acq.start_recording(tmp_path)
    pump(3 * TIMEOUT_MS)

    assert len(acq.warnings) == 1
    assert "no camera frames" in acq.warnings[0].lower()


def test_silent_when_frames_arrive(acq, tmp_path):
    acq.start_recording(tmp_path)
    acq._on_camera_frame(FRAME)  # the slot CameraWorker.frame_ready drives
    pump(3 * TIMEOUT_MS)

    assert acq.warnings == []


def test_quick_restart_warns_once_not_twice(acq, tmp_path):
    acq.start_recording(tmp_path / "first")
    acq.stop_recording()
    pump(10)  # guard delay is 0: the first recording closes here
    assert not acq.is_recording

    acq.start_recording(tmp_path / "second")
    pump(3 * TIMEOUT_MS)

    assert len(acq.warnings) == 1

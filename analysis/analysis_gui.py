#!/usr/bin/env python3
"""
Interactive analysis GUI for browsing aligned ephys data and video.

Features:
  - Load an HDF5 recording and auto-locate the corresponding video
  - Synchronized video + ephys trace display with timeline scrubbing
  - Play/pause at adjustable speed
  - Select a time region and export a composite video clip

Usage:
    python analysis/analysis_gui.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import h5py
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

# -- project imports ---------------------------------------------------------
# When running as a script, ensure the project root is on sys.path so that
# sibling packages (analysis, config) can be imported.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from analysis.align_video import (
    TRACE_HEIGHT,
    _compute_rmp,
    _draw_trace,
    _is_trial_mode,
    _load_continuous,
    _load_trial,
    _y_range,
    find_frame_samples,
)
from config import TRACE_COLORS

# ---------------------------------------------------------------------------
# Dark stylesheet (matches main.py)
# ---------------------------------------------------------------------------

_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1a1a2e;
    color: #e0e0e0;
    font-size: 9pt;
}
QGroupBox {
    border: 1px solid #444466;
    border-radius: 4px;
    margin-top: 6px;
    padding-top: 4px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    color: #aaaaff;
}
QPushButton {
    background-color: #2a2a4a;
    border: 1px solid #555577;
    border-radius: 3px;
    padding: 4px 10px;
    color: #e0e0e0;
}
QPushButton:hover  { background-color: #3a3a6a; }
QPushButton:pressed { background-color: #1a1a3a; }
QPushButton:disabled { color: #666688; border-color: #333355; }
QDoubleSpinBox, QSpinBox, QLineEdit {
    background-color: #121225;
    border: 1px solid #444466;
    border-radius: 2px;
    padding: 2px 4px;
    color: #e0e0e0;
}
QLabel { color: #ccccdd; }
QCheckBox { color: #ccccdd; }
QSplitter::handle { background-color: #333355; }
QSlider::groove:horizontal {
    background: #2a2a4a;
    height: 6px;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #aaaaff;
    width: 14px;
    margin: -4px 0;
    border-radius: 7px;
}
QProgressBar {
    background-color: #121225;
    border: 1px solid #444466;
    border-radius: 3px;
    text-align: center;
    color: #e0e0e0;
}
QProgressBar::chunk {
    background-color: #5555aa;
    border-radius: 2px;
}
"""


# ============================================================================
# VideoPanel
# ============================================================================

class VideoPanel(QWidget):
    """Displays a single video frame using pyqtgraph ImageView."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        box = QGroupBox("Video")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(2, 2, 2, 2)

        self._image_view = pg.ImageView()
        self._image_view.ui.roiBtn.hide()
        self._image_view.ui.menuBtn.hide()
        self._image_view.ui.histogram.hide()
        self._image_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._image_view.setImage(np.zeros((100, 100), dtype=np.uint8))
        box_layout.addWidget(self._image_view)

        layout.addWidget(box)
        self._first_frame = True

    def set_frame(self, bgr: np.ndarray) -> None:
        """Display a BGR (OpenCV) frame."""
        if bgr.ndim == 3 and bgr.shape[2] == 3:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            img = np.transpose(rgb, (1, 0, 2))
        else:
            img = bgr.T
        max_val = 65535 if img.dtype == np.uint16 else 255
        self._image_view.setImage(img, autoRange=self._first_frame,
                                  autoLevels=False,
                                  levels=(0, max_val),
                                  autoHistogramRange=False)
        self._first_frame = False

    def clear(self) -> None:
        self._image_view.setImage(np.zeros((100, 100), dtype=np.uint8))
        self._first_frame = True


# ============================================================================
# TracePanel
# ============================================================================

class TracePanel(QWidget):
    """Ephys trace display with cursor, region selector, and axis controls."""

    seek_requested = Signal(int)  # emits frame index

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        box = QGroupBox("Electrophysiology Trace")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(2, 2, 2, 2)

        self._plot = pg.PlotWidget()
        self._plot.setBackground("#1a1a2e")
        self._plot.setLabel("bottom", "Time", units="s")
        self._plot.setLabel("left", "Vm", units="mV")
        self._plot.showGrid(x=True, y=True, alpha=0.15)

        self._curve = self._plot.plot(pen=pg.mkPen(TRACE_COLORS[0], width=1))
        self._curve.setDownsampling(auto=True, method="peak")
        self._curve.setClipToView(True)

        self._cursor = pg.InfiniteLine(pos=0, angle=90, movable=False,
                                       pen=pg.mkPen("#ffffff", width=1,
                                                     style=Qt.PenStyle.DashLine))
        self._plot.addItem(self._cursor)

        self._region = pg.LinearRegionItem(brush=pg.mkBrush(85, 85, 170, 50))
        self._region.setZValue(-10)
        self._region.hide()
        self._plot.addItem(self._region)

        box_layout.addWidget(self._plot)

        # -- X-axis controls ---------------------------------------------------
        x_row = QHBoxLayout()
        x_row.setSpacing(6)

        x_row.addWidget(QLabel("X range:"))

        self._x_win_spin = QDoubleSpinBox()
        self._x_win_spin.setRange(0.1, 600.0)
        self._x_win_spin.setValue(5.0)
        self._x_win_spin.setSingleStep(0.5)
        self._x_win_spin.setDecimals(1)
        self._x_win_spin.setSuffix(" s")
        self._x_win_spin.setFixedWidth(80)
        self._x_win_spin.setToolTip("Width of visible time window")
        self._x_win_spin.valueChanged.connect(self._apply_x_range)
        x_row.addWidget(self._x_win_spin)

        self._follow_cb = QCheckBox("Follow cursor")
        self._follow_cb.setChecked(True)
        self._follow_cb.setToolTip(
            "Keep the cursor centered in the view during playback")
        x_row.addWidget(self._follow_cb)

        self._show_all_btn = QPushButton("Show All")
        self._show_all_btn.setFixedWidth(70)
        self._show_all_btn.setToolTip("Zoom out to show the full recording")
        self._show_all_btn.clicked.connect(self._show_all)
        x_row.addWidget(self._show_all_btn)

        x_row.addStretch()

        # -- Y-axis controls ---------------------------------------------------
        x_row.addWidget(QLabel("Y min:"))

        self._y_min_spin = QDoubleSpinBox()
        self._y_min_spin.setRange(-1e6, 1e6)
        self._y_min_spin.setDecimals(1)
        self._y_min_spin.setSuffix(" mV")
        self._y_min_spin.setFixedWidth(100)
        self._y_min_spin.valueChanged.connect(self._apply_y_range)
        x_row.addWidget(self._y_min_spin)

        x_row.addWidget(QLabel("Y max:"))

        self._y_max_spin = QDoubleSpinBox()
        self._y_max_spin.setRange(-1e6, 1e6)
        self._y_max_spin.setDecimals(1)
        self._y_max_spin.setSuffix(" mV")
        self._y_max_spin.setFixedWidth(100)
        self._y_max_spin.valueChanged.connect(self._apply_y_range)
        x_row.addWidget(self._y_max_spin)

        self._y_auto_cb = QCheckBox("Auto Y")
        self._y_auto_cb.setChecked(True)
        self._y_auto_cb.setToolTip("Auto-scale Y axis to data range")
        self._y_auto_cb.toggled.connect(self._on_auto_y_toggled)
        x_row.addWidget(self._y_auto_cb)

        box_layout.addLayout(x_row)
        layout.addWidget(box)

        # Click-to-seek
        self._plot.scene().sigMouseClicked.connect(self._on_click)

        # State
        self._frame_samples: np.ndarray | None = None
        self._sr: float = 1.0
        self._t_max: float = 0.0

    def load_trace(self, vm: np.ndarray, sr: float,
                   frame_samples: np.ndarray) -> None:
        """Plot the full Vm trace and store alignment data."""
        self._sr = sr
        self._frame_samples = frame_samples
        t = np.arange(len(vm)) / sr
        self._t_max = t[-1]
        self._curve.setData(t, vm)

        # Set Y spinboxes to data range
        y_lo, y_hi = float(np.nanmin(vm)), float(np.nanmax(vm))
        margin = (y_hi - y_lo) * 0.08
        self._y_min_spin.blockSignals(True)
        self._y_max_spin.blockSignals(True)
        self._y_min_spin.setValue(y_lo - margin)
        self._y_max_spin.setValue(y_hi + margin)
        self._y_min_spin.blockSignals(False)
        self._y_max_spin.blockSignals(False)

        if self._y_auto_cb.isChecked():
            self._plot.enableAutoRange(axis="y")
        else:
            self._apply_y_range()

        # Start with full view
        self._plot.setXRange(0, t[-1], padding=0.01)
        self._region.setRegion([t[-1] * 0.45, t[-1] * 0.55])

    def set_cursor(self, time_s: float) -> None:
        self._cursor.setValue(time_s)
        if self._follow_cb.isChecked():
            half = self._x_win_spin.value() / 2.0
            self._plot.setXRange(time_s - half, time_s + half, padding=0)

    def set_region_visible(self, visible: bool) -> None:
        self._region.setVisible(visible)

    def get_region(self) -> tuple[float, float]:
        """Return selected region bounds in seconds."""
        lo, hi = self._region.getRegion()
        return (float(np.asarray(lo).item()), float(np.asarray(hi).item()))

    def clear(self) -> None:
        self._curve.setData([], [])
        self._cursor.setValue(0)
        self._frame_samples = None

    # -- axis control slots --------------------------------------------------

    def _apply_x_range(self) -> None:
        if self._follow_cb.isChecked():
            cursor_t = self._cursor.value()
            half = self._x_win_spin.value() / 2.0
            self._plot.setXRange(cursor_t - half, cursor_t + half, padding=0)

    def _show_all(self) -> None:
        self._follow_cb.setChecked(False)
        self._plot.setXRange(0, self._t_max, padding=0.01)
        if self._y_auto_cb.isChecked():
            self._plot.enableAutoRange(axis="y")

    def _apply_y_range(self) -> None:
        if not self._y_auto_cb.isChecked():
            self._plot.setYRange(self._y_min_spin.value(),
                                self._y_max_spin.value(), padding=0)

    def _on_auto_y_toggled(self, auto: bool) -> None:
        if auto:
            self._plot.enableAutoRange(axis="y")
            self._y_min_spin.setEnabled(False)
            self._y_max_spin.setEnabled(False)
        else:
            self._plot.disableAutoRange(axis="y")
            self._y_min_spin.setEnabled(True)
            self._y_max_spin.setEnabled(True)
            self._apply_y_range()

    def _on_click(self, event) -> None:
        if self._frame_samples is None:
            return
        pos = event.scenePos()
        if not self._plot.sceneBoundingRect().contains(pos):
            return
        mouse_point = self._plot.plotItem.vb.mapSceneToView(pos)
        click_sample = int(mouse_point.x() * self._sr)
        # Find nearest frame
        idx = int(np.searchsorted(self._frame_samples, click_sample))
        idx = max(0, min(idx, len(self._frame_samples) - 1))
        self.seek_requested.emit(idx)


# ============================================================================
# TimelineController
# ============================================================================

class TimelineController(QWidget):
    """Playback controls: slider, play/pause, speed, labels."""

    seek_requested = Signal(int)
    play_toggled = Signal(bool)
    speed_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._play_btn = QPushButton("Play")
        self._play_btn.setFixedWidth(60)
        self._play_btn.clicked.connect(self._on_play)
        layout.addWidget(self._play_btn)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(0)
        self._slider.valueChanged.connect(self.seek_requested.emit)
        layout.addWidget(self._slider, stretch=1)

        speed_label = QLabel("Speed:")
        layout.addWidget(speed_label)

        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(0.1, 10.0)
        self._speed_spin.setValue(1.0)
        self._speed_spin.setSingleStep(0.1)
        self._speed_spin.setDecimals(1)
        self._speed_spin.setSuffix("x")
        self._speed_spin.setFixedWidth(70)
        self._speed_spin.valueChanged.connect(self.speed_changed.emit)
        layout.addWidget(self._speed_spin)

        self._frame_label = QLabel("Frame: 0 / 0")
        self._frame_label.setFixedWidth(140)
        layout.addWidget(self._frame_label)

        self._time_label = QLabel("Time: 0.000 s")
        self._time_label.setFixedWidth(120)
        layout.addWidget(self._time_label)

        self._playing = False

    def configure(self, n_frames: int) -> None:
        self._slider.setMaximum(max(0, n_frames - 1))
        self._slider.setValue(0)
        self._set_playing(False)

    def set_position(self, frame_idx: int, block_signals: bool = True) -> None:
        if block_signals:
            self._slider.blockSignals(True)
        self._slider.setValue(frame_idx)
        if block_signals:
            self._slider.blockSignals(False)

    def update_labels(self, frame_idx: int, n_frames: int, time_s: float) -> None:
        self._frame_label.setText(f"Frame: {frame_idx + 1} / {n_frames}")
        self._time_label.setText(f"Time: {time_s:.3f} s")

    def speed(self) -> float:
        return self._speed_spin.value()

    def _on_play(self) -> None:
        self._set_playing(not self._playing)
        self.play_toggled.emit(self._playing)

    def _set_playing(self, playing: bool) -> None:
        self._playing = playing
        self._play_btn.setText("Pause" if playing else "Play")


# ============================================================================
# ExportWorker
# ============================================================================

class ExportWorker(QThread):
    """Renders composite video clip in a background thread."""

    progress = Signal(int)   # percentage 0-100
    finished = Signal(str)   # result message

    def __init__(
        self,
        cap_path: str,
        output_path: str,
        vm: np.ndarray,
        sr: float,
        frame_samples: np.ndarray,
        start_frame: int,
        end_frame: int,
        fps: float,
        y_lo: float,
        y_hi: float,
        rmp: float,
        brightness: float = 0.0,
        contrast: float = 1.0,
    ) -> None:
        super().__init__()
        self._cap_path = cap_path
        self._output_path = output_path
        self._vm = vm
        self._sr = sr
        self._frame_samples = frame_samples
        self._start = start_frame
        self._end = end_frame
        self._fps = fps
        self._y_lo = y_lo
        self._y_hi = y_hi
        self._rmp = rmp
        self._brightness = brightness
        self._contrast = contrast

    def run(self) -> None:
        cap = cv2.VideoCapture(self._cap_path)
        if not cap.isOpened():
            self.finished.emit(f"Error: cannot open video {self._cap_path}")
            return

        vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        out_h = vid_h + TRACE_HEIGHT
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        writer = cv2.VideoWriter(self._output_path, fourcc, self._fps,
                                 (vid_w, out_h))
        if not writer.isOpened():
            cap.release()
            self.finished.emit(f"Error: cannot write to {self._output_path}")
            return

        n_clip = self._end - self._start
        cap.set(cv2.CAP_PROP_POS_FRAMES, self._start)

        for i in range(n_clip):
            ret, frame = cap.read()
            if not ret:
                break

            frame_idx = self._start + i
            center_sample = int(self._frame_samples[frame_idx])
            trace_img = _draw_trace(
                self._vm, center_sample, self._sr, vid_w,
                self._y_lo, self._y_hi, self._rmp,
            )

            if frame.shape[1] != vid_w:
                frame = cv2.resize(frame, (vid_w, vid_h))

            if self._contrast != 1.0 or self._brightness != 0.0:
                frame = cv2.convertScaleAbs(
                    frame, alpha=self._contrast, beta=self._brightness,
                )

            composite = np.vstack([frame, trace_img])
            writer.write(composite)

            pct = int(100 * (i + 1) / n_clip)
            self.progress.emit(pct)

        cap.release()
        writer.release()
        self.finished.emit(f"Saved clip to {self._output_path}")


# ============================================================================
# ExportDialog
# ============================================================================

class ExportDialog(QDialog):
    """Configure and run clip export."""

    def __init__(
        self,
        parent: QWidget,
        video_path: str,
        vm: np.ndarray,
        sr: float,
        frame_samples: np.ndarray,
        start_frame: int,
        end_frame: int,
        fps: float,
        y_lo: float,
        y_hi: float,
        rmp: float,
        default_output: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Clip")
        self.setMinimumWidth(400)

        self._worker: ExportWorker | None = None
        self._video_path = video_path
        self._start_frame = start_frame
        self._end_frame = end_frame

        layout = QVBoxLayout(self)

        start_t = frame_samples[start_frame] / sr
        end_t = frame_samples[min(end_frame, len(frame_samples) - 1)] / sr
        n_clip = end_frame - start_frame

        info = QLabel(
            f"Region: {start_t:.3f} s  to  {end_t:.3f} s\n"
            f"Frames: {n_clip}  ({end_t - start_t:.2f} s)"
        )
        layout.addWidget(info)

        # Output path
        path_row = QHBoxLayout()
        self._path_label = QLabel(default_output)
        self._path_label.setWordWrap(True)
        path_row.addWidget(self._path_label, stretch=1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        # Brightness / contrast controls
        adj_box = QGroupBox("Video Adjustments")
        adj_layout = QHBoxLayout(adj_box)

        adj_layout.addWidget(QLabel("Brightness:"))
        self._brightness_spin = QDoubleSpinBox()
        self._brightness_spin.setRange(-255.0, 255.0)
        self._brightness_spin.setValue(0.0)
        self._brightness_spin.setSingleStep(5.0)
        self._brightness_spin.setDecimals(0)
        self._brightness_spin.setToolTip(
            "Additive offset (beta). Positive = brighter, negative = darker."
        )
        adj_layout.addWidget(self._brightness_spin)

        adj_layout.addWidget(QLabel("Contrast:"))
        self._contrast_spin = QDoubleSpinBox()
        self._contrast_spin.setRange(0.1, 5.0)
        self._contrast_spin.setValue(1.0)
        self._contrast_spin.setSingleStep(0.1)
        self._contrast_spin.setDecimals(2)
        self._contrast_spin.setToolTip(
            "Multiplicative gain (alpha). 1.0 = unchanged, >1 = more contrast."
        )
        adj_layout.addWidget(self._contrast_spin)

        auto_btn = QPushButton("Auto")
        auto_btn.setToolTip(
            "Sample frames from the clip and pick brightness/contrast\n"
            "that stretch the 1st–99th percentile to 0–255."
        )
        auto_btn.clicked.connect(self._auto_adjust)
        adj_layout.addWidget(auto_btn)

        reset_btn = QPushButton("Reset")
        reset_btn.clicked.connect(self._reset_adjustments)
        adj_layout.addWidget(reset_btn)

        layout.addWidget(adj_box)

        self._progress = QProgressBar()
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._export_btn = QPushButton("Export")
        self._export_btn.clicked.connect(lambda: self._run_export(
            video_path, vm, sr, frame_samples,
            start_frame, end_frame, fps, y_lo, y_hi, rmp,
        ))
        layout.addWidget(self._export_btn)

        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

    def _reset_adjustments(self) -> None:
        self._brightness_spin.setValue(0.0)
        self._contrast_spin.setValue(1.0)

    def _auto_adjust(self) -> None:
        """Sample frames from the clip range and pick brightness/contrast
        that map the 1st–99th percentile of luminance to 0–255."""
        cap = cv2.VideoCapture(self._video_path)
        if not cap.isOpened():
            return

        n_samples = 10
        n_clip = max(1, self._end_frame - self._start_frame)
        step = max(1, n_clip // n_samples)
        indices = list(range(self._start_frame, self._end_frame, step))[:n_samples]

        pixels = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue
            if frame.ndim == 3:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            pixels.append(frame.ravel())
        cap.release()

        if not pixels:
            return

        data = np.concatenate(pixels)
        p_lo, p_hi = np.percentile(data, [1.0, 99.0])
        if p_hi - p_lo < 1.0:
            return

        alpha = 255.0 / (p_hi - p_lo)
        beta = -alpha * p_lo
        alpha = float(np.clip(alpha, 0.1, 5.0))
        beta = float(np.clip(beta, -255.0, 255.0))
        self._contrast_spin.setValue(alpha)
        self._brightness_spin.setValue(beta)

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Clip", self._path_label.text(),
            "AVI Video (*.avi)")
        if path:
            self._path_label.setText(path)

    def _run_export(
        self,
        video_path: str,
        vm: np.ndarray,
        sr: float,
        frame_samples: np.ndarray,
        start_frame: int,
        end_frame: int,
        fps: float,
        y_lo: float,
        y_hi: float,
        rmp: float,
    ) -> None:
        self._export_btn.setEnabled(False)
        self._status_label.setText("Exporting...")

        self._worker = ExportWorker(
            video_path, self._path_label.text(),
            vm, sr, frame_samples,
            start_frame, end_frame, fps,
            y_lo, y_hi, rmp,
            brightness=self._brightness_spin.value(),
            contrast=self._contrast_spin.value(),
        )
        self._worker.progress.connect(self._progress.setValue)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_finished(self, msg: str) -> None:
        self._status_label.setText(msg)
        self._export_btn.setEnabled(True)


# ============================================================================
# AnalysisWindow
# ============================================================================

class AnalysisWindow(QMainWindow):
    """Main analysis GUI window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Ephys Analysis")
        self.resize(1400, 900)

        # Data state
        self._vm: np.ndarray | None = None
        self._sr: float = 20_000.0
        self._frame_samples: np.ndarray | None = None
        self._cap: cv2.VideoCapture | None = None
        self._video_path: str = ""
        self._h5_path: str = ""
        self._n_frames: int = 0
        self._current_frame: int = 0
        self._fps: float = 100.0
        self._y_lo: float = 0.0
        self._y_hi: float = 0.0
        self._rmp: float = 0.0
        self._seeking: bool = False
        self._playing: bool = False

        self._build_ui()
        self._playback_timer = QTimer(self)
        self._playback_timer.setSingleShot(True)
        self._playback_timer.timeout.connect(self._on_playback_tick)

    # -- UI construction -----------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Top bar
        top_bar = QHBoxLayout()
        open_btn = QPushButton("Open HDF5...")
        open_btn.clicked.connect(self._on_open_file)
        top_bar.addWidget(open_btn)

        self._file_label = QLabel("No file loaded")
        self._file_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top_bar.addWidget(self._file_label, stretch=1)

        self._status_label = QLabel("")
        top_bar.addWidget(self._status_label)
        root.addLayout(top_bar)

        # Splitter: video | trace
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._video_panel = VideoPanel()
        self._trace_panel = TracePanel()
        splitter.addWidget(self._video_panel)
        splitter.addWidget(self._trace_panel)
        splitter.setSizes([500, 800])
        root.addWidget(splitter, stretch=1)

        # Timeline controller
        self._timeline = TimelineController()
        root.addWidget(self._timeline)

        # Bottom controls: region + export
        bottom = QHBoxLayout()
        self._region_cb = QCheckBox("Select Region")
        self._region_cb.toggled.connect(self._trace_panel.set_region_visible)
        bottom.addWidget(self._region_cb)

        self._export_btn = QPushButton("Export Clip...")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._on_export_clip)
        bottom.addWidget(self._export_btn)

        bottom.addStretch()
        root.addLayout(bottom)

        # Wire signals
        self._timeline.seek_requested.connect(self._seek_to_frame)
        self._timeline.play_toggled.connect(self._on_play_toggle)
        self._timeline.speed_changed.connect(self._on_speed_changed)
        self._trace_panel.seek_requested.connect(self._seek_to_frame)

    # -- File loading --------------------------------------------------------

    def _on_open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open HDF5 File", "",
            "HDF5 Files (*.h5 *.hdf5);;All Files (*)")
        if not path:
            return

        h5_path = Path(path)
        self._h5_path = str(h5_path)

        try:
            trial_num = None
            if _is_trial_mode(h5_path):
                # Let user pick a trial
                with h5py.File(h5_path, "r") as f:
                    trials = sorted(k for k in f.keys() if k.startswith("trial_"))
                if not trials:
                    QMessageBox.warning(self, "Error", "No trials found in file.")
                    return
                trial_str, ok = QInputDialog.getItem(
                    self, "Select Trial", "Trial:", trials, 0, False)
                if not ok:
                    return
                trial_num = int(trial_str.split("_")[1])
                vm, ttl, sr = _load_trial(h5_path, trial_num)
            else:
                vm, ttl, sr = _load_continuous(h5_path)

            frame_samples = find_frame_samples(ttl)
            if len(frame_samples) == 0:
                QMessageBox.warning(
                    self, "Error",
                    "No TTL rising edges found on channel 4 (TTLLoopback).")
                return

        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))
            return

        # Find corresponding video
        video_path = self._find_video(h5_path, trial_num)
        if video_path is None:
            # Manual selection
            vp, _ = QFileDialog.getOpenFileName(
                self, "Select Video File", str(h5_path.parent),
                "Video Files (*.avi *.mp4);;All Files (*)")
            if not vp:
                QMessageBox.information(
                    self, "No Video",
                    "Continuing without video. Export will be disabled.")
                video_path = None
            else:
                video_path = Path(vp)

        # Open video
        if self._cap is not None:
            self._cap.release()
            self._cap = None

        has_video = False
        if video_path is not None:
            cap = cv2.VideoCapture(str(video_path))
            if cap.isOpened():
                self._cap = cap
                self._video_path = str(video_path)
                has_video = True
            else:
                QMessageBox.warning(
                    self, "Video Error",
                    f"Cannot open video: {video_path.name}")

        # Store data
        self._vm = vm
        self._sr = sr
        self._frame_samples = frame_samples

        n_video = 0
        if has_video and self._cap is not None:
            n_video = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 100.0
            self._n_frames = min(len(frame_samples), n_video)
        else:
            self._n_frames = len(frame_samples)
            self._fps = 100.0

        if has_video and len(frame_samples) != n_video:
            self._status_label.setText(
                f"Warning: TTL edges ({len(frame_samples)}) != "
                f"video frames ({n_video})")
        else:
            self._status_label.setText("")

        self._y_lo, self._y_hi = _y_range(vm)
        self._rmp = _compute_rmp(vm)

        # Update UI
        trial_suffix = f" (trial {trial_num})" if trial_num else ""
        self._file_label.setText(f"{h5_path.name}{trial_suffix}")
        self._trace_panel.load_trace(vm, sr, frame_samples)
        self._timeline.configure(self._n_frames)
        self._export_btn.setEnabled(has_video)
        self._region_cb.setChecked(False)

        # Show first frame
        self._seek_to_frame(0)

    def _find_video(self, h5_path: Path,
                    trial_num: int | None) -> Path | None:
        """Try to auto-locate the video for the given HDF5 file."""
        directory = h5_path.parent

        # 1) Check metadata JSON sidecar
        meta_path = directory / (h5_path.stem + "_metadata.json")
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                vid_name = meta.get("files", {}).get("video")
                if vid_name:
                    vid_path = directory / vid_name
                    if vid_path.exists():
                        return vid_path
            except (json.JSONDecodeError, KeyError):
                pass

        # 2) Trial mode: check video_file attribute
        if trial_num is not None:
            try:
                with h5py.File(h5_path, "r") as f:
                    key = f"trial_{trial_num:03d}"
                    vid_name = f[key].attrs.get("video_file", "")
                    if vid_name:
                        vid_path = directory / vid_name
                        if vid_path.exists():
                            return vid_path
            except (KeyError, OSError):
                pass

        # 3) Look for .avi with matching stem in the same directory
        for avi in sorted(directory.glob("*.avi")):
            if avi.stem.startswith(h5_path.stem.replace("_metadata", "")):
                return avi

        return None

    # -- Synchronized seeking ------------------------------------------------

    def _seek_to_frame(self, frame_idx: int) -> None:
        if self._frame_samples is None or self._n_frames == 0:
            return
        if self._seeking:
            return
        self._seeking = True
        try:
            frame_idx = max(0, min(frame_idx, self._n_frames - 1))
            sample_idx = int(self._frame_samples[frame_idx])
            time_s = sample_idx / self._sr

            # Video
            if self._cap is not None:
                # Sequential read if next frame, otherwise seek
                if frame_idx != self._current_frame + 1:
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = self._cap.read()
                if ret:
                    self._video_panel.set_frame(frame)

            # Trace cursor
            self._trace_panel.set_cursor(time_s)

            # Timeline
            self._timeline.set_position(frame_idx)
            self._timeline.update_labels(frame_idx, self._n_frames, time_s)

            self._current_frame = frame_idx
        finally:
            self._seeking = False

    # -- Playback ------------------------------------------------------------

    def _on_play_toggle(self, playing: bool) -> None:
        self._playing = playing
        if playing:
            if self._current_frame >= self._n_frames - 1:
                self._seek_to_frame(0)
            self._schedule_next_tick()
        else:
            self._playback_timer.stop()

    def _on_speed_changed(self, _speed: float) -> None:
        pass  # interval recalculated each tick via single-shot timer

    def _playback_interval(self) -> int:
        return max(1, int(1000 / (self._fps * self._timeline.speed())))

    def _schedule_next_tick(self) -> None:
        if self._playing:
            self._playback_timer.start(self._playback_interval())

    def _on_playback_tick(self) -> None:
        if not self._playing:
            return
        next_frame = self._current_frame + 1
        if next_frame >= self._n_frames:
            self._playing = False
            self._timeline._set_playing(False)
            return
        self._seek_to_frame(next_frame)
        self._schedule_next_tick()

    # -- Export --------------------------------------------------------------

    def _on_export_clip(self) -> None:
        if self._vm is None or self._cap is None:
            return

        if self._region_cb.isChecked():
            t_start, t_end = self._trace_panel.get_region()
            start_sample = int(t_start * self._sr)
            end_sample = int(t_end * self._sr)
            start_frame = int(np.searchsorted(self._frame_samples, start_sample))
            end_frame = int(np.searchsorted(self._frame_samples, end_sample))
        else:
            start_frame = 0
            end_frame = self._n_frames

        start_frame = max(0, min(start_frame, self._n_frames - 1))
        end_frame = max(start_frame + 1, min(end_frame, self._n_frames))

        h5_stem = Path(self._h5_path).stem
        default_out = str(Path(self._h5_path).parent / f"{h5_stem}_clip.avi")

        dlg = ExportDialog(
            self,
            self._video_path,
            self._vm, self._sr, self._frame_samples,
            start_frame, end_frame, self._fps,
            self._y_lo, self._y_hi, self._rmp,
            default_out,
        )
        dlg.exec()

    # -- Cleanup -------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._playback_timer.stop()
        if self._cap is not None:
            self._cap.release()
        super().closeEvent(event)


# ============================================================================
# Entry point
# ============================================================================

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Ephys Analysis")
    app.setStyleSheet(_STYLESHEET)

    pg.setConfigOptions(antialias=False)

    window = AnalysisWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

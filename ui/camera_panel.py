"""
CameraPanel — live Basler camera preview + TTL trigger settings.

Exposes two sub-widgets that can be placed independently:
    preview_widget:   live camera image (ImageView)
    ttl_widget:       frame rate / exposure spinboxes + Apply button
"""

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_EXPOSURE_MS, DEFAULT_FRAME_RATE_HZ
from utils.stimulus_generator import get_actual_frame_rate


class CameraPanel(QWidget):
    """
    Camera preview + TTL configuration panel.

    Signals:
        ttl_config_changed(float, float):  (frame_rate_hz, exposure_ms)
    """

    ttl_config_changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame_rate_hz = DEFAULT_FRAME_RATE_HZ
        self._exposure_ms   = DEFAULT_EXPOSURE_MS
        self._first_frame   = True

        self._preview_widget = QWidget()
        self._ttl_widget     = QWidget()
        self._build_preview()
        self._build_ttl()

        # This widget itself is invisible — MainWindow places sub-widgets.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def preview_widget(self) -> QWidget:
        return self._preview_widget

    @property
    def ttl_widget(self) -> QWidget:
        return self._ttl_widget

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def update_frame(self, frame: np.ndarray) -> None:
        """
        Called from the GUI thread (via Qt signal) when a new camera frame arrives.
        frame: HxW (mono) or HxWx3 (color) uint8 / uint16 numpy array.
        """
        auto_range = self._first_frame
        if frame.ndim == 2:
            self._image_view.setImage(
                frame.T, autoLevels=False, autoRange=auto_range,
                levels=(0, frame.max() or 1),
            )
        else:
            self._image_view.setImage(
                frame.transpose(1, 0, 2), autoRange=auto_range,
            )
        self._first_frame = False

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_preview(self) -> None:
        layout = QVBoxLayout(self._preview_widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        preview_box = QGroupBox("Camera Preview")
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(2, 2, 2, 2)

        self._image_view = pg.ImageView()
        self._image_view.ui.roiBtn.hide()
        self._image_view.ui.menuBtn.hide()
        self._image_view.ui.histogram.hide()
        self._image_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._image_view.setImage(np.zeros((100, 100), dtype=np.uint8))
        preview_layout.addWidget(self._image_view)

        layout.addWidget(preview_box)

    def _build_ttl(self) -> None:
        layout = QVBoxLayout(self._ttl_widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        ttl_box = QGroupBox("Camera Trigger (TTL on AO1)")
        form = QFormLayout(ttl_box)
        form.setSpacing(4)

        self._fps_spin = QDoubleSpinBox()
        self._fps_spin.setRange(0.1, 200.0)
        self._fps_spin.setDecimals(2)
        self._fps_spin.setValue(DEFAULT_FRAME_RATE_HZ)
        self._fps_spin.setSuffix(" Hz")
        self._fps_spin.valueChanged.connect(self._update_actual_fps_label)
        form.addRow("Frame rate", self._fps_spin)

        self._actual_fps_lbl = QLabel()
        form.addRow("Actual rate", self._actual_fps_lbl)

        self._exp_spin = QDoubleSpinBox()
        self._exp_spin.setRange(0.01, 5000.0)
        self._exp_spin.setDecimals(2)
        self._exp_spin.setValue(DEFAULT_EXPOSURE_MS)
        self._exp_spin.setSuffix(" ms")
        form.addRow("Exposure", self._exp_spin)

        self._apply_btn = QPushButton("Apply TTL Settings")
        self._apply_btn.clicked.connect(self._on_apply)
        form.addRow(self._apply_btn)

        layout.addWidget(ttl_box)
        layout.addStretch()

        self._update_actual_fps_label()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _update_actual_fps_label(self) -> None:
        actual = get_actual_frame_rate(self._fps_spin.value())
        self._actual_fps_lbl.setText(f"{actual:.3f} Hz")

    def _on_apply(self) -> None:
        self._frame_rate_hz = self._fps_spin.value()
        self._exposure_ms   = self._exp_spin.value()
        self.ttl_config_changed.emit(self._frame_rate_hz, self._exposure_ms)

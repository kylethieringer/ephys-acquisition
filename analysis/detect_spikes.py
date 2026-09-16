"""Spike detection for current-clamp membrane voltage traces.

A single entry point :func:`detect_spikes` returns the sample indices of
detected action potentials in a 1-D membrane voltage trace.  Today only
``method="find_peaks"`` is implemented (a thin wrapper around
:func:`scipy.signal.find_peaks`).  New methods (e.g. ``"dvdt"``) can be
added as additional branches without changing call sites.

Example::

    from analysis.detect_spikes import detect_spikes
    idx = detect_spikes(vm_mV, sr=20000)
    n_spikes = len(idx)
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import find_peaks, savgol_filter

# Adaptive-detector defaults, calibrated against recordings with known
# answers: fre071 (attenuated APs at 300 pA), fre027 (step-transition
# artifacts), fre047 (a silent cell), fre021/fre054 (clean spiking).
DETREND_MS = 20.0        # median-filter window used to remove slow drift
NOISE_K = 6.0            # prominence bar, in robust SDs of the residual
DVDT_K = 4.0             # rate-of-rise bar, in robust SDs of d(residual)/dt
DVDT_SMOOTH_MS = 0.5     # smoothing applied before differentiating
# No real AP in this dataset falls below ~3 mV prominence, even fully
# attenuated (fre071 at 300 pA: 3.1-8.7 mV).  Without this floor the
# adaptive bar scales down into the noise on quiet, silent cells and
# starts reporting sub-millivolt wiggles as spikes (fre048, fre029).
PROMINENCE_FLOOR_MV = 2.5


def _robust_sd(x: np.ndarray) -> float:
    """Median absolute deviation, scaled to a Gaussian SD equivalent."""
    return float(np.median(np.abs(x - np.median(x))) * 1.4826)


def detect_spikes(
    vm_mV: np.ndarray,
    sr: int,
    method: str = "adaptive",
    height_mV: float | None = None,
    prominence_mV: float = 7.0,
    min_distance_ms: float = 2.0,
    detrend_ms: float = DETREND_MS,
    noise_k: float = NOISE_K,
    dvdt_k: float = DVDT_K,
    dvdt_smooth_ms: float = DVDT_SMOOTH_MS,
    prominence_floor_mV: float = PROMINENCE_FLOOR_MV,
) -> np.ndarray:
    """Return spike sample indices in ``vm_mV``.

    Detection never uses an absolute voltage floor: fly motor-neuron APs
    often peak well below 0 mV (e.g. DVMN APs can crest near -15 mV), so
    a fixed height is unreliable across cells.

    ``method="adaptive"`` (default) detrends the trace, sets its
    prominence bar from that trace's own noise, and additionally requires
    an AP-like rate of rise.  A *fixed* prominence bar cannot serve the
    whole dataset: APs attenuate under strong current injection (in
    fre071 the same cell's APs fall from ~9-12 mV prominence at 150 pA to
    ~4-9 mV at 300 pA), so a floor high enough to reject slow humps and
    step-transition artifacts in noisy recordings also discards real
    spikes wherever the cell is driven hardest.  Scaling to local noise
    handles the first problem; the rate-of-rise bar handles the second,
    since slow baseline excursions can be as tall as an attenuated AP but
    never rise as fast.

    ``method="find_peaks"`` is the original fixed-``prominence_mV``
    behaviour, kept for comparison and for callers that need a threshold
    they control.

    Parameters
    ----------
    vm_mV
        1-D membrane potential trace, in millivolts.
    sr
        Sampling rate in Hz.
    method
        ``"adaptive"`` (default) or ``"find_peaks"``.
    height_mV
        Optional absolute peak-height floor in mV.  ``None`` (default)
        disables the floor; use it only if you also want to reject
        sub-threshold bumps below a known Vm.
    prominence_mV
        Minimum peak prominence in mV.  Used only by ``"find_peaks"``.
    min_distance_ms
        Minimum separation between successive spikes, in ms.
    detrend_ms
        Median-filter window used to remove slow drift before detection.
        Must be comfortably longer than an AP and shorter than the
        baseline excursions being rejected.  Adaptive only.
    noise_k
        Prominence bar, in robust SDs of the detrended trace.  Adaptive
        only.
    dvdt_k
        Rate-of-rise bar, in robust SDs of the smoothed derivative.
        Adaptive only.
    dvdt_smooth_ms
        Smoothing applied before differentiating.  Without it the
        derivative's noise scales with the recording's bandwidth rather
        than with spike shape.  Adaptive only.
    prominence_floor_mV
        Absolute lower bound on the adaptive prominence bar, so a very
        quiet trace cannot drive the threshold into the digitisation
        noise.  Adaptive only.

    Returns
    -------
    np.ndarray
        Sample indices (into ``vm_mV``) of detected spike peaks.
    """
    distance = max(1, int(round(min_distance_ms / 1000.0 * sr)))

    if method == "find_peaks":
        peaks, _ = find_peaks(
            vm_mV,
            height=height_mV,
            prominence=prominence_mV,
            distance=distance,
        )
        return peaks

    if method == "adaptive":
        # 1. Remove slow drift.  The window is far longer than an AP, so
        #    spikes survive in the residual while baseline sag, the
        #    post-step relaxation, and slow humps do not.
        window = max(3, int(round(detrend_ms / 1000.0 * sr)) | 1)
        if window >= len(vm_mV):
            return np.array([], dtype=int)
        residual = vm_mV - median_filter(vm_mV, size=window)

        # 2. Prominence bar, scaled to this trace's own noise.
        threshold = max(noise_k * _robust_sd(residual), prominence_floor_mV)
        peaks, _ = find_peaks(
            residual,
            height=height_mV,
            prominence=threshold,
            distance=distance,
        )
        if not len(peaks):
            return peaks

        # 3. Rate-of-rise bar.  Slow humps and step-transition artifacts
        #    can clear the prominence bar; they do not rise like an AP.
        #    The derivative is taken from a lightly smoothed residual:
        #    differentiating raw samples amplifies high-frequency noise in
        #    proportion to its bandwidth, which would make this bar depend
        #    on the recording's filtering rather than on spike shape.
        smooth = max(5, int(round(dvdt_smooth_ms / 1000.0 * sr)) | 1)
        if smooth < len(residual):
            dvdt = savgol_filter(residual, smooth, 2, deriv=1,
                                 delta=1.0 / sr) / 1000.0
        else:
            dvdt = np.diff(residual, prepend=residual[0]) * sr / 1000.0
        look = max(1, int(round(2.0 / 1000.0 * sr)))
        rise = np.array([dvdt[max(0, p - look):p + 1].max() for p in peaks])
        return peaks[rise >= dvdt_k * _robust_sd(dvdt)]

    raise ValueError(f"Unknown spike-detection method: {method!r}")

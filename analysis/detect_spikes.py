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
from scipy.signal import find_peaks


def detect_spikes(
    vm_mV: np.ndarray,
    sr: int,
    method: str = "find_peaks",
    height_mV: float | None = None,
    prominence_mV: float = 7.0,
    min_distance_ms: float = 2.0,
) -> np.ndarray:
    """Return spike sample indices in ``vm_mV``.

    Detection is **prominence-based** by default, so it does not depend
    on the cell's resting potential.  Fly motor-neuron APs often peak
    well below 0 mV (e.g. DVMN APs can crest near -15 mV), which makes
    an absolute voltage floor unreliable across cells.  Prominence
    measures how far a peak rises above the surrounding signal, so the
    same threshold works regardless of where the cell sits.

    Parameters
    ----------
    vm_mV
        1-D membrane potential trace, in millivolts.
    sr
        Sampling rate in Hz.
    method
        Detection algorithm.  Currently only ``"find_peaks"``.
    height_mV
        Optional absolute peak-height floor in mV.  ``None`` (default)
        disables the floor; use it only if you also want to reject
        sub-threshold bumps below a known Vm.
    prominence_mV
        Minimum peak prominence in mV.  Primary spike criterion.
    min_distance_ms
        Minimum separation between successive spikes, in ms.

    Returns
    -------
    np.ndarray
        Sample indices (into ``vm_mV``) of detected spike peaks.
    """
    if method == "find_peaks":
        distance = max(1, int(round(min_distance_ms / 1000.0 * sr)))
        peaks, _ = find_peaks(
            vm_mV,
            height=height_mV,
            prominence=prominence_mV,
            distance=distance,
        )
        return peaks

    raise ValueError(f"Unknown spike-detection method: {method!r}")

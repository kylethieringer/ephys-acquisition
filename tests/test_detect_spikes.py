"""Tests for :mod:`analysis.detect_spikes`.

Covers the adaptive detector, which must recover attenuated action
potentials (small but fast) while rejecting the slow baseline humps and
step-transition artifacts that a fixed prominence floor lets through.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from analysis.detect_spikes import detect_spikes

SR = 20_000
DURATION_S = 0.5


def _trace(rest_mV: float = -55.0, noise_mV: float = 0.05, seed: int = 0):
    rng = np.random.default_rng(seed)
    n = int(SR * DURATION_S)
    return rest_mV + rng.normal(0.0, noise_mV, n)


def _add_spike(vm: np.ndarray, at_ms: float, amp_mV: float,
               width_ms: float = 1.0) -> None:
    """Add a fast, narrow AP-like transient."""
    c = int(at_ms / 1000.0 * SR)
    half = max(2, int(width_ms / 1000.0 * SR))
    idx = np.arange(c - 3 * half, c + 3 * half)
    idx = idx[(idx >= 0) & (idx < len(vm))]
    vm[idx] += amp_mV * np.exp(-0.5 * ((idx - c) / (half / 2.0)) ** 2)


def _add_hump(vm: np.ndarray, at_ms: float, amp_mV: float,
              width_ms: float = 40.0) -> None:
    """Add a slow baseline excursion - not a spike."""
    c = int(at_ms / 1000.0 * SR)
    half = max(2, int(width_ms / 1000.0 * SR))
    idx = np.arange(max(0, c - 3 * half), min(len(vm), c + 3 * half))
    vm[idx] += amp_mV * np.exp(-0.5 * ((idx - c) / (half / 2.0)) ** 2)


def test_detects_large_fast_spikes():
    vm = _trace()
    for t in (50, 150, 250, 350):
        _add_spike(vm, t, amp_mV=25.0)
    assert len(detect_spikes(vm, SR, method="adaptive")) == 4


def test_detects_attenuated_spikes_on_depolarized_plateau():
    """The fre071 300 pA case: small, fast APs on a depolarized baseline.

    A fixed 7 mV prominence floor misses these entirely.
    """
    vm = _trace(rest_mV=8.0, noise_mV=0.35)
    for t in (60, 140, 220, 300, 380):
        _add_spike(vm, t, amp_mV=5.0)
    assert len(detect_spikes(vm, SR, method="adaptive")) == 5
    # the old fixed threshold is what this test exists to guard against
    assert len(detect_spikes(vm, SR, method="find_peaks",
                             prominence_mV=7.0)) < 5


def test_rejects_slow_humps():
    """Slow baseline excursions must not be counted, however tall."""
    vm = _trace()
    for t in (100, 250, 400):
        _add_hump(vm, t, amp_mV=8.0)
    assert len(detect_spikes(vm, SR, method="adaptive")) == 0


def test_separates_spikes_from_humps_in_one_trace():
    vm = _trace()
    for t in (100, 300):
        _add_hump(vm, t, amp_mV=8.0)
    for t in (180, 380):
        _add_spike(vm, t, amp_mV=6.0)
    assert len(detect_spikes(vm, SR, method="adaptive")) == 2


def test_threshold_adapts_to_noise():
    """The same spikes stay detectable when the noise floor rises."""
    for noise in (0.05, 0.2, 0.5):
        vm = _trace(noise_mV=noise, seed=1)
        for t in (80, 200, 320):
            _add_spike(vm, t, amp_mV=12.0)
        assert len(detect_spikes(vm, SR, method="adaptive")) == 3, (
            f"failed at noise={noise} mV"
        )


def test_silent_trace_reports_no_spikes():
    assert len(detect_spikes(_trace(noise_mV=0.3), SR, method="adaptive")) == 0


def test_fixed_method_still_available():
    """Back-compat: the original fixed-prominence path is unchanged."""
    vm = _trace()
    for t in (50, 150, 250):
        _add_spike(vm, t, amp_mV=25.0)
    assert len(detect_spikes(vm, SR, method="find_peaks",
                             prominence_mV=7.0)) == 3


def test_unknown_method_raises():
    with pytest.raises(ValueError, match="Unknown spike-detection method"):
        detect_spikes(_trace(), SR, method="nope")


def test_quiet_silent_cell_reports_no_spikes():
    """Regression: a low-noise, non-spiking cell must stay at zero.

    The adaptive bar scales to local noise, so on a very quiet trace it
    would drop into the sub-millivolt range and start reporting membrane
    wiggles as spikes.  ``prominence_floor_mV`` is what prevents that.
    """
    vm = _trace(noise_mV=0.04, seed=3)
    for t in (60, 140, 220, 300, 380):          # sub-mV fluctuations
        _add_spike(vm, t, amp_mV=0.8)
    assert len(detect_spikes(vm, SR, method="adaptive")) == 0


def test_floor_does_not_reject_attenuated_spikes():
    """The floor must sit below the smallest real AP (~3 mV prominence)."""
    vm = _trace(rest_mV=8.0, noise_mV=0.3, seed=4)
    for t in (60, 160, 260, 360):
        _add_spike(vm, t, amp_mV=4.0)
    assert len(detect_spikes(vm, SR, method="adaptive")) == 4

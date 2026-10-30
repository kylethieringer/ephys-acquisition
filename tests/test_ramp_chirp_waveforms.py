"""Ramp and chirp waveform generation.

Covers the pure pA generators in :mod:`utils.stimulus_generator` and how
:mod:`acquisition.trial_waveforms` places them in a trial.  All sample
indices assume the 20 kHz acquisition rate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from acquisition.trial_protocol import (
    HyperpolarizationParams,
    StimulusDefinition,
    TrialProtocol,
)
from acquisition.trial_waveforms import build_cc_trial_waveform, build_trial_waveform
from utils.stimulus_generator import generate_chirp_pa_array, generate_ramp_pa_array

SR = 20_000


def _hz_at(a: np.ndarray, t_s: float) -> float:
    """Frequency of a zero-mean sine sweep near ``t_s``, from zero crossings."""
    crossings = np.flatnonzero(np.diff(np.signbit(a).astype(int))) / SR
    midpoints = (crossings[:-1] + crossings[1:]) / 2
    half_cycle_hz = 0.5 / np.diff(crossings)
    return float(half_cycle_hz[np.argmin(np.abs(midpoints - t_s))])


def test_ramp_series_shape_and_values():
    a = generate_ramp_pa_array(0, 50, 150, 50, 100, 0, False, 50)
    assert a.size == 3 * (2000 + 1000)
    assert a[1999] == 50
    assert a[2000] == 0
    assert a[3000 + 1999] == 100
    assert a[8999] == 0


def test_ramp_gap_holds_start_current():
    a = generate_ramp_pa_array(-20, 50, 50, 50, 100, 0, False, 50)
    assert a[0] == -20
    assert a[2000] == -20
    assert a[-1] == -20


def test_ramp_hold_and_ramp_down():
    a = generate_ramp_pa_array(-20, 50, 50, 50, 100, 10, True, 0)
    assert a.size == 2000 + 200 + 2000
    assert a[2100] == 50
    assert a[-1] == -20


def test_ramp_rises_linearly():
    a = generate_ramp_pa_array(0, 100, 100, 50, 100, 0, False, 0)
    assert a[1000] == pytest.approx(50, abs=0.1)


def test_ramp_peaks_do_not_overshoot():
    a = generate_ramp_pa_array(0, 10, 25, 10, 10, 0, False, 0)
    assert a.max() == 20


def test_ramp_repeats_tile():
    one = generate_ramp_pa_array(0, 50, 100, 50, 10, 0, False, 5)
    two = generate_ramp_pa_array(0, 50, 100, 50, 10, 0, False, 5, repeats=2)
    assert np.array_equal(two, np.tile(one, 2))


def test_linear_chirp_frequency():
    a = generate_chirp_pa_array(10, 100, 1000, "linear", 20, 0, 0)
    assert _hz_at(a, 0.5) == pytest.approx(55, rel=0.02)
    assert _hz_at(a, 0.9) == pytest.approx(91, rel=0.02)


def test_log_chirp_frequency():
    a = generate_chirp_pa_array(1, 100, 2000, "log", 20, 0, 0)
    assert _hz_at(a, 1.0) == pytest.approx(10, rel=0.05)
    assert _hz_at(a, 1.5) == pytest.approx(31.6, rel=0.05)


def test_downward_chirp_frequency():
    a = generate_chirp_pa_array(100, 10, 1000, "linear", 20, 0, 0)
    assert _hz_at(a, 0.1) == pytest.approx(91, rel=0.02)


def test_chirp_offset_amplitude_gap_and_start_phase():
    a = generate_chirp_pa_array(1, 10, 1000, "linear", 20, 5, 100, repeats=2)
    assert a.size == 2 * (20000 + 2000)
    assert a[0] == pytest.approx(5)
    assert a[:20000].max() <= 25 + 1e-9
    assert a[:20000].min() >= -15 - 1e-9
    assert np.all(a[20000:22000] == 0)


def test_chirp_rejects_unknown_spacing():
    with pytest.raises(ValueError):
        generate_chirp_pa_array(1, 10, 100, "cubic", 20, 0, 0)


def test_ramp_trial_holds_start_after_hyperpol_and_ends_at_zero():
    s = StimulusDefinition(
        type="ramp", ramp_start_pA=-20.0, ramp_min_peak_pA=50.0,
        ramp_max_peak_pA=50.0, ramp_duration_ms=100.0, gap_ms=50.0,
    )
    a = build_cc_trial_waveform(s, 10, 10, HyperpolarizationParams(-50.0, 100.0))
    assert a[199] == 0                       # end of pre
    assert a[2200] == pytest.approx(-0.05)   # gap after hyperpol, -20 pA at 400 pA/V
    assert a[-201] == pytest.approx(-0.05)   # last gap
    assert a[-1] == 0                        # post


def test_ramp_trial_keeps_zero_peak_step_values():
    s = StimulusDefinition(
        type="ramp", ramp_start_pA=-20.0, ramp_min_peak_pA=0.0,
        ramp_max_peak_pA=0.0, ramp_duration_ms=100.0, gap_ms=0.0,
    )
    a = build_cc_trial_waveform(s, 0, 0, None)
    assert a[-1] == 0.0
    assert a[0] == pytest.approx(-0.05)


def test_chirp_trial_gap_is_zero():
    s = StimulusDefinition(type="chirp", chirp_duration_ms=100.0, gap_ms=50.0)
    a = build_cc_trial_waveform(s, 0, 0, HyperpolarizationParams(-50.0, 100.0))
    assert np.all(a[2000:3000] == 0)


def test_ramp_in_voltage_clamp_raises():
    p = TrialProtocol(clamp_mode="voltage_clamp", hyperpolarization=None)
    with pytest.raises(ValueError):
        build_trial_waveform(StimulusDefinition(type="ramp"), p)


def test_unknown_type_raises():
    with pytest.raises(ValueError):
        build_cc_trial_waveform(StimulusDefinition(type="noise"), 0, 0, None)


def test_voltage_step_in_cc_still_plays_steps():
    voltage_step = StimulusDefinition(type="voltage_step")
    step_protocol = StimulusDefinition(type="step_protocol")
    assert np.array_equal(
        build_cc_trial_waveform(voltage_step, 0, 0, None),
        build_cc_trial_waveform(step_protocol, 0, 0, None),
    )

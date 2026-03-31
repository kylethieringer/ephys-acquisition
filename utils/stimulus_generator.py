"""
Pure functions for generating stimulus and TTL waveforms.

No hardware dependencies — safe to import and test standalone.

All functions operate at :data:`~config.SAMPLE_RATE` (20 kHz) by default.
Waveforms are returned as 1-D ``float64`` NumPy arrays unless otherwise noted.

Waveform units
--------------
- **Stimulus functions** (``generate_ao0_waveform``, ``generate_staircase_pa_array``):
  see individual docstrings — some return pA, others return Volts.
- **TTL functions**: return Volts, switching between
  :data:`~config.TTL_HIGH_V` (5 V) and :data:`~config.TTL_LOW_V` (0 V).

Developer notes
---------------
``TTLChunkGenerator`` is the only stateful class here.  It is instantiated
inside :class:`~hardware.daq_worker.DAQWorker` and used exclusively from the
worker thread, so no locking is needed.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from config import SAMPLE_RATE, AO_PA_PER_VOLT, TTL_HIGH_V, TTL_LOW_V


# ---------------------------------------------------------------------------
# Step stimulus
# ---------------------------------------------------------------------------

def get_step_amplitudes(
    min_pa: float,
    max_pa: float,
    step_pa: float,
) -> list[float]:
    """Return the list of current step amplitudes from min to max inclusive.

    Args:
        min_pa: Minimum step amplitude in pA.
        max_pa: Maximum step amplitude in pA.
        step_pa: Step size between amplitudes in pA.  Must be positive.

    Returns:
        List of amplitudes in pA, starting at ``min_pa`` and increasing by
        ``step_pa`` until ``max_pa`` is reached (inclusive, subject to
        floating-point tolerance of 1e-9 pA).  Returns an empty list if
        ``step_pa <= 0``.
    """
    if step_pa <= 0:
        return []
    amps = []
    val = min_pa
    while val <= max_pa + 1e-9:
        amps.append(round(val, 6))
        val += step_pa
    return amps


def generate_preview_steps(
    min_pa: float,
    max_pa: float,
    step_pa: float,
    width_ms: float,
    gap_ms: float,
) -> tuple[NDArray[np.float64], list[NDArray[np.float64]]]:
    """Generate per-step arrays for the stimulus preview overlay plot.

    The step pulse is centered within the total duration (gap split equally
    before and after the pulse) so that all steps start at the same phase
    for visual comparison.

    Args:
        min_pa: Minimum step amplitude in pA.
        max_pa: Maximum step amplitude in pA.
        step_pa: Step size in pA.  Must be positive.
        width_ms: Duration each step is held in ms.
        gap_ms: Total gap duration surrounding the step in ms (split 50/50
            before and after the pulse in the preview).

    Returns:
        A 2-tuple ``(t_ms, step_traces)`` where:

        - ``t_ms``: 1-D float64 time array in ms, length =
          ``width_samples + gap_samples``.
        - ``step_traces``: list of 1-D float64 arrays in pA, one per
          amplitude step.  Each array is the same length as ``t_ms``.
    """
    amplitudes = get_step_amplitudes(min_pa, max_pa, step_pa)
    width_samples = max(1, int(width_ms / 1000.0 * SAMPLE_RATE))
    gap_samples   = max(0, int(gap_ms  / 1000.0 * SAMPLE_RATE))
    total_samples = width_samples + gap_samples

    t_ms = np.linspace(0.0, (total_samples / SAMPLE_RATE) * 1000.0, total_samples)

    # Center the pulse: split gap equally before and after
    pre_gap = gap_samples // 2
    traces = []
    for amp in amplitudes:
        trace = np.zeros(total_samples, dtype=np.float64)
        trace[pre_gap : pre_gap + width_samples] = amp
        traces.append(trace)

    return t_ms, traces


def generate_staircase_pa_array(
    min_pa: float,
    max_pa: float,
    step_pa: float,
    width_ms: float,
    gap_ms: float,
    repeats: int = 1,
) -> NDArray[np.float64]:
    """Generate a staircase current waveform in pA units (not scaled to Volts).

    The staircase consists of ``n_steps`` pulses from ``min_pa`` to
    ``max_pa``, each held for ``width_ms`` followed by ``gap_ms`` of
    silence.  The pattern is tiled ``repeats`` times.

    This function is used by :mod:`acquisition.trial_waveforms` so that the
    hyperpolarization pulse can be prepended *before* the pA→V scaling is
    applied, keeping scaling in one place.

    Args:
        min_pa: Minimum current amplitude in pA.
        max_pa: Maximum current amplitude in pA.
        step_pa: Step size in pA.  Must be positive.
        width_ms: Duration each step is held in ms.
        gap_ms: Silent gap between steps in ms.
        repeats: Number of times the full staircase pattern is tiled.

    Returns:
        1-D float64 array in pA.  Length =
        ``repeats × n_steps × (width_samples + gap_samples)``.
    """
    amplitudes    = get_step_amplitudes(min_pa, max_pa, step_pa)
    width_samples = max(1, int(width_ms / 1000.0 * SAMPLE_RATE))
    gap_samples   = max(0, int(gap_ms   / 1000.0 * SAMPLE_RATE))
    step_samples  = width_samples + gap_samples

    one_pass = np.zeros(len(amplitudes) * step_samples, dtype=np.float64)
    for i, amp_pa in enumerate(amplitudes):
        start = i * step_samples
        one_pass[start : start + width_samples] = amp_pa

    return np.tile(one_pass, repeats)


def generate_ao0_waveform(
    min_pa: float,
    max_pa: float,
    step_pa: float,
    width_ms: float,
    gap_ms: float,
) -> NDArray[np.float64]:
    """Generate a staircase ao0 command waveform in Volts for direct AO output.

    Converts pA → Volts using :data:`~config.AO_PA_PER_VOLT` (400 pA/V).
    Used by :class:`~ui.stimulus_panel.StimulusPanel` for single-pass
    quick stimulation.  For trial-based protocols use
    :func:`~acquisition.trial_waveforms.build_cc_trial_waveform` instead.

    Args:
        min_pa: Minimum current amplitude in pA.
        max_pa: Maximum current amplitude in pA.
        step_pa: Step size in pA.  Must be positive.
        width_ms: Duration each step is held in ms.
        gap_ms: Silent gap between steps in ms.

    Returns:
        1-D float64 array of ao0 voltages in V.  Length =
        ``n_steps × (width_samples + gap_samples)``.
    """
    amplitudes    = get_step_amplitudes(min_pa, max_pa, step_pa)
    width_samples = max(1, int(width_ms / 1000.0 * SAMPLE_RATE))
    gap_samples   = max(0, int(gap_ms  / 1000.0 * SAMPLE_RATE))
    step_samples  = width_samples + gap_samples

    waveform = np.zeros(len(amplitudes) * step_samples, dtype=np.float64)
    for i, amp_pa in enumerate(amplitudes):
        start = i * step_samples
        waveform[start : start + width_samples] = amp_pa / AO_PA_PER_VOLT
    return waveform


# ---------------------------------------------------------------------------
# TTL waveform
# ---------------------------------------------------------------------------

def get_actual_frame_rate(frame_rate_hz: float) -> float:
    """Return the achievable camera frame rate after integer period rounding.

    The DAQ samples at an integer rate (20 kHz), so the TTL period can only
    be an integer number of samples.  Requesting 33 Hz yields a period of
    ``round(20000 / 33) = 606`` samples, giving an actual rate of
    ``20000 / 606 ≈ 33.00 Hz``.  For most frame rates the error is negligible.

    Args:
        frame_rate_hz: Requested camera frame rate in Hz.

    Returns:
        Actual achievable frame rate in Hz after rounding the period to the
        nearest integer sample count.
    """
    period_samples = max(1, int(SAMPLE_RATE / frame_rate_hz))
    return SAMPLE_RATE / period_samples


def generate_ttl_period(
    frame_rate_hz: float,
    exposure_ms: float,
) -> NDArray[np.float64]:
    """Generate exactly one period of the TTL camera trigger square wave.

    The pulse is high for ``exposure_ms`` then low for the remainder of
    the period.  ``exposure_ms`` is clamped to ``[1 sample, period − 1 sample]``
    so the waveform always contains at least one low sample.

    Args:
        frame_rate_hz: Camera frame rate in Hz, determining the period length.
        exposure_ms: Duration the TTL is held high in ms.

    Returns:
        1-D float64 array of TTL voltages in V (values are
        :data:`~config.TTL_HIGH_V` or :data:`~config.TTL_LOW_V`).
        Length = ``int(SAMPLE_RATE / frame_rate_hz)`` samples.
    """
    period_samples   = max(1, int(SAMPLE_RATE / frame_rate_hz))
    exposure_samples = int(exposure_ms / 1000.0 * SAMPLE_RATE)
    exposure_samples = max(1, min(exposure_samples, period_samples - 1))

    period = np.full(period_samples, TTL_LOW_V, dtype=np.float64)
    period[:exposure_samples] = TTL_HIGH_V
    return period


def generate_ttl_waveform(
    frame_rate_hz: float,
    exposure_ms: float,
    n_samples: int,
) -> NDArray[np.float64]:
    """Tile the TTL period to fill exactly ``n_samples``.

    Args:
        frame_rate_hz: Camera frame rate in Hz.
        exposure_ms: Exposure duration in ms.
        n_samples: Desired total length of the output array in samples.

    Returns:
        1-D float64 array of TTL voltages in V, length = ``n_samples``.
    """
    period   = generate_ttl_period(frame_rate_hz, exposure_ms)
    n_tiles  = int(np.ceil(n_samples / len(period)))
    tiled    = np.tile(period, n_tiles)
    return tiled[:n_samples]


# ---------------------------------------------------------------------------
# Combined AO waveform
# ---------------------------------------------------------------------------

def build_combined_ao_waveform(
    ao0: NDArray[np.float64],
    frame_rate_hz: float,
    exposure_ms: float,
) -> NDArray[np.float64]:
    """Combine ao0 (command current) and ao1 (TTL) into a (2, N) array.

    The result is suitable for writing to the NI AO task which controls
    both ao0 (command current) and ao1 (TTL output).

    Args:
        ao0: 1-D float64 array of ao0 voltages in V.  Already scaled from pA.
        frame_rate_hz: Camera frame rate in Hz for the TTL waveform.
        exposure_ms: Exposure duration in ms for the TTL waveform.

    Returns:
        2-D float64 array, shape ``(2, len(ao0))``.  Row 0 = ao0, Row 1 = ao1.
    """
    n = len(ao0)
    ao1 = generate_ttl_waveform(frame_rate_hz, exposure_ms, n)
    return np.vstack([ao0, ao1])


# ---------------------------------------------------------------------------
# Stateful TTL chunk generator (used inside DAQWorker)
# ---------------------------------------------------------------------------

class TTLChunkGenerator:
    """Generate phase-continuous TTL voltage chunks for streaming output.

    Maintains internal phase state across calls so that the TTL square wave
    is continuous even when the waveform is read in fixed-size chunks.

    Thread-safety: used exclusively from the DAQWorker thread — no locking
    is needed.

    Attributes:
        _period_samples: TTL period in samples.
        _exposure_samples: Number of high-voltage samples per period.
        _phase: Current phase offset within the period (samples).
    """

    def __init__(self, frame_rate_hz: float, exposure_ms: float) -> None:
        """Initialise the generator for a given frame rate and exposure.

        Args:
            frame_rate_hz: Camera frame rate in Hz.
            exposure_ms: Camera exposure duration in ms.  Clamped to
                ``[1 sample, period − 1 sample]``.
        """
        self._period_samples   = max(1, int(SAMPLE_RATE / frame_rate_hz))
        self._exposure_samples = int(exposure_ms / 1000.0 * SAMPLE_RATE)
        self._exposure_samples = max(1, min(self._exposure_samples, self._period_samples - 1))
        self._phase            = 0

    def reconfigure(self, frame_rate_hz: float, exposure_ms: float) -> None:
        """Update the frame rate and exposure, resetting the phase to zero.

        Args:
            frame_rate_hz: New camera frame rate in Hz.
            exposure_ms: New exposure duration in ms.
        """
        self._period_samples   = max(1, int(SAMPLE_RATE / frame_rate_hz))
        self._exposure_samples = int(exposure_ms / 1000.0 * SAMPLE_RATE)
        self._exposure_samples = max(1, min(self._exposure_samples, self._period_samples - 1))
        self._phase            = 0

    def next_chunk(self, n_samples: int) -> NDArray[np.float64]:
        """Return the next ``n_samples`` of the TTL waveform.

        Updates the internal phase so that the next call begins exactly
        where this one ended — the waveform is phase-continuous across calls.

        Args:
            n_samples: Number of samples to generate.

        Returns:
            1-D float64 array of TTL voltages in V, length = ``n_samples``.
        """
        indices = (np.arange(n_samples) + self._phase) % self._period_samples
        chunk   = np.where(indices < self._exposure_samples, TTL_HIGH_V, TTL_LOW_V)
        self._phase = (self._phase + n_samples) % self._period_samples
        return chunk.astype(np.float64)

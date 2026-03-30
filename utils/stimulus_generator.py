"""
Pure functions for generating stimulus and TTL waveforms.
No hardware dependencies — safe to import and test standalone.
"""

import numpy as np
from config import SAMPLE_RATE, AO_PA_PER_VOLT, TTL_HIGH_V, TTL_LOW_V


# ---------------------------------------------------------------------------
# Step stimulus
# ---------------------------------------------------------------------------

def get_step_amplitudes(min_pa: float, max_pa: float, step_pa: float) -> list[float]:
    """Return list of step amplitudes in pA from min to max inclusive."""
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
) -> tuple[np.ndarray, list[np.ndarray]]:
    """
    Generate per-step arrays for the stimulus preview overlay plot.
    The step pulse is centered within the total duration (gap split equally
    before and after the pulse).

    Returns:
        t_ms: 1D time array in milliseconds (length = width_samples + gap_samples)
        step_traces: list of 1D arrays in pA, one per amplitude step
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


def generate_ao0_waveform(
    min_pa: float,
    max_pa: float,
    step_pa: float,
    width_ms: float,
    gap_ms: float,
) -> np.ndarray:
    """
    Generate the full ao0 voltage waveform for current injection.
    Converts pA → Volts using AO_PA_PER_VOLT.

    Returns:
        1D float64 array of ao0 voltages (length = n_steps × step_samples)
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
    """Return the achievable frame rate after integer period rounding."""
    period_samples = max(1, int(SAMPLE_RATE / frame_rate_hz))
    return SAMPLE_RATE / period_samples


def generate_ttl_period(frame_rate_hz: float, exposure_ms: float) -> np.ndarray:
    """
    Generate exactly one period of the TTL square wave.

    Returns:
        1D float64 array of TTL voltages, length = period_samples
    """
    period_samples   = max(1, int(SAMPLE_RATE / frame_rate_hz))
    exposure_samples = int(exposure_ms / 1000.0 * SAMPLE_RATE)
    exposure_samples = max(1, min(exposure_samples, period_samples - 1))

    period = np.full(period_samples, TTL_LOW_V, dtype=np.float64)
    period[:exposure_samples] = TTL_HIGH_V
    return period


def generate_ttl_waveform(frame_rate_hz: float, exposure_ms: float, n_samples: int) -> np.ndarray:
    """
    Tile the TTL period to fill exactly n_samples.

    Returns:
        1D float64 array of TTL voltages, length = n_samples
    """
    period   = generate_ttl_period(frame_rate_hz, exposure_ms)
    n_tiles  = int(np.ceil(n_samples / len(period)))
    tiled    = np.tile(period, n_tiles)
    return tiled[:n_samples]


# ---------------------------------------------------------------------------
# Combined AO waveform
# ---------------------------------------------------------------------------

def build_combined_ao_waveform(
    ao0: np.ndarray,
    frame_rate_hz: float,
    exposure_ms: float,
) -> np.ndarray:
    """
    Combine ao0 (command current) and ao1 (TTL) into a (2, N) array
    suitable for writing to the NI AO task.

    ao0 must already be in Volts.
    ao1 is generated to match the length of ao0.

    Returns:
        shape (2, N) — row 0 = ao0, row 1 = ao1
    """
    n = len(ao0)
    ao1 = generate_ttl_waveform(frame_rate_hz, exposure_ms, n)
    return np.vstack([ao0, ao1])


# ---------------------------------------------------------------------------
# Stateful TTL chunk generator (used inside DAQWorker)
# ---------------------------------------------------------------------------

class TTLChunkGenerator:
    """
    Generates TTL voltage chunks while maintaining phase continuity across calls.
    Thread-safe: only used from the DAQWorker thread.
    """

    def __init__(self, frame_rate_hz: float, exposure_ms: float):
        self._period_samples   = max(1, int(SAMPLE_RATE / frame_rate_hz))
        self._exposure_samples = int(exposure_ms / 1000.0 * SAMPLE_RATE)
        self._exposure_samples = max(1, min(self._exposure_samples, self._period_samples - 1))
        self._phase            = 0

    def reconfigure(self, frame_rate_hz: float, exposure_ms: float) -> None:
        self._period_samples   = max(1, int(SAMPLE_RATE / frame_rate_hz))
        self._exposure_samples = int(exposure_ms / 1000.0 * SAMPLE_RATE)
        self._exposure_samples = max(1, min(self._exposure_samples, self._period_samples - 1))
        self._phase            = 0

    def next_chunk(self, n_samples: int) -> np.ndarray:
        indices = (np.arange(n_samples) + self._phase) % self._period_samples
        chunk   = np.where(indices < self._exposure_samples, TTL_HIGH_V, TTL_LOW_V)
        self._phase = (self._phase + n_samples) % self._period_samples
        return chunk.astype(np.float64)

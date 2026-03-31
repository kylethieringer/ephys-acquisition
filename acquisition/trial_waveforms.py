"""
AO waveform builders for trial-based acquisition.

No Qt or hardware dependencies — pure numpy functions.

Each builder returns a 1D float64 array in Volts, sized to exactly
    pre_samples + stim_samples + post_samples
where stim_samples depends on the stimulus type.

Current-clamp trial waveform layout:
    [zeros × pre_samples]
    [hyperpol_pulse × hyperpol_samples]   ← access-resistance measurement
    [staircase waveform × staircase_samples]
    [zeros × post_samples]

The hyperpol pulse occupies the first part of the stimulus window.
TTL fires at trial start (covering pre + stim + post).

Voltage-clamp trial waveform layout:
    [zeros × pre_samples]                ← AO = 0; holding set on amplifier
    [step_mV/ao_mv_per_volt × step_samples]
    [zeros × post_samples]
"""

from __future__ import annotations

import numpy as np

from config import AO_MV_PER_VOLT, AO_PA_PER_VOLT, SAMPLE_RATE
from acquisition.trial_protocol import HyperpolarizationParams, StimulusDefinition, TrialProtocol
from utils.stimulus_generator import generate_staircase_pa_array


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ms_to_samples(ms: float, sample_rate: int = SAMPLE_RATE) -> int:
    return max(0, int(ms / 1000.0 * sample_rate))


# ---------------------------------------------------------------------------
# Current-clamp waveform
# ---------------------------------------------------------------------------

def build_cc_trial_waveform(
    stim_def: StimulusDefinition,
    pre_ms: float,
    post_ms: float,
    hyperpol: HyperpolarizationParams | None,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """
    Build a full CC trial AO waveform (Volts).

    Structure:
        [zeros × pre_ms]
        [hyperpol pulse × hyperpol.duration_ms]   ← when hyperpol is not None
        [zeros × gap_ms]                          ← gap between hyperpol and steps
        [staircase steps]
        [zeros × post_ms]

    The gap_ms from the stimulus definition is reused as the silence between
    the hyperpolarisation pulse and the first current step.
    """
    pre_samples  = _ms_to_samples(pre_ms,  sample_rate)
    post_samples = _ms_to_samples(post_ms, sample_rate)

    # Hyperpolarisation pulse and trailing gap (pA → Volts)
    if hyperpol is not None:
        hyperpol_samples = _ms_to_samples(hyperpol.duration_ms, sample_rate)
        hyperpol_v = np.full(hyperpol_samples,
                             hyperpol.amplitude_pA / AO_PA_PER_VOLT,
                             dtype=np.float64)
        gap_samples = _ms_to_samples(stim_def.gap_ms or 0.0, sample_rate)
        gap_v = np.zeros(gap_samples, dtype=np.float64)
    else:
        hyperpol_v = np.empty(0, dtype=np.float64)
        gap_v      = np.empty(0, dtype=np.float64)

    # Staircase (pA → Volts)
    staircase_pa = generate_staircase_pa_array(
        min_pa   = stim_def.min_pA or 0.0,
        max_pa   = stim_def.max_pA or 0.0,
        step_pa  = stim_def.step_pA or 1.0,
        width_ms = stim_def.step_width_ms or 500.0,
        gap_ms   = stim_def.gap_ms or 0.0,
        repeats  = stim_def.staircase_repeats or 1,
    )
    staircase_v = staircase_pa / AO_PA_PER_VOLT

    return np.concatenate([
        np.zeros(pre_samples,  dtype=np.float64),
        hyperpol_v,
        gap_v,
        staircase_v,
        np.zeros(post_samples, dtype=np.float64),
    ])


# ---------------------------------------------------------------------------
# Voltage-clamp waveform
# ---------------------------------------------------------------------------

def build_vc_trial_waveform(
    stim_def: StimulusDefinition,
    pre_ms: float,
    post_ms: float,
    ao_mv_per_volt: float = AO_MV_PER_VOLT,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """
    Build a full VC trial AO waveform (Volts).

    The holding potential is set externally on the amplifier.
    AO = 0 V during pre/post; AO = step_mV / ao_mv_per_volt during step.

    Structure:
        [zeros × pre]
        [step_mV/ao_mv_per_volt × step_samples]
        [zeros × post]
    """
    pre_samples  = _ms_to_samples(pre_ms,  sample_rate)
    post_samples = _ms_to_samples(post_ms, sample_rate)
    step_samples = _ms_to_samples(stim_def.duration_ms or 500.0, sample_rate)

    step_v = (stim_def.step_mV or 0.0) / ao_mv_per_volt

    return np.concatenate([
        np.zeros(pre_samples,  dtype=np.float64),
        np.full(step_samples, step_v, dtype=np.float64),
        np.zeros(post_samples, dtype=np.float64),
    ])


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def build_trial_waveform(
    stim_def: StimulusDefinition,
    protocol: TrialProtocol,
) -> np.ndarray:
    """Return the AO waveform (Volts) for one trial of the given stimulus."""
    if protocol.clamp_mode == "voltage_clamp":
        return build_vc_trial_waveform(
            stim_def,
            pre_ms         = protocol.pre_ms,
            post_ms        = protocol.post_ms,
            ao_mv_per_volt = protocol.ao_mv_per_volt,
        )
    else:
        return build_cc_trial_waveform(
            stim_def,
            pre_ms   = protocol.pre_ms,
            post_ms  = protocol.post_ms,
            hyperpol = protocol.hyperpolarization,
        )

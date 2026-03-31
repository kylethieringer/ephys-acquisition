"""
AO waveform builders for trial-based acquisition.

No Qt or hardware dependencies — pure numpy functions.

Each builder returns a 1-D float64 array in **Volts** sized to exactly::

    pre_samples + stim_samples + post_samples

where ``stim_samples`` depends on the stimulus type.

Current-clamp (CC) trial waveform layout
-----------------------------------------
::

    [zeros × pre_samples]
    [hyperpol_pulse × hyperpol_samples]   ← access-resistance measurement
    [zeros × gap_samples]                 ← gap between hyperpol and staircase
    [staircase waveform × staircase_samples]
    [zeros × post_samples]

The hyperpolarization section is omitted when
:attr:`~acquisition.trial_protocol.TrialProtocol.hyperpolarization` is ``None``.
Camera TTL fires at trial start and covers the entire waveform (pre + stim + post).

Voltage-clamp (VC) trial waveform layout
-----------------------------------------
::

    [zeros × pre_samples]                ← AO = 0; holding set on amplifier
    [step_V × step_samples]              ← step_mV / ao_mv_per_volt
    [zeros × post_samples]

AO scaling
----------
- Current clamp: divide pA by :data:`~config.AO_PA_PER_VOLT` (400 pA/V).
- Voltage clamp: divide mV by :data:`~config.AO_MV_PER_VOLT` (20 mV/V, or
  the per-protocol ``ao_mv_per_volt`` field).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from config import AO_MV_PER_VOLT, AO_PA_PER_VOLT, SAMPLE_RATE
from acquisition.trial_protocol import HyperpolarizationParams, StimulusDefinition, TrialProtocol
from utils.stimulus_generator import generate_staircase_pa_array


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ms_to_samples(ms: float, sample_rate: int = SAMPLE_RATE) -> int:
    """Convert a duration in ms to an integer sample count.

    Args:
        ms: Duration in ms.  Negative values are clamped to 0.
        sample_rate: DAQ sample rate in Hz.  Defaults to
            :data:`~config.SAMPLE_RATE` (20 kHz).

    Returns:
        Number of samples corresponding to ``ms`` at ``sample_rate``,
        truncated to an integer and clamped to a minimum of 0.
    """
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
) -> NDArray[np.float64]:
    """Build the AO waveform for one current-clamp trial.

    Produces a 1-D Volts array for the ao0 channel of the NI DAQ.
    The amplifier command sensitivity is :data:`~config.AO_PA_PER_VOLT`
    (400 pA/V), so a 100 pA step is encoded as 0.25 V.

    Waveform layout::

        [0 V × pre_samples]
        [hyperpol_V × hyperpol_samples]   ← only when hyperpol is not None
        [0 V × gap_samples]               ← stim_def.gap_ms reused as post-hyperpol gap
        [staircase_V × staircase_samples]
        [0 V × post_samples]

    Args:
        stim_def: Staircase stimulus definition containing ``min_pA``,
            ``max_pA``, ``step_pA``, ``step_width_ms``, ``gap_ms``, and
            ``staircase_repeats``.
        pre_ms: Silent baseline duration before the stimulus in ms.
        post_ms: Silent tail duration after the stimulus in ms.
        hyperpol: Optional hyperpolarization pulse parameters.  When provided,
            a negative current pulse of ``hyperpol.amplitude_pA`` pA lasting
            ``hyperpol.duration_ms`` ms is prepended, followed by a gap of
            ``stim_def.gap_ms`` ms before the staircase begins.
            Pass ``None`` to omit the pulse.
        sample_rate: DAQ sample rate in Hz.  Defaults to
            :data:`~config.SAMPLE_RATE`.

    Returns:
        1-D float64 array of ao0 voltages in V.  Total length =
        ``pre_samples + hyperpol_samples + gap_samples + staircase_samples
        + post_samples``.
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
) -> NDArray[np.float64]:
    """Build the AO waveform for one voltage-clamp trial.

    The amplifier holding potential is set externally on the amplifier.
    The AO output is 0 V during pre/post windows, and
    ``stim_def.step_mV / ao_mv_per_volt`` V during the step window.

    Waveform layout::

        [0 V × pre_samples]
        [step_V × step_samples]   ← step_V = step_mV / ao_mv_per_volt
        [0 V × post_samples]

    Args:
        stim_def: Voltage-step stimulus definition containing ``step_mV``
            (step amplitude relative to holding potential, in mV) and
            ``duration_ms`` (step duration in ms).
        pre_ms: Silent baseline duration before the step in ms.
        post_ms: Silent tail duration after the step in ms.
        ao_mv_per_volt: Amplifier command sensitivity in mV/V.  Defaults to
            :data:`~config.AO_MV_PER_VOLT` (20 mV/V for Axopatch 200B).
            Override with :attr:`~acquisition.trial_protocol.TrialProtocol.ao_mv_per_volt`
            for other amplifiers.
        sample_rate: DAQ sample rate in Hz.  Defaults to
            :data:`~config.SAMPLE_RATE`.

    Returns:
        1-D float64 array of ao0 voltages in V.  Total length =
        ``pre_samples + step_samples + post_samples``.
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
) -> NDArray[np.float64]:
    """Return the ao0 waveform (Volts) for one trial of the given stimulus.

    Dispatches to :func:`build_vc_trial_waveform` or
    :func:`build_cc_trial_waveform` based on ``protocol.clamp_mode``.

    Args:
        stim_def: Stimulus definition for this trial.
        protocol: Protocol containing clamp mode, timing, and (in CC mode)
            hyperpolarization parameters.

    Returns:
        1-D float64 array of ao0 voltages in V.
    """
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

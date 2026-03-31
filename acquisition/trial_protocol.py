"""
Trial protocol data model and JSON serialisation.

No Qt or hardware dependencies — safe to import and test standalone.

A TrialProtocol describes a complete trial-based acquisition run:
  - clamp_mode:           "current_clamp" | "voltage_clamp"
  - name:                 user-defined label for the protocol
  - pre_ms/post_ms:       baseline and tail windows around each stimulus
  - iti_ms:               inter-trial interval (camera off, AO silent)
  - repeats_per_stimulus: how many times each stimulus is played
  - hyperpolarization:    CC-only, prepended access-resistance pulse
  - ao_mv_per_volt:       VC-only, amplifier command sensitivity (mV per Volt)
  - stimuli:              ordered list of StimulusDefinition entries

Trial order is randomised at run time: each stimulus appears
repeats_per_stimulus times, then the whole list is shuffled.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class HyperpolarizationParams:
    """Short negative current pulse prepended to each CC staircase trial."""
    amplitude_pA: float = -50.0
    duration_ms: float  = 100.0


@dataclass
class StimulusDefinition:
    """
    One stimulus entry in a protocol.

    type == "staircase"   →  current-clamp staircase (uses min_pA … staircase_repeats)
    type == "voltage_step" → voltage-clamp step (uses step_mV, duration_ms)
                             Holding potential is set on the amplifier; the AO
                             command is 0 V during pre/post and step_mV/ao_mv_per_volt
                             during the step window only.
    """
    type: str  = "staircase"
    name: str  = "Unnamed stimulus"

    # Staircase fields (CC)
    min_pA:            Optional[float] = 0.0
    max_pA:            Optional[float] = 400.0
    step_pA:           Optional[float] = 100.0
    step_width_ms:     Optional[float] = 500.0
    gap_ms:            Optional[float] = 100.0
    staircase_repeats: Optional[int]   = 1

    # Voltage-step fields (VC)
    step_mV:     Optional[float] = -40.0
    duration_ms: Optional[float] = 500.0


@dataclass
class TrialProtocol:
    name:                 str   = "Unnamed protocol"
    clamp_mode:           str   = "current_clamp"   # "current_clamp" | "voltage_clamp"
    pre_ms:               float = 500.0
    post_ms:              float = 1000.0
    iti_ms:               float = 2000.0
    repeats_per_stimulus: int   = 5
    ao_mv_per_volt:       float = 20.0              # VC only; ignored in CC
    hyperpolarization:    Optional[HyperpolarizationParams] = field(
        default_factory=HyperpolarizationParams
    )
    stimuli: list[StimulusDefinition] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def protocol_to_dict(p: TrialProtocol) -> dict:
    """Convert a TrialProtocol to a JSON-serialisable dict."""
    d = asdict(p)
    d["version"] = 1
    return d


def protocol_from_dict(d: dict) -> TrialProtocol:
    """Reconstruct a TrialProtocol from a dict (as loaded from JSON)."""
    d = dict(d)
    d.pop("version", None)

    hyperpol_raw = d.pop("hyperpolarization", None)
    hyperpol = (
        HyperpolarizationParams(**hyperpol_raw)
        if hyperpol_raw is not None
        else None
    )

    stimuli_raw = d.pop("stimuli", [])
    stimuli = [StimulusDefinition(**s) for s in stimuli_raw]

    return TrialProtocol(hyperpolarization=hyperpol, stimuli=stimuli, **d)


def save_protocol(p: TrialProtocol, path: str | Path) -> None:
    """Write protocol to a JSON file."""
    Path(path).write_text(json.dumps(protocol_to_dict(p), indent=2))


def load_protocol(path: str | Path) -> TrialProtocol:
    """Load a protocol from a JSON file."""
    return protocol_from_dict(json.loads(Path(path).read_text()))


# ---------------------------------------------------------------------------
# Trial ordering
# ---------------------------------------------------------------------------

def build_trial_order(p: TrialProtocol) -> list[int]:
    """
    Return a shuffled list of stimulus indices.
    Length = len(p.stimuli) × p.repeats_per_stimulus.
    Each stimulus index appears exactly repeats_per_stimulus times.
    """
    if not p.stimuli:
        return []
    order = list(range(len(p.stimuli))) * p.repeats_per_stimulus
    random.shuffle(order)
    return order


# ---------------------------------------------------------------------------
# Duration estimation
# ---------------------------------------------------------------------------

def _staircase_duration_ms(stim: StimulusDefinition) -> float:
    """Estimate staircase waveform duration in ms."""
    if stim.step_pA is None or stim.step_pA <= 0:
        return 0.0
    if stim.min_pA is None or stim.max_pA is None:
        return 0.0
    n_steps = max(1, round((stim.max_pA - stim.min_pA) / stim.step_pA) + 1)
    step_dur = (stim.step_width_ms or 0.0) + (stim.gap_ms or 0.0)
    repeats = stim.staircase_repeats or 1
    return n_steps * step_dur * repeats


def _stim_duration_ms(stim: StimulusDefinition, hyperpol: Optional[HyperpolarizationParams]) -> float:
    """Estimate total stimulus window duration in ms (including hyperpol pulse if CC)."""
    if stim.type == "staircase":
        if hyperpol is not None:
            # hyperpol pulse + gap (reused from stim gap_ms) + staircase steps
            hyperpol_dur = hyperpol.duration_ms
            gap_dur      = stim.gap_ms or 0.0
        else:
            hyperpol_dur = 0.0
            gap_dur      = 0.0
        return hyperpol_dur + gap_dur + _staircase_duration_ms(stim)
    elif stim.type == "voltage_step":
        return stim.duration_ms or 0.0
    return 0.0


def estimated_total_duration_s(p: TrialProtocol) -> float:
    """
    Estimate total run time in seconds.
    Returns 0 if the protocol has no stimuli.
    """
    if not p.stimuli:
        return 0.0
    n_trials = len(p.stimuli) * p.repeats_per_stimulus
    # Average trial duration across stimuli (they may have different lengths)
    avg_trial_ms = sum(
        p.pre_ms + _stim_duration_ms(s, p.hyperpolarization) + p.post_ms
        for s in p.stimuli
    ) / len(p.stimuli)
    total_ms = n_trials * (avg_trial_ms + p.iti_ms)
    return total_ms / 1000.0

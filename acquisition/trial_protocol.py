"""
Trial protocol data model and JSON serialisation.

No Qt or hardware dependencies — safe to import and test standalone.

Overview for lab members
------------------------
A **trial protocol** defines everything needed to run a structured
recording session:

- The **clamp mode** (current-clamp or voltage-clamp).
- A list of **stimuli** — each stimulus describes the current or voltage
  waveform to present on one trial.
- Global **timing** parameters:
    - ``pre_ms``: silent baseline recorded before each stimulus (ms).
    - ``post_ms``: silent tail recorded after each stimulus ends (ms).
    - ``iti_ms``: inter-trial interval (ITI) — the quiet period *between*
      trials during which the AO is silent and the camera TTL is off.
    - ``repeats_per_stimulus``: how many times each stimulus is played.
- An optional **hyperpolarization pulse** (current-clamp only): a brief
  sub-threshold negative current step prepended to each trial that lets
  you estimate the cell's access resistance from the resulting voltage
  deflection.

Trial order is randomised at run-time: each stimulus index appears
``repeats_per_stimulus`` times and the full list is shuffled.

Stimulus types
--------------
- **Staircase (CC)**: a sequence of current pulses of increasing amplitude.
  The amplitudes span ``min_pA`` to ``max_pA`` in ``step_pA`` increments.
  Each pulse lasts ``step_width_ms`` followed by ``gap_ms`` of silence.
  The pattern repeats ``staircase_repeats`` times within a single trial.

- **Voltage step (VC)**: a single voltage command step of ``step_mV``
  held for ``duration_ms``.  The holding potential is set on the amplifier;
  the AO command is 0 V during pre/post and ``step_mV / ao_mv_per_volt``
  V during the step.

Serialisation
-------------
Protocols are saved as JSON files (``save_protocol`` / ``load_protocol``).
The JSON format is versioned (``"version": 1``) and fully self-describing,
so saved files document their own parameters.
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
    """Parameters for the access-resistance measurement pulse in CC mode.

    A brief negative current pulse is prepended to each current-clamp trial.
    The resulting voltage deflection divided by the injected current gives an
    estimate of the cell's access resistance (Ra = ΔV / ΔI).

    The pulse occupies the first ``duration_ms`` of the stimulus window,
    followed by ``gap_ms`` of silence (taken from the stimulus definition)
    before the staircase steps begin.

    Attributes:
        amplitude_pA: Hyperpolarization current amplitude in pA.  Must be
            negative (sub-threshold).  Typical value: −50 pA.
        duration_ms: Duration of the hyperpolarization pulse in ms.
            Typical value: 100 ms.
    """

    amplitude_pA: float = -50.0
    duration_ms: float = 100.0


@dataclass
class StimulusDefinition:
    """One stimulus entry in a :class:`TrialProtocol`.

    The ``type`` field selects which set of fields is active:

    - ``"staircase"``: current-clamp staircase.  Uses ``min_pA``,
      ``max_pA``, ``step_pA``, ``step_width_ms``, ``gap_ms``,
      ``staircase_repeats``.
    - ``"voltage_step"``: voltage-clamp step.  Uses ``step_mV``,
      ``duration_ms``.  The holding potential is set on the amplifier;
      the AO command is 0 V during pre/post windows.

    Attributes:
        type: Stimulus type — ``"staircase"`` or ``"voltage_step"``.
        name: Human-readable label shown in the protocol builder and
            stored in the HDF5 file.
        min_pA: Lowest current step amplitude in pA (staircase only).
        max_pA: Highest current step amplitude in pA (staircase only).
        step_pA: Step size between amplitudes in pA (staircase only).
            Must be positive.
        step_width_ms: Duration each current step is held in ms
            (staircase only).
        gap_ms: Silent gap between current steps in ms (staircase only).
            Also used as the gap after the hyperpolarization pulse when
            :class:`HyperpolarizationParams` is present.
        staircase_repeats: Number of times the full staircase pattern
            (all steps from min to max) is repeated within one trial
            (staircase only).
        step_mV: Voltage step amplitude in mV (voltage step only).
            Relative to the holding potential set on the amplifier.
        duration_ms: Duration of the voltage step in ms (voltage step only).
    """

    type: str = "staircase"
    name: str = "Unnamed stimulus"

    # Staircase fields (CC)
    min_pA:            Optional[float] = -50.0
    max_pA:            Optional[float] = 50.0
    step_pA:           Optional[float] = 10.0
    step_width_ms:     Optional[float] = 500.0
    gap_ms:            Optional[float] = 500.0
    staircase_repeats: Optional[int]   = 1

    # Voltage-step fields (VC)
    step_mV:     Optional[float] = -40.0
    duration_ms: Optional[float] = 500.0


@dataclass
class TrialProtocol:
    """Complete specification for a trial-based acquisition run.

    A protocol is the top-level object passed to
    :class:`~acquisition.trial_mode.TrialAcquisition` to start a run.
    It is also serialised to JSON for saving and loading between sessions.

    Attributes:
        name: Human-readable protocol name stored in the HDF5 file.
        clamp_mode: Recording mode — ``"current_clamp"`` or
            ``"voltage_clamp"``.  Determines which AI channel definitions
            and AO scaling are used.
        pre_ms: Duration of the silent baseline window *before* each
            stimulus in ms.  Camera TTL is active during this window.
        post_ms: Duration of the silent tail window *after* each stimulus
            in ms.  Camera TTL remains active.
        iti_ms: Inter-trial interval in ms.  The DAQ AO is silent (0 V)
            and the camera TTL is off during the ITI.
        repeats_per_stimulus: Number of times each stimulus is presented.
            The total number of trials = ``len(stimuli) × repeats_per_stimulus``.
        ao_mv_per_volt: Amplifier command sensitivity in mV/V
            (voltage-clamp only).  Default: 20 mV/V (Axopatch 200B).
            Ignored in current-clamp mode.
        hyperpolarization: Optional access-resistance pulse prepended to
            each current-clamp trial.  Set to ``None`` to disable.
            Ignored in voltage-clamp mode.
        stimuli: Ordered list of stimuli.  The run order is randomised by
            :func:`build_trial_order`; this list defines *which* stimuli
            are included, not the playback order.
    """

    name:                 str   = "Unnamed protocol"
    clamp_mode:           str   = "current_clamp"
    pre_ms:               float = 1000.0
    post_ms:              float = 1000.0
    iti_ms:               float = 2000.0
    repeats_per_stimulus: int   = 5
    ao_mv_per_volt:       float = 20.0
    hyperpolarization:    Optional[HyperpolarizationParams] = field(
        default_factory=HyperpolarizationParams
    )
    stimuli: list[StimulusDefinition] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def protocol_to_dict(p: TrialProtocol) -> dict:
    """Serialise a :class:`TrialProtocol` to a JSON-compatible dict.

    The returned dict includes a ``"version"`` key (currently 1) so that
    future format changes can be detected and migrated.

    Args:
        p: Protocol to serialise.

    Returns:
        A dict suitable for ``json.dumps``.  All nested dataclasses
        (``HyperpolarizationParams``, ``StimulusDefinition``) are
        converted to plain dicts via :func:`dataclasses.asdict`.
    """
    d = asdict(p)
    d["version"] = 1
    return d


def protocol_from_dict(d: dict) -> TrialProtocol:
    """Reconstruct a :class:`TrialProtocol` from a plain dict.

    Handles the nested ``hyperpolarization`` and ``stimuli`` fields and
    strips the ``"version"`` key before construction.

    Args:
        d: Dict as produced by :func:`protocol_to_dict` or loaded from
            a JSON file with :func:`load_protocol`.

    Returns:
        A fully initialised :class:`TrialProtocol`.
    """
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
    """Write a protocol to a JSON file.

    Args:
        p: Protocol to save.
        path: Destination file path.  The file is overwritten if it exists.
            A ``.json`` extension is conventional but not enforced.
    """
    Path(path).write_text(json.dumps(protocol_to_dict(p), indent=2))


def load_protocol(path: str | Path) -> TrialProtocol:
    """Load a protocol from a JSON file.

    Args:
        path: Path to the JSON file produced by :func:`save_protocol`.

    Returns:
        A fully initialised :class:`TrialProtocol`.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        TypeError: If the JSON structure does not match the dataclass fields.
    """
    return protocol_from_dict(json.loads(Path(path).read_text()))


# ---------------------------------------------------------------------------
# Trial ordering
# ---------------------------------------------------------------------------

def build_trial_order(p: TrialProtocol) -> list[int]:
    """Return a randomised list of stimulus indices for one protocol run.

    Each stimulus index (0-based position in ``p.stimuli``) appears exactly
    ``p.repeats_per_stimulus`` times.  The list is shuffled in-place using
    Python's built-in random module.

    Args:
        p: Protocol whose stimuli and repeat count define the order.

    Returns:
        A list of integer stimulus indices, length =
        ``len(p.stimuli) × p.repeats_per_stimulus``.
        Returns an empty list if ``p.stimuli`` is empty.
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
    """Estimate the waveform duration of one staircase stimulus in ms.

    Accounts for step count, step width, gap, and staircase repeats.
    Returns 0 if any required field is missing or invalid.

    Args:
        stim: A staircase-type :class:`StimulusDefinition`.

    Returns:
        Estimated staircase duration in ms (excludes hyperpol pulse and
        pre/post windows).
    """
    if stim.step_pA is None or stim.step_pA <= 0:
        return 0.0
    if stim.min_pA is None or stim.max_pA is None:
        return 0.0
    n_steps = max(1, round((stim.max_pA - stim.min_pA) / stim.step_pA) + 1)
    step_dur = (stim.step_width_ms or 0.0) + (stim.gap_ms or 0.0)
    repeats = stim.staircase_repeats or 1
    return n_steps * step_dur * repeats


def _stim_duration_ms(
    stim: StimulusDefinition,
    hyperpol: Optional[HyperpolarizationParams],
) -> float:
    """Estimate the total stimulus window duration in ms for one trial.

    For staircase stimuli this includes the optional hyperpolarization
    pulse and gap before the staircase steps.  For voltage-step stimuli
    this is simply ``stim.duration_ms``.

    Args:
        stim: Stimulus whose duration is being estimated.
        hyperpol: Hyperpolarization parameters for CC mode, or ``None``
            if no pulse is used.

    Returns:
        Estimated stimulus window duration in ms.
    """
    if stim.type == "staircase":
        if hyperpol is not None:
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
    """Estimate the total wall-clock duration of a protocol run in seconds.

    Uses the average per-trial duration across all stimuli (stimuli may
    differ in length) plus the ITI, multiplied by the total trial count.

    Args:
        p: Protocol to estimate.

    Returns:
        Estimated run time in seconds.  Returns 0.0 if ``p.stimuli`` is
        empty.
    """
    if not p.stimuli:
        return 0.0
    n_trials = len(p.stimuli) * p.repeats_per_stimulus
    avg_trial_ms = sum(
        p.pre_ms + _stim_duration_ms(s, p.hyperpolarization) + p.post_ms
        for s in p.stimuli
    ) / len(p.stimuli)
    total_ms = n_trials * (avg_trial_ms + p.iti_ms)
    return total_ms / 1000.0

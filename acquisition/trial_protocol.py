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
- **Step protocol (CC)**: a sequence of current pulses of increasing amplitude.
  The amplitudes span ``min_pA`` to ``max_pA`` in ``step_pA`` increments.
  Each pulse lasts ``step_width_ms`` followed by ``gap_ms`` of silence.
  The pattern repeats ``step_protocol_repeats`` times within a single trial.

- **Voltage step (VC)**: a single voltage command step of ``step_mV``
  held for ``duration_ms``.  The holding potential is set on the amplifier;
  the AO command is 0 V during pre/post and ``step_mV / ao_mv_per_volt``
  V during the step.

- **Ramp (CC)**: a series of linear current ramps with increasing peaks, from
  ``ramp_min_peak_pA`` to ``ramp_max_peak_pA`` in ``ramp_peak_step_pA``
  increments.  Current stays at ``ramp_start_pA`` between ramps.

- **Chirp (CC)**: a sine sweep from ``chirp_start_hz`` to ``chirp_end_hz``
  over ``chirp_duration_ms``, with log or linear frequency spacing.

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

from config import AO_PA_PER_VOLT, SAMPLE_RATE
from utils.stimulus_generator import get_step_amplitudes

MAX_TRIAL_MS: float = 60_000.0
"""Longest allowed ramp or chirp trial (pre + stimulus + post) in ms."""

AO_MAX_V: float = 10.0
"""Largest AO command magnitude in Volts (the DAQ's output range)."""


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
    before the step protocol steps begin.

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

    - ``"step_protocol"``: current-clamp step protocol.  Uses ``min_pA``,
      ``max_pA``, ``step_pA``, ``step_width_ms``, ``gap_ms``,
      ``step_protocol_repeats``.
    - ``"voltage_step"``: voltage-clamp step.  Uses ``step_mV``,
      ``duration_ms``.  The holding potential is set on the amplifier;
      the AO command is 0 V during pre/post windows.
    - ``"ramp"``: current-clamp ramp series.  Uses the ``ramp_*`` fields,
      ``gap_ms`` and ``step_protocol_repeats``.
    - ``"chirp"``: current-clamp sine sweep.  Uses the ``chirp_*`` fields,
      ``gap_ms`` and ``step_protocol_repeats``.

    Attributes:
        type: Stimulus type — ``"step_protocol"``, ``"voltage_step"``,
            ``"ramp"``, ``"chirp"`` or ``"baseline"``.
        min_pA: Lowest current step amplitude in pA (step protocol only).
        max_pA: Highest current step amplitude in pA (step protocol only).
        step_pA: Step size between amplitudes in pA (step protocol only).
            Must be positive.
        step_width_ms: Duration each current step is held in ms
            (step protocol only).
        gap_ms: Silent gap between current steps in ms (step protocol only).
            Also used as the gap after the hyperpolarization pulse when
            :class:`HyperpolarizationParams` is present.
        step_protocol_repeats: Number of times the full step protocol pattern
            (all steps from min to max) is repeated within one trial
            (step protocol only).
        step_mV: Voltage step amplitude in mV (voltage step only).
            Relative to the holding potential set on the amplifier.
        duration_ms: Duration of the voltage step in ms (voltage step only).
        ramp_start_pA: Current at the start of each ramp, held between
            ramps, in pA (ramp only).
        ramp_min_peak_pA: Peak of the first ramp in pA (ramp only).
        ramp_max_peak_pA: Peak of the last ramp in pA (ramp only).
        ramp_peak_step_pA: Increase in peak between ramps in pA (ramp only).
            Must be positive.
        ramp_duration_ms: Time from start current to peak in ms (ramp only).
        ramp_hold_ms: Time held at the peak in ms (ramp only).
        ramp_return: ``"instant"`` to drop straight back to the start
            current, or ``"ramp_down"`` to ramp back over
            ``ramp_duration_ms`` (ramp only).
        chirp_start_hz: Frequency at the start of the sweep in Hz (chirp
            only).
        chirp_end_hz: Frequency at the end of the sweep in Hz (chirp only).
        chirp_duration_ms: Length of one sweep in ms (chirp only).
        chirp_spacing: ``"log"`` or ``"linear"`` (chirp only).
        chirp_amplitude_pA: Peak sine amplitude in pA (chirp only).
        chirp_offset_pA: DC offset added during the sweep in pA (chirp only).
    """

    type: str = "step_protocol"

    # Step-protocol fields (CC)
    min_pA:                Optional[float] = -50.0
    max_pA:                Optional[float] = 50.0
    step_pA:               Optional[float] = 10.0
    step_width_ms:         Optional[float] = 500.0
    gap_ms:                Optional[float] = 500.0
    step_protocol_repeats: Optional[int]   = 1

    # Voltage-step fields (VC)
    step_mV:     Optional[float] = -40.0
    duration_ms: Optional[float] = 500.0

    # Ramp fields (CC)
    ramp_start_pA:     Optional[float] = 0.0
    ramp_min_peak_pA:  Optional[float] = 50.0
    ramp_max_peak_pA:  Optional[float] = 150.0
    ramp_peak_step_pA: Optional[float] = 50.0
    ramp_duration_ms:  Optional[float] = 1000.0
    ramp_hold_ms:      Optional[float] = 0.0
    ramp_return:       Optional[str]   = "instant"

    # Chirp fields (CC)
    chirp_start_hz:     Optional[float] = 0.5
    chirp_end_hz:       Optional[float] = 100.0
    chirp_duration_ms:  Optional[float] = 20000.0
    chirp_spacing:      Optional[str]   = "log"
    chirp_amplitude_pA: Optional[float] = 20.0
    chirp_offset_pA:    Optional[float] = 0.0


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


def stimulus_label(s: StimulusDefinition) -> str:
    """Describe a stimulus from its parameters, e.g. ``"Steps -50 to 50 pA by 10 pA"``.

    Used as the display label in the protocol builder and as the
    ``stimulus_name`` stored in the HDF5 file.  Two stimuli with the same
    parameters get the same label; use ``stimulus_index`` to tell
    protocol entries apart.

    Args:
        s: Stimulus to describe.

    Returns:
        A short human-readable description.
    """
    if s.type == "step_protocol":
        label = f"Steps {s.min_pA:g} to {s.max_pA:g} pA by {s.step_pA:g} pA"
        if s.step_protocol_repeats and s.step_protocol_repeats > 1:
            label += f" x{s.step_protocol_repeats}"
        return label
    if s.type == "voltage_step":
        return f"Voltage step {s.step_mV:g} mV, {s.duration_ms:g} ms"
    if s.type == "ramp":
        label = (
            f"Ramp {s.ramp_start_pA:g} to {s.ramp_min_peak_pA:g}-"
            f"{s.ramp_max_peak_pA:g} pA by {s.ramp_peak_step_pA:g} pA, "
            f"{s.ramp_duration_ms:g} ms"
        )
        if s.ramp_hold_ms:
            label += f", hold {s.ramp_hold_ms:g} ms"
        if s.ramp_return == "ramp_down":
            label += ", ramp down"
        if s.step_protocol_repeats and s.step_protocol_repeats > 1:
            label += f" x{s.step_protocol_repeats}"
        return label
    if s.type == "chirp":
        label = (
            f"Chirp {s.chirp_start_hz:g}-{s.chirp_end_hz:g} Hz "
            f"{s.chirp_spacing}, {s.chirp_amplitude_pA:g} pA, "
            f"{s.chirp_duration_ms:g} ms"
        )
        if s.chirp_offset_pA:
            label += f", offset {s.chirp_offset_pA:g} pA"
        if s.step_protocol_repeats and s.step_protocol_repeats > 1:
            label += f" x{s.step_protocol_repeats}"
        return label
    if s.type == "baseline":
        return "Baseline"
    return s.type


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _normalize_legacy_stim_dict(s: dict) -> dict:
    """Rewrite a legacy stimulus dict to the current schema.

    Older protocols used ``"staircase"`` as the stimulus ``type`` and
    stored the repeat count under ``"staircase_repeats"``.  Both keys
    were renamed to ``"step_protocol"`` / ``"step_protocol_repeats"``.
    Older protocols also carried a user-entered ``"name"``, which was
    removed in favour of :func:`stimulus_label`; it is dropped here.
    This helper converts old → new in place on a copy and is safe to
    apply to dicts that already use the new schema (it is a no-op).

    Args:
        s: Raw stimulus dict, typically loaded from JSON or from the
            ``/metadata/protocol`` field of an HDF5 recording.

    Returns:
        A new dict with legacy keys rewritten to the current schema.
    """
    s = dict(s)
    s.pop("name", None)
    if s.get("type") == "staircase":
        s["type"] = "step_protocol"
    if "staircase_repeats" in s and "step_protocol_repeats" not in s:
        s["step_protocol_repeats"] = s.pop("staircase_repeats")
    else:
        s.pop("staircase_repeats", None)
    return s


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
    stimuli = [
        StimulusDefinition(**_normalize_legacy_stim_dict(s)) for s in stimuli_raw
    ]

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

def _step_protocol_duration_ms(stim: StimulusDefinition) -> float:
    """Estimate the waveform duration of one step-protocol stimulus in ms.

    Accounts for step count, step width, gap, and step-protocol repeats.
    Returns 0 if any required field is missing or invalid.

    Args:
        stim: A step-protocol-type :class:`StimulusDefinition`.

    Returns:
        Estimated step-protocol duration in ms (excludes hyperpol pulse
        and pre/post windows).
    """
    if stim.step_pA is None or stim.step_pA <= 0:
        return 0.0
    if stim.min_pA is None or stim.max_pA is None:
        return 0.0
    n_steps = max(1, round((stim.max_pA - stim.min_pA) / stim.step_pA) + 1)
    step_dur = (stim.step_width_ms or 0.0) + (stim.gap_ms or 0.0)
    repeats = stim.step_protocol_repeats or 1
    return n_steps * step_dur * repeats


def _ramp_duration_ms(stim: StimulusDefinition) -> float:
    """Return the waveform duration of one ramp stimulus in ms.

    Args:
        stim: A ramp-type :class:`StimulusDefinition`.

    Returns:
        Ramp series duration in ms (excludes hyperpol pulse and pre/post
        windows).
    """
    peaks = get_step_amplitudes(
        stim.ramp_min_peak_pA, stim.ramp_max_peak_pA, stim.ramp_peak_step_pA
    )
    ramp_down = stim.ramp_duration_ms if stim.ramp_return == "ramp_down" else 0.0
    one_ramp = stim.ramp_duration_ms + stim.ramp_hold_ms + ramp_down + (stim.gap_ms or 0.0)
    return len(peaks) * one_ramp * (stim.step_protocol_repeats or 1)


def _chirp_duration_ms(stim: StimulusDefinition) -> float:
    """Return the waveform duration of one chirp stimulus in ms.

    Args:
        stim: A chirp-type :class:`StimulusDefinition`.

    Returns:
        Chirp duration in ms (excludes hyperpol pulse and pre/post windows).
    """
    return (stim.chirp_duration_ms + (stim.gap_ms or 0.0)) * (stim.step_protocol_repeats or 1)


def _stim_duration_ms(
    stim: StimulusDefinition,
    hyperpol: Optional[HyperpolarizationParams],
) -> float:
    """Estimate the total stimulus window duration in ms for one trial.

    For step-protocol stimuli this includes the optional hyperpolarization
    pulse and gap before the step-protocol steps.  For voltage-step stimuli
    this is simply ``stim.duration_ms``.

    Args:
        stim: Stimulus whose duration is being estimated.
        hyperpol: Hyperpolarization parameters for CC mode, or ``None``
            if no pulse is used.

    Returns:
        Estimated stimulus window duration in ms.
    """
    if stim.type in ("step_protocol", "ramp", "chirp"):
        if hyperpol is not None:
            hyperpol_dur = hyperpol.duration_ms
            gap_dur      = stim.gap_ms or 0.0
        else:
            hyperpol_dur = 0.0
            gap_dur      = 0.0
        body = {
            "step_protocol": _step_protocol_duration_ms,
            "ramp":          _ramp_duration_ms,
            "chirp":         _chirp_duration_ms,
        }[stim.type](stim)
        return hyperpol_dur + gap_dur + body
    elif stim.type == "voltage_step":
        return stim.duration_ms or 0.0
    elif stim.type == "baseline":
        return 0.0   # no stim window; trial = pre + post only
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


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _ramp_errors(s: StimulusDefinition) -> list[str]:
    """Return the problems with a ramp's parameters."""
    errors = []
    if s.ramp_min_peak_pA > s.ramp_max_peak_pA:
        errors.append("lowest peak is above highest peak")
    if s.ramp_peak_step_pA <= 0:
        errors.append("peak step size must be positive")
    if s.ramp_duration_ms <= 0:
        errors.append("ramp duration must be positive")
    largest = max(abs(s.ramp_start_pA), abs(s.ramp_min_peak_pA), abs(s.ramp_max_peak_pA))
    if largest > AO_MAX_V * AO_PA_PER_VOLT:
        errors.append(f"current exceeds the {AO_MAX_V * AO_PA_PER_VOLT:g} pA output limit")
    return errors


def _chirp_errors(s: StimulusDefinition) -> list[str]:
    """Return the problems with a chirp's parameters."""
    errors = []
    f0, f1 = s.chirp_start_hz, s.chirp_end_hz
    if s.chirp_duration_ms <= 0:
        errors.append("sweep duration must be positive")
    if min(f0, f1) < 0:
        errors.append("frequencies cannot be negative")
    if max(f0, f1) >= SAMPLE_RATE / 2:
        errors.append(f"frequency must be below {SAMPLE_RATE / 2:g} Hz")
    if f0 == f1:
        errors.append("start and end frequency are equal")
    if s.chirp_spacing == "log" and min(f0, f1) <= 0:
        errors.append("log spacing needs frequencies above 0 Hz")
    if abs(s.chirp_offset_pA) + abs(s.chirp_amplitude_pA) > AO_MAX_V * AO_PA_PER_VOLT:
        errors.append(f"current exceeds the {AO_MAX_V * AO_PA_PER_VOLT:g} pA output limit")
    return errors


def validate_protocol(p: TrialProtocol) -> tuple[list[str], list[str]]:
    """Check a protocol's ramp and chirp stimuli before a run.

    Other stimulus types are not checked, so existing protocols keep running
    as they always have.

    Args:
        p: Protocol to check.

    Returns:
        A 2-tuple ``(errors, warnings)`` of human-readable messages.  Errors
        must block the run.  Warnings may be accepted by the user.
    """
    errors: list[str] = []
    warnings: list[str] = []
    for i, s in enumerate(p.stimuli, start=1):
        if s.type not in ("ramp", "chirp"):
            continue
        name = f"Stimulus {i} ({stimulus_label(s)})"
        problems = _ramp_errors(s) if s.type == "ramp" else _chirp_errors(s)
        if p.clamp_mode == "voltage_clamp":
            problems.append("ramps and chirps need a current-clamp protocol")
        trial_ms = p.pre_ms + _stim_duration_ms(s, p.hyperpolarization) + p.post_ms
        if trial_ms > MAX_TRIAL_MS:
            problems.append(
                f"one trial lasts {trial_ms / 1000:g} s, "
                f"above the {MAX_TRIAL_MS / 1000:g} s limit"
            )
        errors.extend(f"{name}: {problem}" for problem in problems)
        if s.type == "chirp" and max(s.chirp_start_hz, s.chirp_end_hz) > SAMPLE_RATE / 20:
            warnings.append(
                f"{name}: frequencies above {SAMPLE_RATE / 20:g} Hz show "
                "visible stepping in the analog output"
            )
    return errors, warnings

"""Ramp and chirp stimulus definitions in :mod:`acquisition.trial_protocol`.

Covers labels, duration estimates, JSON round trips and the rules the
protocol builder enforces before a run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from acquisition.trial_protocol import (
    HyperpolarizationParams,
    StimulusDefinition,
    TrialProtocol,
    _stim_duration_ms,
    protocol_from_dict,
    protocol_to_dict,
    stimulus_label,
    validate_protocol,
)


def _ramp(**kw) -> StimulusDefinition:
    return StimulusDefinition(type="ramp", **kw)


def _chirp(**kw) -> StimulusDefinition:
    return StimulusDefinition(type="chirp", **kw)


def _errors(*stimuli, clamp: str = "current_clamp") -> list[str]:
    return validate_protocol(TrialProtocol(clamp_mode=clamp, stimuli=list(stimuli)))[0]


def test_ramp_label():
    assert stimulus_label(_ramp()) == "Ramp 0 to 50-150 pA by 50 pA, 1000 ms"


def test_chirp_label():
    assert stimulus_label(_chirp()) == "Chirp 0.5-100 Hz log, 20 pA, 20000 ms"


def test_labels_add_repeats_and_offset():
    assert stimulus_label(_ramp(step_protocol_repeats=2)).endswith(" x2")
    assert "offset 5 pA" in stimulus_label(_chirp(chirp_offset_pA=5.0))


def test_ramp_label_names_hold_and_ramp_down():
    label = stimulus_label(_ramp(ramp_hold_ms=10.0, ramp_return="ramp_down"))
    assert "hold 10 ms" in label and "ramp down" in label


def test_ramp_duration_includes_hyperpol_and_gap():
    stim = _ramp(gap_ms=500.0)
    hyperpol = HyperpolarizationParams(duration_ms=100.0)
    assert _stim_duration_ms(stim, hyperpol) == 100 + 500 + 3 * (1000 + 500)


def test_ramp_down_doubles_ramp_time():
    stim = _ramp(
        gap_ms=0.0, ramp_return="ramp_down", ramp_hold_ms=10.0,
        ramp_min_peak_pA=50.0, ramp_max_peak_pA=50.0,
    )
    assert _stim_duration_ms(stim, None) == 1000 + 10 + 1000


def test_chirp_duration():
    stim = _chirp(gap_ms=500.0, step_protocol_repeats=2)
    assert _stim_duration_ms(stim, None) == 2 * (20000 + 500)


def test_json_round_trip():
    p = TrialProtocol(
        stimuli=[_ramp(ramp_start_pA=-20.0), _chirp(chirp_spacing="linear")]
    )
    assert protocol_from_dict(protocol_to_dict(p)) == p


def test_old_protocol_dict_loads_with_defaults():
    d = {"stimuli": [{"type": "staircase", "name": "x", "staircase_repeats": 2}]}
    stim = protocol_from_dict(d).stimuli[0]
    assert stim.type == "step_protocol"
    assert stim.step_protocol_repeats == 2


def test_valid_defaults_pass():
    assert validate_protocol(TrialProtocol(stimuli=[_ramp(), _chirp()])) == ([], [])


@pytest.mark.parametrize("stim", [_ramp(), _chirp()])
def test_new_types_blocked_in_voltage_clamp(stim):
    assert _errors(stim, clamp="voltage_clamp")


@pytest.mark.parametrize("stim", [
    _ramp(ramp_min_peak_pA=200.0),
    _ramp(ramp_peak_step_pA=0.0),
    _ramp(ramp_duration_ms=0.0),
    _ramp(ramp_max_peak_pA=4001.0),
    _ramp(ramp_start_pA=-4001.0),
    _chirp(chirp_end_hz=10000.0),
    _chirp(chirp_start_hz=10000.0),
    _chirp(chirp_start_hz=0.0),
    _chirp(chirp_start_hz=100.0),
    _chirp(chirp_duration_ms=0.0),
    _chirp(chirp_amplitude_pA=3000.0, chirp_offset_pA=1001.0),
])
def test_invalid_stimuli_blocked(stim):
    assert _errors(stim)


def test_error_names_the_stimulus_position():
    assert _errors(_chirp(), _ramp(ramp_min_peak_pA=200.0))[0].startswith("Stimulus 2")


def test_linear_chirp_may_start_at_zero():
    assert not _errors(_chirp(chirp_start_hz=0.0, chirp_spacing="linear"))


def test_downward_chirp_allowed():
    assert not _errors(_chirp(chirp_start_hz=100.0, chirp_end_hz=1.0))


def test_trial_over_60_s_blocked():
    p = TrialProtocol(
        pre_ms=1000.0, post_ms=1000.0,
        stimuli=[_chirp(chirp_duration_ms=59000.0)],
    )
    assert validate_protocol(p)[0]


def test_long_step_trial_is_not_blocked():
    p = TrialProtocol(stimuli=[StimulusDefinition(step_protocol_repeats=100)])
    assert validate_protocol(p) == ([], [])


def test_chirp_above_1khz_warns_only():
    errors, warnings = validate_protocol(
        TrialProtocol(stimuli=[_chirp(chirp_end_hz=1000.1)])
    )
    assert not errors
    assert warnings

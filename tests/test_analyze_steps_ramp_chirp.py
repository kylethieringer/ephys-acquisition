"""Ramp and chirp handling in :mod:`analysis.analyze_steps`.

Step detection thresholds the command channel, so without special handling
a ramp reads as one false pulse and a chirp as many.  These trials must not
appear in F-I or intrinsic properties output.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from analysis import analyze_steps as A
from utils.stimulus_generator import generate_chirp_pa_array, generate_ramp_pa_array

SR = 20_000
DISPLAY_SCALES = np.array([10.0, 2.0, 400.0, 1.0, 1.0])
CMD_CH = 2

RAMP_NAME = "Ramp 0 to 100-100 pA by 50 pA, 500 ms"
CHIRP_NAME = "Chirp 5-50 Hz log, 100 pA, 1000 ms"
STEP_NAME = "Steps 100 to 100 pA by 10 pA"


def _ramp_pa() -> np.ndarray:
    return generate_ramp_pa_array(0, 100, 100, 50, 500, 0, False, 100)


def _chirp_pa() -> np.ndarray:
    return generate_chirp_pa_array(5, 50, 1000, "log", 100, 0, 100)


def _step_pa() -> np.ndarray:
    return np.concatenate([np.full(10_000, 100.0), np.zeros(2_000)])


def _recording(*segments: tuple[str, np.ndarray]) -> tuple[np.ndarray, dict]:
    """Build a recording with one apply/clear window per ``(name, pA)`` segment."""
    silence = np.zeros(SR)
    pieces = [silence]
    sample_index, event_type, stimulus_name, stimulus_index = [], [], [], []
    cursor = SR
    for i, (name, pa) in enumerate(segments):
        pieces += [pa, silence]
        sample_index += [cursor, cursor + pa.size]
        event_type += ["apply", "clear"]
        stimulus_name += [name, name]
        stimulus_index += [i, i]
        cursor += pa.size + SR
    cmd_pa = np.concatenate(pieces)
    data = np.zeros((5, cmd_pa.size))
    data[CMD_CH] = cmd_pa / DISPLAY_SCALES[CMD_CH]
    events = {
        "sample_index": np.array(sample_index),
        "event_type": event_type,
        "stimulus_name": stimulus_name,
        "stimulus_index": np.array(stimulus_index),
    }
    return data, events


def _run(*stim_types: str) -> dict:
    """A protocol run as ``load_protocol_metadata`` returns it."""
    return {
        "start_sample": 0,
        "protocol_dict": {
            "stimuli": [{"type": t} for t in stim_types],
            "hyperpolarization": None,
        },
    }


def _find(data: np.ndarray, events: dict, runs: list[dict]) -> tuple[list, list]:
    return A.find_step_protocols_from_events(data, events, runs, SR, DISPLAY_SCALES)


def test_ramp_window_is_identified_by_protocol_type():
    assert A._is_ramp_or_chirp_window("unlabelled", 0, 100, [_run("ramp")])


def test_protocol_type_outranks_the_label():
    assert not A._is_ramp_or_chirp_window(RAMP_NAME, 0, 100, [_run("step_protocol")])


def test_window_is_identified_by_label_without_sidecar():
    assert A._is_ramp_or_chirp_window(CHIRP_NAME, 0, 100, [])
    assert A._is_ramp_or_chirp_window(RAMP_NAME, 0, 100, [])


def test_step_window_is_not_identified():
    assert not A._is_ramp_or_chirp_window(STEP_NAME, 0, 100, [_run("step_protocol")])
    assert not A._is_ramp_or_chirp_window(STEP_NAME, 0, 100, [])


def test_chirp_window_produces_no_pulses():
    data, events = _recording((CHIRP_NAME, _chirp_pa()))
    assert _find(data, events, []) == ([], [])


def test_ramp_window_produces_no_pulses():
    data, events = _recording((RAMP_NAME, _ramp_pa()))
    assert _find(data, events, [_run("ramp")]) == ([], [])


def test_mixed_recording_keeps_step_windows():
    data, events = _recording((STEP_NAME, _step_pa()), (RAMP_NAME, _ramp_pa()))
    steps, meta = _find(data, events, [])
    assert len(steps) == 1
    assert meta[0]["stimulus_name"] == STEP_NAME


def test_ramp_only_recording_reports_no_step_protocols(monkeypatch, tmp_path):
    data, events = _recording((RAMP_NAME, _ramp_pa()))
    monkeypatch.setattr(A, "load_continuous_h5", lambda path: {
        "data": data,
        "sample_rate": SR,
        "display_scales": DISPLAY_SCALES,
        "stimulus_events": events,
    })
    # A regression would otherwise write a CSV into the real results folder.
    monkeypatch.setattr(A, "write_csv", lambda *args: None)
    assert A.process_file(tmp_path / "ramps.h5", compute_step_rates=False) is None


def test_recording_without_events_keeps_waveform_detection(monkeypatch, tmp_path):
    data, _ = _recording((STEP_NAME, _step_pa()))
    calls = []

    def fake_waveform_detection(*args):
        calls.append(args)
        return [], []

    monkeypatch.setattr(A, "find_step_protocols_from_waveform", fake_waveform_detection)
    monkeypatch.setattr(A, "load_continuous_h5", lambda path: {
        "data": data,
        "sample_rate": SR,
        "display_scales": DISPLAY_SCALES,
        "stimulus_events": None,
    })
    A.process_file(tmp_path / "old.h5", compute_step_rates=False)
    assert len(calls) == 1


def _long_ramp_pa() -> np.ndarray:
    """Ramps held long enough to fill most of the recording's samples."""
    return generate_ramp_pa_array(-30, 100, 100, 50, 5000, 0, False, 10000)


def _offset_chirp_pa() -> np.ndarray:
    return generate_chirp_pa_array(5, 50, 8000, "log", 20, 30, 100)


def _stub_loader(monkeypatch, data: np.ndarray, events: dict) -> None:
    monkeypatch.setattr(A, "load_continuous_h5", lambda path: {
        "data": data,
        "sample_rate": SR,
        "display_scales": DISPLAY_SCALES,
        "stimulus_events": events,
    })
    monkeypatch.setattr(A, "write_csv", lambda *args: None)


def test_long_ramps_leave_silence_at_zero(monkeypatch, tmp_path):
    data, events = _recording((RAMP_NAME, _long_ramp_pa()))
    _stub_loader(monkeypatch, data, events)
    assert A.process_file(tmp_path / "ramps.h5", compute_step_rates=False) is None


def test_offset_chirp_leaves_silence_at_zero(monkeypatch, tmp_path):
    data, events = _recording((CHIRP_NAME, _offset_chirp_pa()))
    _stub_loader(monkeypatch, data, events)
    assert A.process_file(tmp_path / "chirps.h5", compute_step_rates=False) is None


def test_long_ramps_do_not_shift_real_step_amplitudes():
    data, events = _recording((STEP_NAME, _step_pa()), (RAMP_NAME, _long_ramp_pa()))
    steps, meta = _find(data, events, [])
    assert len(steps) == 1
    assert meta[0]["stimulus_name"] == STEP_NAME
    assert steps[0][0]["amplitude_pA"] == pytest.approx(100, abs=1)


def test_mixed_recording_keeps_step_windows_with_sidecar():
    data, events = _recording((STEP_NAME, _step_pa()), ("x", _long_ramp_pa()))
    steps, meta = _find(data, events, [_run("step_protocol", "ramp")])
    assert len(steps) == 1
    assert meta[0]["stimulus_name"] == STEP_NAME

"""Protocol checks on the Acquire page's Run Protocol path.

This is the documented way to run a protocol, and it never passes through the
protocol builder.  Runs the real window with the acquisition calls recorded.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PySide6.QtWidgets import QMessageBox

from acquisition.trial_protocol import StimulusDefinition, TrialProtocol, protocol_to_dict
from ui import main_window as MW
from ui import protocol_builder as PB


@pytest.fixture
def window(monkeypatch):
    w = MW.MainWindow()
    w.calls = []
    monkeypatch.setattr(w, "_check_metadata", lambda: True)
    monkeypatch.setattr(w, "_apply_channel_defs", lambda mode: None)
    for acq in (w._acq, w._trial_acq):
        monkeypatch.setattr(acq, "start", lambda: None)
    monkeypatch.setattr(w._trial_acq, "run_protocol",
                        lambda *a: w.calls.append("run_protocol"))
    monkeypatch.setattr(w._acq, "start_recording",
                        lambda *a: w.calls.append("start_recording"))
    monkeypatch.setattr(w._acq, "start_protocol",
                        lambda *a: w.calls.append("start_protocol"))
    w.dialogs = []
    for module in (PB, MW):
        monkeypatch.setattr(module.QMessageBox, "warning",
                            lambda parent, title, text: w.dialogs.append(text))
        monkeypatch.setattr(module.QMessageBox, "critical",
                            lambda parent, title, text: w.dialogs.append(text))
    return w


def _start(window, mode: str, *stimuli: StimulusDefinition) -> None:
    window._active_mode = mode
    window._pending_protocol = protocol_to_dict(TrialProtocol(stimuli=list(stimuli)))
    window._on_start_protocol()


@pytest.mark.parametrize("mode", ["trial", "continuous"])
def test_invalid_protocol_never_starts(window, mode):
    _start(window, mode, StimulusDefinition(type="chirp", chirp_start_hz=0.0))
    assert window.calls == []
    assert len(window.dialogs) == 1


@pytest.mark.parametrize("mode", ["trial", "continuous"])
def test_stimulus_with_no_waveform_never_starts_recording(window, mode):
    _start(window, mode, StimulusDefinition(type="chirp", chirp_spacing="cubic"))
    assert window.calls == []
    assert len(window.dialogs) == 1


def test_valid_trial_protocol_runs(window):
    _start(window, "trial", StimulusDefinition(type="ramp"))
    assert window.calls == ["run_protocol"]
    assert window.dialogs == []


def test_valid_continuous_protocol_records_then_runs(window):
    _start(window, "continuous", StimulusDefinition(type="ramp"))
    assert window.calls == ["start_recording", "start_protocol"]


def test_declined_warning_does_not_start(window, monkeypatch):
    monkeypatch.setattr(PB.QMessageBox, "question", lambda *a, **kw: QMessageBox.No)
    _start(window, "trial", StimulusDefinition(type="chirp", chirp_end_hz=2000.0))
    assert window.calls == []

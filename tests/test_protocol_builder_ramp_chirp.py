"""Ramp and chirp pages of :class:`ui.protocol_builder.ProtocolBuilderPanel`.

Runs the real widgets offscreen, with only the blocking message boxes stubbed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PySide6.QtWidgets import QMessageBox

from acquisition.trial_protocol import save_protocol
from ui import protocol_builder as PB


@pytest.fixture
def panel():
    return PB.ProtocolBuilderPanel()


def _add_ramp(panel) -> None:
    panel._on_add()
    panel._rp_type_rb.setChecked(True)


def _add_chirp(panel) -> None:
    panel._on_add()
    panel._ch_type_rb.setChecked(True)


def _capture_runs(panel) -> list[dict]:
    runs: list[dict] = []
    panel.protocol_run_requested.connect(runs.append)
    return runs


def test_ramp_edits_reach_the_stimulus(panel):
    _add_ramp(panel)
    panel._rp_start.setValue(-20.0)
    panel._rp_return.setCurrentIndex(panel._rp_return.findData("ramp_down"))
    stim = panel.get_protocol().stimuli[0]
    assert stim.type == "ramp"
    assert stim.ramp_start_pA == -20.0
    assert stim.ramp_return == "ramp_down"


def test_chirp_edits_reach_the_stimulus(panel):
    _add_chirp(panel)
    panel._ch_end.setValue(50.0)
    panel._ch_spacing.setCurrentIndex(panel._ch_spacing.findData("linear"))
    stim = panel.get_protocol().stimuli[0]
    assert stim.type == "chirp"
    assert stim.chirp_end_hz == 50.0
    assert stim.chirp_spacing == "linear"


def test_gap_and_repeats_reach_the_shared_fields(panel):
    _add_chirp(panel)
    panel._ch_gap.setValue(250.0)
    panel._ch_reps.setValue(3)
    stim = panel.get_protocol().stimuli[0]
    assert stim.gap_ms == 250.0
    assert stim.step_protocol_repeats == 3


def test_list_label_describes_the_new_type(panel):
    _add_chirp(panel)
    assert "Chirp" in panel._stim_list.item(0).text()


def test_switching_rows_keeps_each_stimulus_values(panel):
    _add_ramp(panel)
    panel._rp_max.setValue(300.0)
    _add_chirp(panel)
    panel._ch_end.setValue(50.0)
    panel._stim_list.setCurrentRow(0)
    panel._stim_list.setCurrentRow(1)
    panel._stim_list.setCurrentRow(0)
    ramp, chirp = panel.get_protocol().stimuli
    assert ramp.type == "ramp"
    assert ramp.ramp_max_peak_pA == 300.0
    assert chirp.type == "chirp"
    assert chirp.chirp_end_hz == 50.0


def test_selecting_a_stimulus_shows_its_page(panel):
    _add_ramp(panel)
    _add_chirp(panel)
    panel._stim_list.setCurrentRow(0)
    assert panel._stim_stack.currentIndex() == 3
    panel._stim_list.setCurrentRow(1)
    assert panel._stim_stack.currentIndex() == 4


def test_save_and_load_restores_widgets(panel, tmp_path):
    _add_chirp(panel)
    panel._ch_spacing.setCurrentIndex(panel._ch_spacing.findData("linear"))
    panel._ch_amp.setValue(35.0)
    path = tmp_path / "chirp.json"
    save_protocol(panel.get_protocol(), path)

    loaded = PB.ProtocolBuilderPanel()
    loaded.load_protocol_from_file(path)
    assert loaded._ch_type_rb.isChecked()
    assert loaded._ch_spacing.currentData() == "linear"
    assert loaded._ch_amp.value() == 35.0


def test_defaults_fit_spin_ranges(panel):
    _add_chirp(panel)
    assert panel._ch_dur.value() == 20000.0
    assert panel._ch_start.value() == 0.5


def test_estimated_time_includes_new_types(panel):
    _add_chirp(panel)
    # 5 x (0.5 s pre + 0.1 s hyperpol + 0.5 s gap + 20.5 s chirp + 1 s post + 2 s ITI)
    assert "2 min 03 sec" in panel._time_lbl.text()


def test_run_blocks_invalid_protocol(panel, monkeypatch):
    warned: list[str] = []
    monkeypatch.setattr(PB.QMessageBox, "warning",
                        lambda parent, title, text: warned.append(text))
    runs = _capture_runs(panel)
    _add_ramp(panel)
    panel._rp_min.setValue(200.0)
    panel._on_run()
    assert runs == []
    assert len(warned) == 1
    assert "lowest peak" in warned[0]


def test_run_blocks_ramp_in_voltage_clamp(panel, monkeypatch):
    warned: list[str] = []
    monkeypatch.setattr(PB.QMessageBox, "warning",
                        lambda parent, title, text: warned.append(text))
    runs = _capture_runs(panel)
    _add_ramp(panel)
    panel._vc_rb.setChecked(True)
    panel._on_run()
    assert runs == []
    assert len(warned) == 1


@pytest.mark.parametrize("answer, expected_runs", [
    (QMessageBox.No, 0),
    (QMessageBox.Yes, 1),
])
def test_run_warning_can_be_cancelled_or_accepted(panel, monkeypatch, answer, expected_runs):
    monkeypatch.setattr(PB.QMessageBox, "question", lambda *args, **kw: answer)
    runs = _capture_runs(panel)
    _add_chirp(panel)
    panel._ch_end.setValue(2000.0)
    panel._on_run()
    assert len(runs) == expected_runs


def test_valid_run_is_not_interrupted(panel, monkeypatch):
    def fail(*args, **kw):
        raise AssertionError("no dialog expected")

    monkeypatch.setattr(PB.QMessageBox, "warning", fail)
    monkeypatch.setattr(PB.QMessageBox, "question", fail)
    runs = _capture_runs(panel)
    _add_ramp(panel)
    panel._on_run()
    assert len(runs) == 1

# Ramp and Chirp Stimuli Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Ramp (CC) and Chirp (CC) stimulus types to the protocol builder, with waveforms, saved parameters, and step analysis that ignores them.

**Architecture:** New optional fields on `StimulusDefinition` drive two pure numpy generators in `utils/stimulus_generator.py`, selected by type in `build_cc_trial_waveform`. A pure `validate_protocol` function holds the Run rules, and the builder shows its results. Trial mode, continuous-protocol mode, saving and QC already go through `build_trial_waveform`, so they need no changes.

**Tech Stack:** Python 3.14, numpy, scipy (tests), PySide6, pytest.

**Spec:** `docs/superpowers/specs/2026-10-30-ramp-chirp-stimuli-design.md`

## Global Constraints

- Run Python with `.venv/Scripts/python.exe`. Bare `python` hits the Microsoft Store stub.
- Commit messages have no `Co-Authored-By` trailer. This repo's rule overrides any default attribution.
- Many files hold uncommitted user work, including `acquisition/trial_protocol.py`, `ui/protocol_builder.py`, `analysis/analyze_steps.py` and `analysis/qc/stimulus.py`. Never revert them. Do not start Task 1 until the requester has said how to handle them, because `git add` on those files sweeps their work into a task commit.
- No new QC, and no change to `hardware/` in committed code, unless the Task 5 rig measurement requires it.
- New fields test `is None`, never `value or default`, because 0 is a valid value.
- Existing protocols and recordings load and analyze exactly as they do today.
- `SAMPLE_RATE` is 20000 Hz, `AO_PA_PER_VOLT` is 400, and AO range is ±10 V, so the current limit is ±4000 pA.

## Review Focus

- Selecting another stimulus in the list while editing must keep each stimulus's own ramp and chirp values. Pinned in Task 4 (`test_switching_rows_keeps_each_stimulus_values`).
- A mixed recording must keep its step windows and drop only ramp and chirp windows. Pinned in Task 3 (`test_mixed_recording_keeps_step_windows`).
- A long existing step protocol must still run. The 60 s limit applies only to trials containing a ramp or chirp. Pinned in Task 1 (`test_long_step_trial_is_not_blocked`).
- A ramp loaded from JSON into a voltage-clamp protocol must fail, not play a voltage step. Pinned in Task 2 (`test_ramp_in_voltage_clamp_raises`).
- Peak step sizes that don't divide the range evenly must not overshoot the highest peak. Pinned in Task 2 (`test_ramp_peaks_do_not_overshoot`).

---

### Task 1: Data model, labels, durations and validation

**Files:**
- Modify: `acquisition/trial_protocol.py`
- Test: `tests/test_ramp_chirp_protocol.py` (create)

**Interfaces:**
- Produces, on `StimulusDefinition`, all `Optional` with these defaults: `ramp_start_pA=0.0`, `ramp_min_peak_pA=50.0`, `ramp_max_peak_pA=150.0`, `ramp_peak_step_pA=50.0`, `ramp_duration_ms=1000.0`, `ramp_hold_ms=0.0`, `ramp_return="instant"` (or `"ramp_down"`), `chirp_start_hz=0.5`, `chirp_end_hz=100.0`, `chirp_duration_ms=20000.0`, `chirp_spacing="log"` (or `"linear"`), `chirp_amplitude_pA=20.0`, `chirp_offset_pA=0.0`.
- Ramps and chirps reuse `gap_ms` and `step_protocol_repeats`.
- Produces: `validate_protocol(p: TrialProtocol) -> tuple[list[str], list[str]]`, returning `(errors, warnings)`.
- Produces: `_ramp_duration_ms(stim)` and `_chirp_duration_ms(stim)` in `trial_protocol.py`, covered through `_stim_duration_ms`.

- [ ] **Step 1: Write the failing tests**

Use the `sys.path` header from `tests/test_analyze_steps_baseline.py`.

```python
def _ramp(**kw): return StimulusDefinition(type="ramp", **kw)
def _chirp(**kw): return StimulusDefinition(type="chirp", **kw)

def test_ramp_label():
    assert stimulus_label(_ramp()) == "Ramp 0 to 50-150 pA by 50 pA, 1000 ms"

def test_chirp_label():
    assert stimulus_label(_chirp()) == "Chirp 0.5-100 Hz log, 20 pA, 20000 ms"

def test_labels_add_repeats_and_offset():
    assert stimulus_label(_ramp(step_protocol_repeats=2)).endswith(" x2")
    assert "offset 5 pA" in stimulus_label(_chirp(chirp_offset_pA=5.0))

def test_ramp_duration_includes_hyperpol_and_gap():
    s = _ramp(gap_ms=500.0)
    hp = HyperpolarizationParams(duration_ms=100.0)
    assert _stim_duration_ms(s, hp) == 100 + 500 + 3 * (1000 + 500)

def test_ramp_down_doubles_ramp_time():
    s = _ramp(gap_ms=0.0, ramp_return="ramp_down", ramp_hold_ms=10.0,
              ramp_min_peak_pA=50.0, ramp_max_peak_pA=50.0)
    assert _stim_duration_ms(s, None) == 1000 + 10 + 1000

def test_chirp_duration():
    s = _chirp(gap_ms=500.0, step_protocol_repeats=2)
    assert _stim_duration_ms(s, None) == 2 * (20000 + 500)

def test_json_round_trip():
    p = TrialProtocol(stimuli=[_ramp(ramp_start_pA=-20.0), _chirp(chirp_spacing="linear")])
    assert protocol_from_dict(protocol_to_dict(p)) == p

def test_old_protocol_dict_loads_with_defaults():
    d = {"stimuli": [{"type": "staircase", "name": "x", "staircase_repeats": 2}]}
    s = protocol_from_dict(d).stimuli[0]
    assert s.type == "step_protocol" and s.step_protocol_repeats == 2

def _errors(*stimuli, clamp="current_clamp"):
    return validate_protocol(TrialProtocol(clamp_mode=clamp, stimuli=list(stimuli)))[0]

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

def test_linear_chirp_may_start_at_zero():
    assert not _errors(_chirp(chirp_start_hz=0.0, chirp_spacing="linear"))

def test_downward_chirp_allowed():
    assert not _errors(_chirp(chirp_start_hz=100.0, chirp_end_hz=1.0))

def test_trial_over_60_s_blocked():
    p = TrialProtocol(pre_ms=1000.0, post_ms=1000.0, stimuli=[_chirp(chirp_duration_ms=59000.0)])
    assert validate_protocol(p)[0]

def test_long_step_trial_is_not_blocked():
    p = TrialProtocol(stimuli=[StimulusDefinition(step_protocol_repeats=100)])
    assert validate_protocol(p) == ([], [])

def test_chirp_above_1khz_warns_only():
    errors, warnings = validate_protocol(TrialProtocol(stimuli=[_chirp(chirp_end_hz=1000.1)]))
    assert not errors and warnings
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ramp_chirp_protocol.py -v`
Expected: FAIL at import, because `validate_protocol` does not exist.

- [ ] **Step 3: Add the fields and extend `stimulus_label` and `_stim_duration_ms`**

Update the module and `StimulusDefinition` docstrings to describe both types. A ramp's duration is `repeats × n_peaks × (ramp + hold + down + gap)`, where `down` is the ramp duration for `"ramp_down"`, else 0. Get `n_peaks` from `get_step_amplitudes` in `utils.stimulus_generator`. Include the hyperpolarizing pulse and `gap_ms` as the step branch does. Append `, hold {h:g} ms` when hold is above 0, `, ramp down` for `"ramp_down"`, `, offset {o:g} pA` when offset is non-zero, and ` x{n}` when repeats exceed 1.

- [ ] **Step 4: Implement `validate_protocol`**

Module constants: `MAX_TRIAL_MS = 60_000.0`, `AO_MAX_V = 10.0`. Import `SAMPLE_RATE` and `AO_PA_PER_VOLT` from `config`. Only ramp and chirp stimuli are checked. Each message names the stimulus through its 1-based position and `stimulus_label`. Rules:

- Any: voltage-clamp protocol is an error. `pre_ms + _stim_duration_ms(s, hyperpol) + post_ms > MAX_TRIAL_MS` is an error.
- Ramp: error if min peak exceeds max peak, peak step ≤ 0, duration ≤ 0, or the largest of `|start|`, `|min peak|`, `|max peak|` exceeds `AO_MAX_V * AO_PA_PER_VOLT`.
- Chirp: error if duration ≤ 0, a frequency is negative, `max(f0, f1) >= SAMPLE_RATE / 2`, `f0 == f1`, log spacing has a frequency ≤ 0, or `|offset| + |amplitude|` exceeds the AO limit. Warn if `max(f0, f1) > SAMPLE_RATE / 20`.

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ramp_chirp_protocol.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add acquisition/trial_protocol.py tests/test_ramp_chirp_protocol.py
git commit -m "feat: add ramp and chirp stimulus definitions"
```

---

### Task 2: Waveform generation

**Files:**
- Modify: `utils/stimulus_generator.py`, `acquisition/trial_waveforms.py`
- Test: `tests/test_ramp_chirp_waveforms.py` (create)

**Interfaces:**
- Consumes: Task 1 fields.
- Produces: `generate_ramp_pa_array(start_pa, min_peak_pa, max_peak_pa, peak_step_pa, ramp_ms, hold_ms, ramp_down: bool, gap_ms, repeats=1) -> NDArray[np.float64]`
- Produces: `generate_chirp_pa_array(start_hz, end_hz, duration_ms, spacing: str, amplitude_pa, offset_pa, gap_ms, repeats=1) -> NDArray[np.float64]`

- [ ] **Step 1: Write the failing tests**

All at 20 kHz.

```python
def test_ramp_series_shape_and_values():
    a = generate_ramp_pa_array(0, 50, 150, 50, 100, 0, False, 50)
    assert a.size == 3 * (2000 + 1000)
    assert a[1999] == 50 and a[2000] == 0
    assert a[3000 + 1999] == 100 and a[8999] == 0

def test_ramp_gap_holds_start_current():
    a = generate_ramp_pa_array(-20, 50, 50, 50, 100, 0, False, 50)
    assert a[0] == -20 and a[2000] == -20 and a[-1] == -20

def test_ramp_hold_and_ramp_down():
    a = generate_ramp_pa_array(-20, 50, 50, 50, 100, 10, True, 0)
    assert a.size == 2000 + 200 + 2000
    assert a[2100] == 50 and a[-1] == -20

def test_ramp_peaks_do_not_overshoot():
    a = generate_ramp_pa_array(0, 10, 25, 10, 10, 0, False, 0)
    assert a.max() == 20

def test_ramp_repeats_tile():
    one = generate_ramp_pa_array(0, 50, 100, 50, 10, 0, False, 5)
    assert np.array_equal(generate_ramp_pa_array(0, 50, 100, 50, 10, 0, False, 5, repeats=2),
                          np.tile(one, 2))

def _inst_hz(a):
    return np.diff(np.unwrap(np.angle(hilbert(a)))) * 20000 / (2 * np.pi)

def test_linear_chirp_frequency():
    f = _inst_hz(generate_chirp_pa_array(10, 100, 1000, "linear", 20, 0, 0))
    assert f[2000] == pytest.approx(19, rel=0.03)
    assert f[18000] == pytest.approx(91, rel=0.03)

def test_log_chirp_frequency():
    f = _inst_hz(generate_chirp_pa_array(1, 100, 2000, "log", 20, 0, 0))
    assert f[20000] == pytest.approx(10, rel=0.05)

def test_chirp_offset_amplitude_gap_and_start_phase():
    a = generate_chirp_pa_array(1, 10, 1000, "linear", 20, 5, 100, repeats=2)
    assert a.size == 2 * (20000 + 2000)
    assert a[0] == pytest.approx(5)
    assert a[:20000].max() <= 25 + 1e-9 and a[:20000].min() >= -15 - 1e-9
    assert np.all(a[20000:22000] == 0)

def test_chirp_is_deterministic():
    args = (0.5, 100, 500, "log", 20, 0, 0)
    assert np.array_equal(generate_chirp_pa_array(*args), generate_chirp_pa_array(*args))

def test_ramp_trial_holds_start_after_hyperpol_and_ends_at_zero():
    s = StimulusDefinition(type="ramp", ramp_start_pA=-20.0, ramp_min_peak_pA=50.0,
                           ramp_max_peak_pA=50.0, ramp_duration_ms=100.0, gap_ms=50.0)
    a = build_cc_trial_waveform(s, 10, 10, HyperpolarizationParams(-50.0, 100.0))
    assert a[199] == 0                       # end of pre
    assert a[2200] == pytest.approx(-0.05)   # gap after hyperpol, -20 pA at 400 pA/V
    assert a[-201] == pytest.approx(-0.05)   # last gap
    assert a[-1] == 0                        # post

def test_chirp_trial_gap_is_zero():
    s = StimulusDefinition(type="chirp", chirp_duration_ms=100.0, gap_ms=50.0)
    a = build_cc_trial_waveform(s, 0, 0, HyperpolarizationParams(-50.0, 100.0))
    assert np.all(a[2000:3000] == 0)

def test_ramp_in_voltage_clamp_raises():
    p = TrialProtocol(clamp_mode="voltage_clamp", hyperpolarization=None)
    with pytest.raises(ValueError):
        build_trial_waveform(StimulusDefinition(type="ramp"), p)

def test_unknown_type_raises():
    with pytest.raises(ValueError):
        build_cc_trial_waveform(StimulusDefinition(type="noise"), 0, 0, None)

def test_voltage_step_in_cc_still_plays_steps():
    s = StimulusDefinition(type="voltage_step")
    legacy = StimulusDefinition(type="step_protocol")
    assert np.array_equal(build_cc_trial_waveform(s, 0, 0, None),
                          build_cc_trial_waveform(legacy, 0, 0, None))
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ramp_chirp_waveforms.py -v`
Expected: FAIL at import.

- [ ] **Step 3: Implement both generators in `utils/stimulus_generator.py`**

Use `get_step_amplitudes` for peaks. Each ramp is `np.linspace(start, peak, ramp_samples)`, then hold at peak, then `np.linspace(peak, start, ramp_samples)` when `ramp_down`, then `gap_samples` at `start_pa`, all tiled `repeats` times. Sample counts use `max(1, int(ms / 1000 * SAMPLE_RATE))` for ramps and `max(0, ...)` for hold and gap, as the step generator does. Chirp uses `t = np.arange(n) / SAMPLE_RATE`, `T = n / SAMPLE_RATE`, and the phase formulas in the spec, followed by `gap_samples` of 0 pA. Raise `ValueError` for any `spacing` other than `"log"` or `"linear"`. Update the module docstring's function list.

- [ ] **Step 4: Add `_cc_stimulus_pa(stim_def) -> NDArray[np.float64]` in `trial_waveforms.py` and use it in `build_cc_trial_waveform`**

It returns the ramp, chirp or step array. `step_protocol` and `voltage_step` both use the existing step call unchanged. Any other type raises `ValueError` naming the type. New fields test `is None` before falling back to dataclass defaults. In the hyperpolarizing section, fill `gap_v` with `ramp_start_pA / AO_PA_PER_VOLT` for a ramp and zeros otherwise. Update the module and function docstrings' layout diagrams.

- [ ] **Step 5: Reject new types in voltage clamp in `build_trial_waveform`**

Raise `ValueError` before the clamp-mode dispatch when `stim_def.type in ("ramp", "chirp")` and the protocol is voltage clamp.

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ramp_chirp_waveforms.py tests/test_ramp_chirp_protocol.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add utils/stimulus_generator.py acquisition/trial_waveforms.py tests/test_ramp_chirp_waveforms.py
git commit -m "feat: generate ramp and chirp trial waveforms"
```

---

### Task 3: Keep ramps and chirps out of step analysis

**Files:**
- Modify: `analysis/analyze_steps.py` (`find_step_protocols_from_events` near line 540, and the fallback near line 1786)
- Test: `tests/test_analyze_steps_ramp_chirp.py` (create)

**Interfaces:**
- Consumes: Task 1 labels (`Ramp ` and `Chirp ` prefixes) and stimulus types; Task 2 generators for synthetic data.
- Produces: `_is_ramp_or_chirp_window(stimulus_name: str, stimulus_index: int, sample: int, protocol_runs: list[dict]) -> bool` and `_has_ramp_or_chirp_windows(stimulus_events: dict, protocol_runs: list[dict]) -> bool`. `find_step_protocols_from_events` keeps its return shape.

- [ ] **Step 1: Write the failing tests**

Build a synthetic 5-channel recording as in `tests/test_analyze_steps_baseline.py`, with `DISPLAY_SCALES` and the AmpCmd channel that `smooth_cmd` reads. Helper `_events(windows)` returns the `sample_index`, `event_type`, `stimulus_name` and `stimulus_index` arrays for `(start, end, name, index)` tuples.

```python
def test_ramp_window_is_skipped_by_protocol_type():
    # sidecar protocol says index 0 is a ramp; the name gives no hint
    assert A._is_ramp_or_chirp_window("x", 0, 100, [_run("ramp")])

def test_window_is_skipped_by_label_without_sidecar():
    assert A._is_ramp_or_chirp_window("Chirp 0.5-100 Hz log, 20 pA, 20000 ms", 0, 100, [])
    assert A._is_ramp_or_chirp_window("Ramp 0 to 50-150 pA by 50 pA, 1000 ms", 0, 100, [])

def test_step_window_is_not_skipped():
    assert not A._is_ramp_or_chirp_window("Steps -50 to 50 pA by 10 pA", 0, 100, [_run("step_protocol")])

def test_chirp_window_produces_no_pulses():
    data, events = _recording_with([("chirp", ...)])
    steps, meta = A.find_step_protocols_from_events(data, events, [], SR, DISPLAY_SCALES)
    assert steps == [] and meta == []

def test_mixed_recording_keeps_step_windows():
    data, events = _recording_with([("step", ...), ("ramp", ...)])
    steps, meta = A.find_step_protocols_from_events(data, events, [], SR, DISPLAY_SCALES)
    assert len(steps) == 1 and meta[0]["stimulus_name"].startswith("Steps")

def test_ramp_only_recording_does_not_use_waveform_fallback(monkeypatch, tmp_path):
    # Write a ramp-only recording, patch find_step_protocols_from_waveform to fail
    # the test if called, and assert the analysis entry point returns None.
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_analyze_steps_ramp_chirp.py -v`
Expected: FAIL with `AttributeError` on the new helpers.

- [ ] **Step 3: Implement the two helpers and skip those windows**

`_is_ramp_or_chirp_window` resolves the run with `_find_protocol_run` and reads `protocol_dict["stimuli"][index]["type"]` when available. Otherwise it checks `stimulus_name.startswith(("Ramp ", "Chirp "))`. In `find_step_protocols_from_events`, `continue` before `find_pulses`. Do not change the `covered` calculation, so these windows stay out of the manual-stimulus scan.

- [ ] **Step 4: Guard the fallback**

When events exist, find no steps, and `_has_ramp_or_chirp_windows` is true, skip `find_step_protocols_from_waveform` and reach the existing "No step_protocols detected" path, which returns `None`.

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_analyze_steps_ramp_chirp.py tests/test_analyze_steps_baseline.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add analysis/analyze_steps.py tests/test_analyze_steps_ramp_chirp.py
git commit -m "fix: exclude ramp and chirp windows from step analysis"
```

---

### Task 4: Builder pages and Run checks

**Files:**
- Modify: `ui/protocol_builder.py`
- Test: `tests/test_protocol_builder_ramp_chirp.py` (create)

**Interfaces:**
- Consumes: Task 1 fields and `validate_protocol`.
- Produces: radios `_rp_type_rb`, `_ch_type_rb`; stack pages 3 (ramp) and 4 (chirp); widgets `_rp_start`, `_rp_min`, `_rp_max`, `_rp_step`, `_rp_width`, `_rp_hold`, `_rp_return`, `_rp_gap`, `_rp_reps`, `_ch_start`, `_ch_end`, `_ch_dur`, `_ch_spacing`, `_ch_amp`, `_ch_offset`, `_ch_gap`, `_ch_reps`.

- [ ] **Step 1: Write the failing tests**

Set `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` before importing Qt, and share one `QApplication`.

```python
def test_ramp_edits_reach_the_stimulus(panel):
    panel._on_add()
    panel._rp_type_rb.setChecked(True)
    panel._rp_start.setValue(-20.0)
    p = panel.get_protocol()
    assert p.stimuli[0].type == "ramp" and p.stimuli[0].ramp_start_pA == -20.0

def test_switching_rows_keeps_each_stimulus_values(panel):
    panel._on_add(); panel._rp_type_rb.setChecked(True); panel._rp_max.setValue(300.0)
    panel._on_add(); panel._ch_type_rb.setChecked(True); panel._ch_end.setValue(50.0)
    panel._stim_list.setCurrentRow(0)
    panel._stim_list.setCurrentRow(1)
    panel._stim_list.setCurrentRow(0)
    s0, s1 = panel.get_protocol().stimuli
    assert s0.ramp_max_peak_pA == 300.0
    assert s1.chirp_end_hz == 50.0

def test_save_and_load_restores_widgets(panel, tmp_path):
    # add a chirp with linear spacing, save_protocol to tmp_path, load with
    # load_protocol_from_file, assert _ch_type_rb and _ch_spacing match.

def test_defaults_fit_spin_ranges(panel):
    panel._on_add(); panel._ch_type_rb.setChecked(True)
    assert panel._ch_dur.value() == 20000.0 and panel._ch_start.value() == 0.5

def test_run_blocks_invalid_protocol(panel, monkeypatch):
    # stub QMessageBox.warning in ui.protocol_builder; connect protocol_run_requested
    # to a list; a ramp with min peak above max peak emits nothing and warns once.

def test_run_warning_can_be_cancelled_or_accepted(panel, monkeypatch):
    # chirp at 2000 Hz: QMessageBox.question stub returning No emits nothing;
    # returning Yes emits once.
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_protocol_builder_ramp_chirp.py -v`
Expected: FAIL with `AttributeError` on `_rp_type_rb`.

- [ ] **Step 3: Add the radios and pages**

Labels: "Ramp (CC)" and "Chirp (CC)", placed after "Step protocol (CC)" in the row as in the approved mockup. Stack indices stay stable, with ramp appended as 3 and chirp as 4. Fields and order follow the spec tables. `_rp_return` and `_ch_spacing` are `QComboBox` with data `"instant"`/`"ramp_down"` and `"log"`/`"linear"`. Ranges: currents ±4000 pA at 1 decimal; ramp, hold and gap times 0 to 60000 ms (ramp minimum 1); chirp duration 1 to 60000 ms; frequencies 0 to 10000 Hz at 2 decimals; repeats 1 to 100. Connect every widget to `_update_estimated_time` and `_on_stim_param_changed`.

- [ ] **Step 4: Wire the type handling**

Extend `_on_stim_type_changed`, `_populate_editor` (including the `blockSignals` tuple and the type-to-page map) and `_sync_editor_to_stim` for both types. Every new widget must be in the tuple, otherwise selecting a row overwrites stored values.

- [ ] **Step 5: Add the Run checks in `_on_run`**

After the empty-list check, call `validate_protocol(p)`. Errors: `QMessageBox.warning` listing them, then return. Warnings: `QMessageBox.question` listing them, and return unless the answer is Yes.

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_protocol_builder_ramp_chirp.py -v`
Expected: all PASS.

- [ ] **Step 7: Confirm the full window still builds**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -c "from PySide6.QtWidgets import QApplication; from ui.main_window import MainWindow; a=QApplication([]); MainWindow(); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 8: Commit**

```bash
git add ui/protocol_builder.py tests/test_protocol_builder_ramp_chirp.py
git commit -m "feat: add ramp and chirp pages to the protocol builder"
```

---

### Task 5: Documentation and rig verification

**Files:**
- Modify: `docs/guide/trial-mode.md`
- Temporary, never committed: `hardware/daq_worker.py`

**Interfaces:**
- Consumes: the finished feature.

- [ ] **Step 1: Run the full test suite**

Run: `.venv/Scripts/python.exe -m pytest tests -q`
Expected: all PASS.

- [ ] **Step 2: Add a "Ramp and chirp stimuli" section to `docs/guide/trial-mode.md`**

Before "Cancelling". Cover the parameters, that a ramp holds the start current from after the hyperpolarizing pulse through its last gap, that the start current adds to amplifier holding, the 60 s limit and the 1 kHz warning, that step analysis skips these trials (so no input resistance or resting potential from them), and that the recorded AmpCmd trace is the record of what played, because QC cannot confirm a chirp ran.

- [ ] **Step 3: Commit**

```bash
git add docs/guide/trial-mode.md
git commit -m "docs: describe ramp and chirp stimuli"
```

- [ ] **Step 4: Rig check with the requester, using a model cell or loopback**

Temporarily wrap the `_rebuild_ao` call in `hardware/daq_worker.py` with `time.perf_counter()` and print the elapsed ms and sample count. Run a default ramp, a default 23 s chirp trial and a 60 s chirp trial, in trial mode and in continuous-protocol mode.

Expected: no DAQ error, no buffer warning near the write, and AmpCmd in `analysis/visualize_trace.ipynb` matches the requested stimulus. A write under 100 ms is safe. Above that, watch for AI overrun.

- [ ] **Step 5: Remove the timing code**

Run: `git diff --stat -- hardware/daq_worker.py`
Expected: no changes.

- [ ] **Step 6: Act on the result**

If 60 s failed, lower `MAX_TRIAL_MS` to a measured safe value and update Task 1's test and the guide. If 23 s failed, stop and bring the measurement to the requester, because moving the write off the acquisition loop is a separate decision.

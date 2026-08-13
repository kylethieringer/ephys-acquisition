# Code map

One line per module — what it does and where to look first. Follow any name
into the {doc}`API reference <../autoapi/index>` for the details.

## Top level

:::{list-table}
:widths: 30 70

* - {py:mod}`main`
  - Qt application entry point
* - {py:mod}`config`
  - Hardware constants, channel definitions, scaling. See
    {doc}`configuration`.
:::

## `ui/` — Qt interface

:::{list-table}
:widths: 30 70

* - {py:mod}`ui.main_window`
  - Top-level window; owns both acquisition controllers
* - {py:mod}`ui.control_panel`
  - Mode/clamp pills, subject card, protocol widget, recording bar
* - {py:mod}`ui.camera_panel`
  - Camera preview and TTL settings
* - {py:mod}`ui.stimulus_panel`
  - Ad-hoc step stimulus, continuous mode only
* - {py:mod}`ui.trace_panel`
  - Rolling trace display and Y-range controls
* - {py:mod}`ui.protocol_builder`
  - Protocol editor
* - {py:mod}`ui.widgets`
  - Shared widgets, including the top chrome bar
:::

## `hardware/` — device workers

:::{list-table}
:widths: 30 70

* - {py:mod}`hardware.daq_worker`
  - NI DAQ AI/AO/CTR worker thread; watches buffer fill
* - {py:mod}`hardware.daq_config`
  - DAQ task configuration, including explicit `co_pulse_term` routing
* - {py:mod}`hardware.camera_worker`
  - Basler camera worker thread
* - {py:mod}`hardware.camera_config`
  - Camera settings
:::

## `acquisition/` — recording

:::{list-table}
:widths: 30 70

* - {py:mod}`acquisition.continuous_mode`
  - Continuous acquisition controller. See {doc}`../guide/continuous-mode`.
* - {py:mod}`acquisition.continuous_protocol_runner`
  - Flattens a protocol into a sample-offset event timeline
* - {py:mod}`acquisition.trial_mode`
  - Trial state machine, advanced by sample counting. See
    {doc}`../guide/trial-mode`.
* - {py:mod}`acquisition.trial_protocol`
  - Protocol data model and JSON serialisation — no Qt or hardware imports
* - {py:mod}`acquisition.trial_waveforms`
  - AO waveform builders for CC and VC
* - {py:mod}`acquisition.data_buffer`
  - Ring buffer backing the live display
* - {py:mod}`acquisition.data_saver`
  - `ContinuousSaver` — binary during recording, HDF5 after
* - {py:mod}`acquisition.trial_saver`
  - `TrialSaver` — binary during recording, per-trial HDF5 after
:::

## `analysis/` — offline

Nothing here touches hardware; everything operates on saved files.

:::{list-table}
:widths: 30 70

* - {py:mod}`analysis.analyze_steps`
  - Step detection, resting potential, input resistance
* - {py:mod}`analysis.detect_spikes`
  - Spike detection from a membrane-voltage trace
* - {py:mod}`analysis.batch_intrinsics`
  - Batch driver over the experiment log
* - {py:mod}`analysis.summary_figures`
  - Per-cell F-I, Ri, and RMP summaries
* - {py:mod}`analysis.align_video`
  - Composite video aligned via TTL edges
* - {py:mod}`analysis.analysis_gui`
  - Interactive analysis GUI
* - {py:mod}`analysis.qc_report`
  - CLI to re-run QC on an existing recording
* - {py:mod}`analysis.qc_alignment`
  - CLI for the standalone rig alignment check
:::

### `analysis/qc/` — the QC pipeline

:::{list-table}
:widths: 30 70

* - {py:mod}`analysis.qc`
  - `Check`, `Status`, `worst` — the lightweight package root
* - {py:mod}`analysis.qc.hook`
  - Fire-and-forget runner used from the acquisition path
* - {py:mod}`analysis.qc.load`
  - Loads a recording plus every companion artifact into one bundle
* - {py:mod}`analysis.qc.integrity`
  - Sample counts, finite values, TTL ↔ video, event tables
* - {py:mod}`analysis.qc.signal`
  - Per-channel signal sanity
* - {py:mod}`analysis.qc.stimulus`
  - Commanded vs recorded fidelity
* - {py:mod}`analysis.qc.alignment`
  - Phase A/B rig alignment checks
* - {py:mod}`analysis.qc.report`
  - Orchestrator, plot builders, and `run_qc`
* - {py:mod}`analysis.qc.descriptions`
  - Human-readable interpretation text for the reports
:::

## `utils/`

:::{list-table}
:widths: 30 70

* - {py:mod}`utils.stimulus_generator`
  - Pure waveform generation — no hardware imports
* - {py:mod}`utils.data_loader`
  - Load and quick-plot saved HDF5 files
* - {py:mod}`utils.experiment_checklist`
  - Experiment-day checklist GUI. See {doc}`../guide/experiment-checklist`.
* - {py:mod}`utils.experiment_dashboard`
  - Streamlit browser over the experiment log
* - {py:mod}`utils.update_experiment_log`
  - Builds the experiment log CSV from sidecars
:::

## Not documented

`analysis/_head_sf.py` and `analysis/align_video_skeleton.py` are work in
progress and deliberately excluded from the API reference.

## Import boundaries worth knowing

These are load-bearing design decisions, not accidents:

- {py:mod}`acquisition.trial_protocol` and {py:mod}`utils.stimulus_generator`
  have **no Qt or hardware imports** — safe to use from a notebook or a test.
- {py:mod}`analysis.qc` stays cheap to import; `run_qc` lives in
  {py:mod}`analysis.qc.report` so matplotlib, plotly, and jinja2 load only when
  a report is actually rendered.
- {py:mod}`utils.data_loader` imports without `h5py` or `matplotlib` present and
  raises only when you call something needing them.
- Nothing in `analysis/` touches hardware, so the whole package runs on any
  machine.

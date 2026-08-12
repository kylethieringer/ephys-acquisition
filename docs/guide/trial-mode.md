# Trial mode

Trial mode saves each trial as its own HDF5 group, with a matching video file.
Datasets are pre-allocated, so sequential reads during analysis are fast and
trial boundaries are unambiguous.

Use it when the experiment is genuinely trial-structured and you know the
boundaries in advance. If you might want to re-cut trials later, use
{doc}`continuous-mode` instead.

## Running

1. Select **Trial-based** mode
2. Load or build a protocol
3. Click **Run Protocol**

Each trial is written to `/trial_001/`, `/trial_002/`, … with a per-trial video
alongside. The clamp pill is hidden in this mode — the protocol's `clamp_mode`
governs.

## The state machine

{py:class}`acquisition.trial_mode.TrialAcquisition` advances by **counting AI
samples**, not by wall-clock timers. It runs entirely in the GUI thread via the
`_on_ai_chunk` slot, so no locking is needed on state variables.

```text
IDLE
  │  run_protocol() called
  ▼
ITI      inter-trial interval — AO silent, TTL off
  │  iti_samples counted
  ▼
PRE      pre-baseline — TTL fires, data accumulates
  │  pre_samples counted
  ▼
TRIAL    stimulus + post — data accumulates
  │  (total_samples − pre_samples) counted
  │  → TTL stops, trial saved
  ▼
ITI      ← repeat until all trials done, or cancel requested
  │  all trials done / cancel
  ▼
DONE
```

:::{important}
Sample counting is why trial timing is exact. A timer-driven state machine
would drift against the 20 kHz acquisition clock; counting samples means the
pre/stimulus/post boundaries land on precise sample indices, and every trial in
a run is the same length to the sample.
:::

The camera TTL is active for the **full** trial window — pre + stimulus + post
— and off during the ITI.

## Cancelling

**Stop Protocol** cancels gracefully: the current trial finishes and is saved,
then the run ends. You do not lose a partially-recorded trial to a hard stop.

`stop()` cancels any active run before shutting the workers down.

## Trial order

Trial order is randomised by
{py:func}`acquisition.trial_protocol.build_trial_order`, honouring
`repeats_per_stimulus`. The realised order is written to
`/metadata/trial_order`, and each trial group also carries its own
`stimulus_name` and `stimulus_index` attributes — so analysis never has to
reconstruct what was played.

## On disk

```text
/metadata/
    protocol         full JSON protocol definition
    trial_order      int32 array
    clamp_mode, sample_rate, channel_names, display_scales, units, start_time
/subject/
/trial_001/
    analog_input     float64 (n_channels × n_samples)
    attrs: stimulus_name, stimulus_index, trial_index, onset_time, video_file
/trial_002/ ...
```

As in continuous mode, data is appended to a flat `.bin` file during the run
and converted to HDF5 by {py:class}`acquisition.trial_saver.TrialSaver` when
the protocol ends. An in-memory index table tracks each trial's byte offset and
sample count. The `.bin` is always kept as a backup.

Full schema: {doc}`../data/hdf5-format`.

## Choosing between the modes

:::{list-table}
:header-rows: 1
:widths: 34 33 33

* -
  - Continuous
  - Trial
* - Recording
  - One unbroken stream
  - One group per trial
* - Trial boundaries
  - Events in `/stimulus_events/`, re-cuttable
  - Fixed at acquisition time
* - Video
  - One file per recording
  - One file per trial
* - Best when
  - You may re-cut trials, or want uninterrupted context
  - Structure is known and you want fast per-trial reads
:::

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

## Ramp and chirp stimuli

Ramps and chirps are current-clamp stimuli and can share a protocol with steps
and baselines. Each trial still has the pre baseline, hyperpolarizing pulse and
post baseline.

A **ramp** rises linearly from the start current to a peak, with one ramp for
each peak from the lowest to the highest. You can hold at the peak, and return
either instantly or by ramping down over the same time as the ramp up.

Current stays at the start current from just after the hyperpolarizing pulse
through the last gap, so it never drops to zero between ramps. It returns to
zero for the post baseline. The start current adds to any holding current set
on the amplifier, so leave it at 0 pA to ramp from your holding level.

A **chirp** is a sine sweep between two frequencies with log or linear spacing,
plus an optional DC offset. Gaps between chirps sit at 0 pA. Chirps have no
taper, so an offset or the sweep's final phase can cause a small current step at
the edges.

The builder refuses to run:

- a ramp or chirp in a voltage-clamp protocol
- a ramp or chirp trial longer than 60 s
- a frequency at or above 10 kHz, or a current beyond ±4000 pA

**Run Protocol** on the Acquire page makes the same checks, and the builder
won't load a file that fails them.

Chirp frequencies above 1 kHz give a warning you can accept, because the analog
output's stepping becomes significant.

:::{warning}
QC does not confirm that a chirp played. A default 20 pA chirp is below the QC
noise floor in trial mode, and continuous mode only compares overall activity
inside and outside stimulus windows. Large chirps can also warn on good
recordings, because the output's start time isn't tied to a known sample.

The recorded `AmpCmd` trace is the record of what was delivered. Check it by eye,
and use it for any later frequency analysis.
:::

Step analysis skips ramp and chirp trials, including their hyperpolarizing
pulses. A recording with only ramps, chirps or baselines reports no step
protocols, so include a step protocol if you need input resistance or resting
potential.

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
reconstruct what was played. `stimulus_name` is a label generated from the
stimulus parameters (e.g. `Steps -50 to 50 pA by 10 pA`); use
`stimulus_index` to identify the protocol entry, since two entries with the
same parameters share a label.

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

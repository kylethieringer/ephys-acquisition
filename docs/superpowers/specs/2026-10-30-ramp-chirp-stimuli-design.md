# Ramp and chirp current-clamp stimuli: design

Date: 2026-10-30

## Why

Two questions about the recorded neurons:

- **Frequency tuning:** do some neurons respond preferentially to particular
  input frequencies? A chirp answers this.
- **Spike-limiting conductances:** do some cells have conductances that prevent
  spiking? A ramp shows how a cell approaches threshold, whether it is delayed,
  and whether it fails to fire.

The workflow is to look at raw traces first. Calculated results come later.

## Goals

- Add **Ramp (CC)** and **Chirp (CC)** to the stimulus builder, with every
  parameter below settable.
- Both behave like existing stimulus types: they appear in the stimulus list
  with a generated label, duplicate and reorder, save and load in protocol JSON,
  count toward estimated run time, and run in both trial and continuous-protocol
  acquisition.
- Ramps, chirps, steps and baselines can be mixed in one current-clamp protocol.
- Exact stimulus parameters are saved with each recording, so analysis can
  regenerate the waveform.
- Raw traces of the new stimuli are viewable with the existing tools, and those
  tools do not fail or produce misleading output.

## Non-goals

- Calculated results: impedance curves, resonance frequency, ramp spike
  threshold, first-spike latency. This is a separate later project, after real
  recordings exist.
- Voltage-clamp ramps or chirps.
- Other stimulus shapes (noise, pulse trains, arbitrary waveforms).
- A waveform preview in the builder. The mockup sketches were illustrations.

## Anti-goals

- A ramp or chirp must never silently play as something else. See the
  voltage-clamp risk under Constraints.
- Chirp cycles or ramps must never appear as current steps in F-I or intrinsic
  properties output.
- Existing protocols and recordings must keep loading and analyzing exactly as
  they do now.

## Stimulus definitions

### Ramp

Parameters (mockup defaults are placeholders):

| Setting | Default | Meaning |
|---|---|---|
| Start current | 0 pA | Current at the start of each ramp |
| Lowest peak current | 50 pA | First ramp's peak |
| Highest peak current | 150 pA | Last ramp's peak |
| Peak step size | 50 pA | Increase in peak between ramps; must be positive |
| Ramp duration | 1000 ms | Time from start current to peak |
| Hold at peak | 0 ms | Time held at the peak |
| Return to start | Instant | Instant, or a ramp down lasting the ramp duration |
| Gap between ramps | 500 ms | Time after each ramp, held at the start current |
| Repeats within trial | 1 | Times the full series plays in one trial |

Peaks run from lowest to highest, inclusive, matching how the step protocol
enumerates amplitudes.

The current must not jump to 0 between ramps. It stays at the start current from
the beginning of the gap after the hyperpolarizing pulse until the end of the last
gap, then returns to 0 for the post baseline. With no hyperpolarizing pulse, it
steps to the start current when the stimulus begins. Pre baseline, the
hyperpolarizing pulse and post baseline stay at 0 pA of added AO current, so the
start current is added on top of any holding set on the amplifier.

### Chirp

| Setting | Default | Meaning |
|---|---|---|
| Start frequency | 0.5 Hz | Instantaneous frequency at the start of the sweep |
| End frequency | 100 Hz | Instantaneous frequency at the end |
| Sweep duration | 20000 ms | Length of one sweep |
| Sweep spacing | Log | Log or linear |
| Amplitude | 20 pA | Peak amplitude of the sine |
| DC offset | 0 pA | Added to the sine during the sweep |
| Gap between chirps | 500 ms | Silence after each chirp, at 0 pA |
| Repeats within trial | 1 | Sweeps in one trial |

Waveform, with `t = n / sample_rate`, `T` the sweep duration, `f0` and `f1` the
start and end frequencies:

```
I(t) = offset + amplitude * sin(phi(t))

linear: phi(t) = 2*pi * (f0*t + (f1 - f0)*t^2 / (2*T))
log:    phi(t) = 2*pi * f0*T / ln(f1/f0) * ((f1/f0)^(t/T) - 1)
```

The phase starts at zero. The waveform must be deterministic, because QC
regenerates it from the saved protocol, and later analysis may do the same.

## Trial structure

Both types use the existing current-clamp layout: pre baseline, hyperpolarizing
pulse, gap, stimulus, post baseline. As with step protocols, `gap_ms` is reused
as the gap after the hyperpolarizing pulse. The builder always includes the
hyperpolarizing pulse in current clamp, and that does not change. Trial
randomization and repeats per stimulus do not change.

## Builder

- Add "Ramp (CC)" and "Chirp (CC)" radio buttons and matching editor pages,
  as in the approved mockup.
- Spin box ranges must allow practical values. The current 10000 ms limit is too
  short for the 20000 ms chirp default, and frequencies need sub-Hz precision.
- On Run, block with a message box naming the problem if any of these hold:
  - a ramp or chirp is in a voltage-clamp protocol
  - lowest peak is above highest peak, or peak step is not positive
  - any frequency is at or above Nyquist (half of `SAMPLE_RATE`, 10 kHz)
  - log spacing with a start frequency of 0
  - start and end frequency are equal
  - the largest commanded current exceeds what AO can output at
    `AO_PA_PER_VOLT` and the DAQ's output range
  - one ramp or chirp trial (pre + stimulus + post) is longer than 60 s; step
    protocols are not limited, so existing protocols keep running
- Warn, but allow continuing, if a chirp's highest frequency is above
  `SAMPLE_RATE / 20` (1 kHz), where the staircase in the AO output becomes
  significant.

## Data and compatibility

- Add new optional fields to `StimulusDefinition`. Existing JSON and recordings
  load with defaults through the existing `protocol_from_dict` path.
- Recordings store the protocol JSON already, so the new parameters are saved
  without changing the file format.
- `stimulus_label` produces labels such as `Ramp 0 to 50-150 pA by 50 pA, 1000 ms`
  and `Chirp 0.5-100 Hz log, 20 pA, 20000 ms`. These become `stimulus_name` in
  the HDF5 file.
- `_stim_duration_ms` must cover both types, including the hyperpolarizing pulse
  and gap.

## Constraints from the existing code

- **Voltage-clamp risk:** `build_trial_waveform` picks a builder from the clamp
  mode, not the stimulus type. A ramp in a voltage-clamp protocol would silently
  play a voltage step using `step_mV`. The Run check prevents it from the UI, and
  the waveform code must also raise for ramp or chirp in voltage clamp, because
  protocols can be loaded from JSON.
- **CC waveform builder:** `build_cc_trial_waveform` always generates a step
  protocol, so it must select the stimulus body by type. Types it does not know
  must raise. `voltage_step` in a current-clamp protocol keeps today's behavior,
  so old recordings regenerate unchanged.
- **Zero-valued parameters:** the existing `value or default` pattern would turn
  a valid 0 into the default. New fields must test `is None`.
- **Step analysis:** `analyze_steps.find_step_protocols_from_events` thresholds
  the command channel in every apply-to-clear window, so a ramp becomes a false
  pulse and a chirp becomes many. Windows whose protocol entry is a ramp or chirp
  must be skipped. `_expected_step_count` returning `None` is not enough, because
  those windows are still kept. Identify these windows from the protocol
  sidecar, and fall back to the `Ramp` or `Chirp` prefix of `stimulus_name` when
  the sidecar is missing.
- **Waveform fallback:** the recording analysis falls back to
  `find_step_protocols_from_waveform` when event detection finds no steps, which
  would turn a ramp-only or chirp-only recording into false pulses. Do not fall
  back when ramp or chirp windows were skipped. Report "no step protocols" and
  return `None`, as a recording with no steps does today. Files with no event
  data keep the fallback.
- **Long trials:** `DAQWorker._rebuild_ao` writes the full waveform inside the
  acquisition loop, and the AI buffer holds `CHUNK_SIZE * 10`, or 100 ms. A long
  write could overrun AI and end the recording. The requester expects trials of
  about 23 s, rarely much longer, and wants this timed on the rig. Do not move the
  write off the loop unless that measurement shows it is needed.
- **Stimulus verification:** the recorded AmpCmd trace is the source of truth for
  what was delivered, for checking by eye now and for calculations later. No new
  QC and no sample-aligned AO start in this project.
- **QC limits:** `analysis/qc/stimulus.py` uses `build_trial_waveform`, so
  trial-mode QC runs on the new types without changes. It is not proof that a
  chirp played. A default 20 pA chirp is below its 50 mV floor, continuous mode
  only compares activity inside and outside stimulus windows, and unknown AO start
  timing can make large chirps warn on good recordings. State this in the trial
  mode guide so a QC result is not misread.
- **Intrinsics:** skipping ramp and chirp windows also skips their hyperpolarizing
  pulses. A recording with only those stimuli has no input resistance or resting
  potential from step analysis. The requester accepted this.
- **Working tree:** many relevant files have uncommitted changes, including
  `trial_protocol.py`, `protocol_builder.py`, `analyze_steps.py` and
  `analysis/qc/stimulus.py`. Build on them and do not revert them.
- **Code home:** waveform math belongs in `utils/stimulus_generator.py` next to
  `generate_step_protocol_pa_array`, as pure numpy functions.

## Verification

- Pytest: waveform length and values for both types, including ramp series order,
  hold, ramp down, linear and log chirp start and end frequency, duration
  estimates, JSON round trip, legacy protocol loading, voltage-clamp rejection,
  validation rules, and step analysis skipping ramp and chirp windows.
- Headless GUI check: `MainWindow()` builds under `QT_QPA_PLATFORM=offscreen`,
  and a protocol with each new type survives add, edit, save and load.
- On the rig: run each type with the amplifier in a model cell or loopback and
  confirm the AmpCmd trace in `analysis/visualize_trace.ipynb` matches.
- On the rig: time the AO waveform load for a 23 s and a 60 s trial during live
  acquisition, in both trial and continuous-protocol modes, and confirm no AI
  overrun or dropped data. If the 60 s load fails, lower the limit or move the
  write off the loop before finishing.

## Calls made during design

These were not explicitly decided by the requester:

- The start current begins right after the hyperpolarizing pulse and ends with
  the last gap, rather than also covering the pre and post baselines.
- A ramp down takes the same time as the ramp up.
- Chirps have no taper, so a non-zero offset or end phase causes a small step at
  the edges of the sweep.
- Downward chirps (start frequency above end frequency) are allowed.
- The 1 kHz warning threshold and the Run blocks above, including the 60 s
  maximum trial length.
- Ramps and chirps reuse `gap_ms` and `step_protocol_repeats`, rather than adding
  duplicate fields.
- A recording with only ramps, chirps or baselines reports "no step protocols"
  rather than an empty result.
- `voltage_step` in a current-clamp protocol is left as it is today.
- New field names are left to the builder.

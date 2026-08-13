# Protocols

A **protocol** defines everything needed to run a structured recording:
the clamp mode, a list of stimuli, and the timing around them. Protocols are
plain JSON files in `D:/protocols`, and the same protocol can be run either as
a continuous recording or as discrete trials.

The data model lives in {py:mod}`acquisition.trial_protocol`, which has no Qt
or hardware dependencies — you can build, save, and inspect protocols from a
notebook.

## Anatomy

:::{list-table}
:header-rows: 1
:widths: 28 72

* - Field
  - Meaning
* - `clamp_mode`
  - Current clamp or voltage clamp
* - `stimuli`
  - List of {py:class}`~acquisition.trial_protocol.StimulusDefinition`, one per
    stimulus
* - `pre_ms`
  - Silent baseline recorded before each stimulus
* - `post_ms`
  - Silent tail recorded after each stimulus ends
* - `iti_ms`
  - Inter-trial interval — quiet period *between* trials, AO silent and camera
    TTL off
* - `repeats_per_stimulus`
  - How many times each stimulus is played
* - `hyperpolarization`
  - Optional sub-threshold negative current step prepended to each trial
    (current clamp only), for estimating access resistance
:::

## Stimulus types

The `type` field selects which of the other fields are active.

::::{tab-set}

:::{tab-item} step_protocol (CC)
A current-clamp staircase. Uses:

`min_pA`, `max_pA`, `step_pA`, `step_width_ms`, `gap_ms`,
`step_protocol_repeats`

Steps run from `min_pA` to `max_pA` inclusive in increments of `step_pA` (which
must be positive), each held for `step_width_ms` and separated by `gap_ms`.
`step_protocol_repeats` repeats the whole staircase within a single trial.

{py:func}`utils.stimulus_generator.get_step_amplitudes` returns the exact
amplitude list a given set of parameters produces.
:::

:::{tab-item} voltage_step (VC)
A voltage-clamp step. Uses:

`step_mV`, `duration_ms`

`step_mV` is relative to the holding potential set on the amplifier. The AO
command is 0 V during the pre and post windows.
:::

::::

## Building one

Use the **Protocol** page: the list on the left shows everything in
`D:/protocols` with a filter box; the builder on the right edits the selected
protocol. **New** starts a fresh one, **Refresh** (↻) re-reads the directory.

From code:

```python
from acquisition.trial_protocol import (
    TrialProtocol, StimulusDefinition, save_protocol, estimated_total_duration_s
)

protocol = TrialProtocol(
    name="FI_curve",
    clamp_mode="current_clamp",   # or "voltage_clamp"
    pre_ms=500,
    post_ms=500,
    iti_ms=3000,
    repeats_per_stimulus=3,
    stimuli=[
        StimulusDefinition(
            type="step_protocol",
            name="staircase",
            min_pA=-100, max_pA=400, step_pA=50,
            step_width_ms=500, gap_ms=500,
            step_protocol_repeats=1,
        ),
    ],
)

print(f"{estimated_total_duration_s(protocol) / 60:.1f} min")
save_protocol(protocol, "D:/protocols/FI_curve.json")
```

:::{tip}
{py:func}`~acquisition.trial_protocol.estimated_total_duration_s` is worth
calling before you commit to a protocol — it is easy to build something that
looks reasonable and takes forty minutes per cell.
:::

## Running one

Select the protocol from the dropdown on the Acquire page and click
**Run Protocol**. What happens next depends on the mode pill:

- **Continuous** — one unbroken recording, stimulus timing saved as events.
  See {doc}`continuous-mode`.
- **Trial** — one HDF5 group per trial. See {doc}`trial-mode`.

In both cases recording starts and stops automatically; you do not press
Record yourself.

## Trial order

{py:func}`~acquisition.trial_protocol.build_trial_order` returns a **randomised**
list of stimulus indices for one protocol run, honouring
`repeats_per_stimulus`. The realised order is saved with the recording, so
analysis never has to guess what was played when.

## On disk

{py:func}`~acquisition.trial_protocol.save_protocol` and
{py:func}`~acquisition.trial_protocol.load_protocol` read and write the JSON;
{py:func}`~acquisition.trial_protocol.protocol_to_dict` and
{py:func}`~acquisition.trial_protocol.protocol_from_dict` handle conversion if
you want to store a protocol somewhere else. The full protocol JSON is also
embedded in trial-mode recordings under `/metadata/protocol`.

:::{note}
Protocol files are hand-editable JSON. If you edit one outside the builder,
click ↻ in the dropdown to pick up the change.
:::

# Continuous mode

Continuous mode records one unbroken stream. When a protocol runs, the
recording is *not* broken into trials — instead, every stimulus onset and
offset is logged as a sample-accurate event, and you slice the recording into
pseudo-trials later.

This is the right mode when you want an uninterrupted view of the cell, or when
you may want to re-cut trial boundaries during analysis.

## Lifecycle

{py:class}`acquisition.continuous_mode.ContinuousAcquisition` coordinates the
DAQ worker, camera worker, ring buffer, and saver:

```text
Start            → DAQ AI begins (traces visible); AO silent;
                   camera opens in hardware-triggered mode
                   (no TTL yet — no frames arrive)
Record           → TTL fires; camera captures every triggered frame;
                   HDF5 file and video file open
Stop Recording   → TTL ceases; after the guard delay HDF5 + video close;
                   camera stays open and armed for the next recording
Stop             → camera closed; DAQ shut down
```

The gap between **Start** and **Record** is deliberate: it is where you patch,
with live traces and nothing being written.

:::{note}
After **Stop Recording**, the camera may still deliver one final frame
triggered by the last TTL pulse. The HDF5 stays open an extra
{py:data}`config.CAMERA_GUARD_DELAY_MS` ms (2 s) to capture the trailing
exposure-return signal on `ai3` before closing.
:::

## Running a protocol

1. Select **Continuous** mode and set the clamp mode
2. Load a protocol from the dropdown, or build one on the Protocol page
3. Click **Run Protocol**

From there it is automatic:

- Recording starts
- Stimulus waveforms are applied at the correct sample offsets
- Each stimulus onset and offset is logged to `/stimulus_events/`
- Recording stops when the protocol finishes

## How the timeline works

{py:class}`acquisition.continuous_protocol_runner.ContinuousProtocolRunner`
flattens the protocol into a list of events, each carrying a sample offset
relative to the recording start:

```text
(sample_offset, action, waveform, stimulus_name, stimulus_index)
```

An event fires when `n_saved >= recording_start + event.sample_offset`. Each
stimulus block produces exactly two events:

`"apply"`
: Send the AO waveform to the DAQ worker.

`"clear"`
: Revert `ao0` to 0 V.

The inter-trial interval is simply a silent gap between blocks.

## Slicing into pseudo-trials

The `/stimulus_events/` table is what makes this mode useful. Every event
carries the sample index at which it occurred, so trial boundaries are exact —
no inference from the command trace required:

```python
import h5py
import numpy as np

with h5py.File("KT001_CS_20260812.h5", "r") as f:
    data = f["data"]["analog_input"][:]          # (n_channels, n_samples)
    sr = f["metadata"].attrs["sample_rate"]
    ev = f["stimulus_events"]
    sample_index = ev["sample_index"][:]
    event_type = [e.decode() for e in ev["event_type"][:]]
    stim_name = [s.decode() for s in ev["stimulus_name"][:]]

# One pseudo-trial per "apply" event: 500 ms before onset to 2 s after
pre, post = int(0.5 * sr), int(2.0 * sr)
trials = [
    data[:, i - pre : i + post]
    for i, t in zip(sample_index, event_type)
    if t == "apply" and i - pre >= 0
]
```

See {doc}`../data/hdf5-format` for the full event-table schema.

## Ad-hoc stimulation

The stimulus panel on the Acquire page applies a step stimulus without a
protocol — useful for probing a cell before committing to a protocol run. It is
available in continuous mode only, and its labels switch between pA and mV with
the clamp mode.

:::{warning}
Ad-hoc stimuli are applied to the amplifier but are **not** written to
`/stimulus_events/`. Only protocol-driven stimuli are logged. If you need the
timing on disk, run a protocol.
:::

## Threading

All public methods are called from the GUI thread. The AI-chunk and
camera-frame handlers are Qt slots connected with `AutoConnection`, so they
execute on the GUI thread even though the signals are emitted from worker
threads. No locking is needed on the saver or ring buffer.

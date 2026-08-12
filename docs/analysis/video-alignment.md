# Video alignment

Produces a composite video where the camera image and the membrane-potential
trace advance together, so behaviour and physiology can be read off one frame.

```text
┌─────────────────────────────┐
│      camera image at t      │
├─────────────────────────────┤
│  Vm (mV), ±1 s centred on t │
│         with centre line    │
└─────────────────────────────┘
```

## Running it

```bash
# Continuous recording
uv run python -m analysis.align_video recording.h5 video.avi output.avi

# Trial recording — pick one trial
uv run python -m analysis.align_video trials.h5 video.avi output.avi --trial 3
```

Omit the output path and the file is written next to the HDF5 as
`<stem>_aligned.avi`, or `<stem>_trial<N>_aligned.avi` for trial mode.

Further options: `--vm-channel` and `--ttl-channel` to override channel
selection, `--half-window` to change the ±1 s trace window, and
`--trace-height` to resize the bottom panel.

## How frames get timestamps

Frame times are not assumed from the nominal frame rate. They are derived from
**rising edges on `ai4` (TTLLoopback)**, which fires one pulse per camera frame
— the same signal, on the same clock, as the ephys data.

{py:func}`analysis.align_video.find_frame_samples` returns those rising-edge
sample indices; {py:func}`analysis.align_video.align` builds the composite.

:::{important}
This is why alignment survives a dropped frame. A frame that never arrived
leaves no TTL edge, so the remaining frames keep their true sample positions
instead of every subsequent frame sliding by one period.
:::

A mismatch between TTL edge count and video frame count is exactly what the
QC pass reports as TTL ↔ video drift — see {doc}`../qc/post-recording`. Worth
checking before spending time on a long alignment run.

## Programmatic use

```python
from analysis.align_video import find_frame_samples, align

# Sample index of every camera frame
frame_samples = find_frame_samples(ttl_trace)   # threshold defaults to 2.5 V
```

Use `find_frame_samples` on its own whenever you need to relate any per-frame
measurement — tracking output, pose estimates — back to sample indices in the
ephys recording. The composite video is only one consumer of it.

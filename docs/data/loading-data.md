# Loading data in Python

{py:mod}`utils.data_loader` is the shortest path from a saved recording to
arrays you can work with.

## Load a recording

```python
from utils.data_loader import load_hdf5

rec = load_hdf5("D:/data/KT001/KT001_CS_20260812.h5")
```

{py:func}`~utils.data_loader.load_hdf5` returns a dict:

:::{list-table}
:header-rows: 1
:widths: 24 76

* - Key
  - Contents
* - `data`
  - `(n_channels, n_samples)` array of **raw voltage**
* - `sample_rate`
  - Sampling rate in Hz
* - `channel_names`
  - List of channel names, in row order of `data`
* - `display_scales`
  - Per-channel multipliers from volts to display units
* - `units`
  - Per-channel unit strings
* - `start_time`
  - ISO-8601 recording start
* - `metadata`
  - Dict of `sample_rate`, `start_time`, `n_samples`, `n_channels`
:::

## Convert to physical units

`data` is raw volts. Apply the per-channel scale:

```python
import numpy as np

names = rec["channel_names"]
scales = rec["display_scales"]
sr = rec["sample_rate"]

vm = rec["data"][names.index("ScAmpOut")] * scales[names.index("ScAmpOut")]
t = np.arange(vm.size) / sr

print(f"{vm.mean():.1f} {rec['units'][names.index('ScAmpOut')]} resting")
```

:::{important}
Channel *names* differ between clamp modes — `ScAmpOut` in current clamp is
`I_mem` in voltage clamp. Index by name from `channel_names` rather than
hard-coding row 0, and your analysis will work in both modes. See
{doc}`../guide/clamp-modes`.
:::

## Quick plots

Two convenience plotters, both of which load the file for you:

```python
from utils.data_loader import plot_data, plot_data_overlay

# One subplot per channel, correct names, scales, and units
plot_data("D:/data/KT001/KT001_CS_20260812.h5")

# Just the first 10 seconds
plot_data("D:/data/KT001/KT001_CS_20260812.h5", time_range=(0, 10))

# All channels overlaid on one axes
plot_data_overlay("D:/data/KT001/KT001_CS_20260812.h5", colors=["k", "b", "r"])
```

Both take `figsize` and `show`. Pass `show=False` to get the figure back
without displaying it, so you can save or restyle it.

## Trial recordings

{py:func}`~utils.data_loader.load_hdf5` targets continuous recordings. For
trial files, read the groups directly:

```python
import h5py

with h5py.File("KT001_CS_20260812_trials.h5", "r") as f:
    names = [n.decode() for n in f["metadata"]["channel_names"][:]]
    scales = f["metadata"]["display_scales"][:]
    ch = names.index("ScAmpOut")

    trials = []
    for k in sorted(k for k in f if k.startswith("trial_")):
        g = f[k]
        trials.append({
            "stimulus": g.attrs["stimulus_name"],
            "index": g.attrs["trial_index"],
            "vm": g["analog_input"][ch, :] * scales[ch],
        })
```

For QC work, {py:func}`analysis.qc.load.load_recording` loads a recording
*and* everything around it — sidecar, video paths, acquisition log, stimulus
events — into one bundle, and auto-detects continuous vs trial mode.

## Downstream analysis

Rather than starting from raw arrays, check whether an existing entry point
already does what you need:

- {py:func}`analysis.detect_spikes.detect_spikes` — spike indices from a
  membrane-voltage trace
- {py:mod}`analysis.analyze_steps` — step detection, resting potential, input
  resistance
- {py:func}`analysis.batch_intrinsics.collect_intrinsics` — intrinsics across
  many recordings at once

See {doc}`../analysis/intrinsics`.

:::{note}
{py:mod}`utils.data_loader` degrades gracefully — it imports without `h5py` or
`matplotlib` present and raises a clear error only when you call something that
needs them.
:::

# HDF5 layout

Two layouts, one per acquisition mode. Both share the same `/metadata/` and
`/subject/` structure, written by
{py:func}`acquisition.write_common_hdf5_metadata`.

:::{important}
`analog_input` is stored as **raw volts**. Physical units come from multiplying
by the per-channel `display_scale`, which is stored alongside in
`/metadata/display_scales`. Scaling is never baked into the samples — that way
a mis-set clamp mode is recoverable.
:::

## Continuous recording — `.h5`

```text
/metadata/
    sample_rate      int          (attribute)
    start_time       str          (attribute, ISO-8601)
    channel_names    string array
    display_scales   float64 array
    units            string array
/subject/            attributes: expt_id, genotype, age, sex, …
/data/
    analog_input     float64 (n_channels × n_samples), LZF compressed,
                     chunked in 1-second blocks
/stimulus_events/    present only when a protocol ran in continuous mode
    sample_index     int64
    event_type       string   ("apply" or "clear")
    stimulus_name    string
    stimulus_index   int32
```

### The stimulus event table

One row per event, two rows per stimulus block:

`apply`
: The AO waveform was sent to the DAQ at this sample index.

`clear`
: `ao0` was reverted to 0 V at this sample index.

Because `sample_index` is in the same units as the data array's second axis,
slicing is exact — `data[:, idx]` is the sample at which the event fired. See
{doc}`../guide/continuous-mode` for a worked pseudo-trial example.

## Trial recording — `_trials.h5`

```text
/metadata/
    protocol         full JSON protocol definition
    trial_order      int32 array
    clamp_mode       str
    sample_rate, start_time, channel_names, display_scales, units
/subject/
/trial_001/
    analog_input     float64 (n_channels × n_samples)
    attrs: stimulus_name, stimulus_index, trial_index, onset_time, video_file
/trial_002/
    ...
```

Trial datasets are pre-allocated, which is what makes sequential reads fast.
Each group carries its own stimulus identity, so you never have to cross-index
against `trial_order` unless you want the presentation sequence itself.

`video_file` names the per-trial AVI, so video and ephys stay associated even if
files are moved together into another folder.

## Reading it

For most purposes use {py:func}`utils.data_loader.load_hdf5`, which returns a
dict with the scaling already available — see {doc}`loading-data`. Directly with
h5py:

```python
import h5py
import numpy as np

with h5py.File("KT001_CS_20260812.h5", "r") as f:
    sr = f["metadata"].attrs["sample_rate"]
    names = [n.decode() for n in f["metadata"]["channel_names"][:]]
    scales = f["metadata"]["display_scales"][:]
    units = [u.decode() for u in f["metadata"]["units"][:]]
    volts = f["data"]["analog_input"][:]        # (n_channels, n_samples)

# Convert to display units
scaled = volts * scales[:, None]

vm = scaled[names.index("ScAmpOut")]            # mV in current clamp
t = np.arange(vm.size) / sr
```

Trial mode:

```python
with h5py.File("KT001_CS_20260812_trials.h5", "r") as f:
    trial_keys = sorted(k for k in f if k.startswith("trial_"))
    for k in trial_keys:
        g = f[k]
        data = g["analog_input"][:]
        print(k, g.attrs["stimulus_name"], g.attrs["onset_time"], data.shape)
```

:::{note}
String datasets and attributes come back as bytes from h5py and need
`.decode()`. {py:func}`utils.data_loader.load_hdf5` handles this for you.
:::

## Compression

Continuous `analog_input` uses LZF compression with 1-second chunks. LZF is
fast enough not to matter on the write side and keeps whole-second reads
cheap — the natural access pattern for both display and analysis.

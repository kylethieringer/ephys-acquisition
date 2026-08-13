# Configuration

{py:mod}`config` is the single authoritative source for physical units, scaling
factors, and hardware identifiers. Everything else imports from it. There is no
settings file and no hidden state — if a hardware fact matters, it is here.

Constants are intentionally module-level rather than dataclass fields, so they
import as plain names without instantiating anything.

## Device and timing

:::{list-table}
:header-rows: 1
:widths: 30 12 58

* - Constant
  - Default
  - Meaning
* - {py:data}`~config.DEVICE_NAME`
  - `"Dev1"`
  - NI DAQ device name as shown in NI MAX
* - {py:data}`~config.SAMPLE_RATE`
  - `20000`
  - AI/AO sample rate in Hz — 50 µs resolution, enough to resolve a ~1 ms
    action potential
* - {py:data}`~config.CHUNK_SIZE`
  - `200`
  - Samples read per loop iteration — ~10 ms at 20 kHz, giving the GUI a
    ~100 Hz callback rate
* - {py:data}`~config.DISPLAY_SECONDS`
  - `5`
  - Width of the live rolling display
* - {py:data}`~config.DISPLAY_SAMPLES`
  - derived
  - `SAMPLE_RATE × DISPLAY_SECONDS` — ring buffer size
:::

## Channels

{py:data}`~config.AI_CHANNELS` and {py:data}`~config.AI_CHANNELS_VC` hold the
per-mode channel definitions as `ChannelDef` tuples:

```python
(display_name, ni_channel, terminal_config, display_scale, units)
```

The order of entries determines the row order of every data array in the
codebase, and {py:data}`~config.N_AI_CHANNELS` is derived from it.

Full mapping and scaling: {doc}`../guide/clamp-modes`.

:::{warning}
Changing the order or count of channels changes the on-disk row order of
`analog_input`. Recordings made before the change will not match recordings
made after, and nothing in the file format signals the difference beyond
`channel_names`. Always index by name.
:::

## Command output

:::{list-table}
:header-rows: 1
:widths: 30 12 58

* - Constant
  - Default
  - Meaning
* - {py:data}`~config.AO_COMMAND_CH`
  - `"ao0"`
  - Analog output driving the amplifier command input
* - {py:data}`~config.AO_PA_PER_VOLT`
  - `400.0`
  - Current-clamp command sensitivity, pA per volt
* - {py:data}`~config.AO_MV_PER_VOLT`
  - `20.0`
  - Voltage-clamp command sensitivity, mV per volt
:::

## Camera trigger

:::{list-table}
:header-rows: 1
:widths: 30 12 58

* - Constant
  - Default
  - Meaning
* - {py:data}`~config.CTR_CHANNEL`
  - `"ctr0"`
  - Counter generating the TTL pulse train
* - {py:data}`~config.CTR_OUT_TERMINAL`
  - `"PFI12"`
  - Physical output terminal — wire to the camera trigger input
* - {py:data}`~config.TTL_HIGH_V`
  - `5.0`
  - TTL logic high
* - {py:data}`~config.TTL_LOW_V`
  - `0.0`
  - TTL logic low
* - {py:data}`~config.DEFAULT_FRAME_RATE_HZ`
  - `100.0`
  - Camera frame rate — divides evenly into 20 kHz, so no rounding error
* - {py:data}`~config.DEFAULT_EXPOSURE_MS`
  - `5.0`
  - Exposure; must be shorter than the TTL period
* - {py:data}`~config.CAMERA_GUARD_DELAY_MS`
  - `2000`
  - How long the HDF5 stays open after TTL stops, to catch the trailing
    exposure return on `ai3`
:::

See {doc}`../getting-started/hardware-setup` for wiring and the explicit
`co_pulse_term` routing requirement.

## Display

{py:data}`~config.AI_Y_DEFAULTS` and {py:data}`~config.AI_Y_DEFAULTS_VC` give
default Y-axis ranges per channel per clamp mode;
{py:data}`~config.TRACE_COLORS` sets the trace palette. These affect display
only — never the saved data.

## Changing a value

Edit `config.py` and restart. The Setup page shows the same values and can
change them for the running session, but `config.py` is what persists.

:::{important}
Scaling constants describe **your amplifier's** sensitivity. If you change
amplifier gain settings, update {py:data}`~config.AO_PA_PER_VOLT` /
{py:data}`~config.AO_MV_PER_VOLT` and the per-channel `display_scale` values to
match — then run the {doc}`../qc/alignment-check` to confirm the numbers are
real. The alignment check exists precisely to catch a mismatch between what
`config.py` claims and what the hardware does.
:::

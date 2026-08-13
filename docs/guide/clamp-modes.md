# Clamp modes

The rig runs in **current clamp (CC)** or **voltage clamp (VC)**. Switching
between them relabels every analog input channel and swaps its scaling factor,
so the traces, the axis units, and the saved metadata all follow the mode
automatically.

Switch modes with the clamp pill in the top chrome bar. It is visible in
continuous mode only.

## Channel mapping

The same five physical inputs mean different things in each mode:

:::{list-table}
:header-rows: 1
:widths: 10 26 26 38

* - Port
  - Current clamp
  - Voltage clamp
  - Physical meaning
* - `ai0`
  - `ScAmpOut` — ×10.0 → mV
  - `I_mem` — ×100.0 → pA
  - Scaled amplifier output
* - `ai1`
  - `RawAmpOut` — ×2.0 → nA
  - `V_pip` — ×1000.0 → mV
  - Raw monitor
* - `ai2`
  - `AmpCmd` — ×400.0 → pA
  - `AmpCmd` — ×20.0 → mV
  - Amplifier command loopback
* - `ai3`
  - `Camera` — ×1.0 → V
  - `Camera` — ×1.0 → V
  - Camera TTL sync pulse
* - `ai4`
  - `TTLLoopback` — ×1.0 → V
  - `TTLLoopback` — ×1.0 → V
  - DAQ TTL output loopback
:::

These live in {py:data}`config.AI_CHANNELS` and
{py:data}`config.AI_CHANNELS_VC` as `ChannelDef` tuples of
`(display_name, ni_channel, terminal_config, display_scale, units)`.

:::{important}
Raw volts are what gets **saved**. The `display_scale` column converts raw DAQ
volts to display units at draw time and during analysis — it is stored in the
HDF5 metadata alongside the data, never baked into the samples themselves.
:::

## Command scaling

The amplifier's command input converts a DAQ voltage on `ao0` into either an
injected current or a holding-potential offset:

::::{tab-set}

:::{tab-item} Current clamp
Sensitivity: **400 pA per volt** ({py:data}`config.AO_PA_PER_VOLT`).

To inject *X* pA, write *X* / 400 V to `ao0`.

```text
 200 pA  →   0.5 V
-500 pA  →  -1.25 V
```
:::

:::{tab-item} Voltage clamp
Sensitivity: **20 mV per volt** ({py:data}`config.AO_MV_PER_VOLT`).

To step the holding potential by *X* mV, write *X* / 20 V to `ao0`.

```text
 -40 mV  →  -2.0 V
 +10 mV  →   0.5 V
```
:::

::::

The stimulus panel and protocol builder do this conversion for you — their
labels switch between pA and mV with the mode, and you enter values in physical
units throughout.

## What the mode affects

The clamp mode is not just a display setting. Changing it changes:

Channel names and units
: Trace legends, Y-axis labels, and the channel table on the Channels page.

Saved metadata
: `channel_names`, `display_scales`, and `units` in `/metadata/` reflect the
  mode that was active during the recording.

Stimulus units
: The stimulus panel and protocol builder switch between pA and mV.

Waveform construction
: {py:mod}`acquisition.trial_waveforms` builds the `ao0` waveform using the
  matching sensitivity constant.

QC thresholds
: Signal checks in {py:mod}`analysis.qc.signal` apply per-channel scaling
  before thresholding, so limits stay in physical units.

:::{warning}
Set the clamp mode to match the amplifier **before** recording. The mode is
captured in the file's metadata at save time, and a recording saved with the
wrong mode carries wrong scale factors and wrong units — the raw volts survive,
but every downstream analysis reads them in the wrong physical units.
:::

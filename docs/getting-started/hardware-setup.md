# Hardware setup

Everything on this page is defined in {py:mod}`config`. If your rig differs,
that module is the only place you need to change.

## Signal chain

```text
  amplifier ──── ai0..ai2 ──┐
                            ├──> NI PCIe-6323 ──> acquisition (20 kHz)
  camera TTL ─── ai3 ───────┤
  TTL loopback ─ ai4 ───────┘

  NI PCIe-6323 ──ao0──> amplifier command input
               └─ctr0──> PFI12 ──> camera external trigger
```

## Analog input

Five differential/RSE inputs sampled at {py:data}`config.SAMPLE_RATE` (20 kHz,
50 µs resolution), read {py:data}`config.CHUNK_SIZE` samples at a time
(200 samples ≈ 10 ms, giving the GUI a ~100 Hz update rate).

:::{list-table}
:header-rows: 1
:widths: 12 20 20 48

* - Port
  - Terminal config
  - Carries
  - Notes
* - `ai0`
  - differential
  - Scaled amplifier output
  - Membrane potential (CC) or membrane current (VC)
* - `ai1`
  - differential
  - Raw amplifier monitor
  - Current monitor (CC) or pipette voltage (VC)
* - `ai2`
  - differential
  - Amplifier command loopback
  - Records what the amplifier was actually commanded to do
* - `ai3`
  - **RSE**
  - Camera TTL / exposure return
  - Used for TTL ↔ video frame-count QC
* - `ai4`
  - differential
  - DAQ TTL output loopback
  - Used to derive frame timestamps during video alignment
:::

Scaling differs by clamp mode — see {doc}`../guide/clamp-modes`.

:::{important}
`ai2` and `ai4` are loopbacks: they record what the DAQ *sent*, not what the
cell did. They exist so QC can verify commanded-vs-recorded fidelity and so
video frames can be timestamped against the same clock as the ephys data. Do
not repurpose them.
:::

## Analog output

{py:data}`config.AO_COMMAND_CH` (`ao0`) drives the amplifier command input.
Sensitivity depends on clamp mode:

- Current clamp — {py:data}`config.AO_PA_PER_VOLT` = 400 pA/V
- Voltage clamp — {py:data}`config.AO_MV_PER_VOLT` = 20 mV/V

## Camera trigger

The camera is triggered by a hardware counter, not by software timing.

:::{list-table}
:header-rows: 1
:widths: 40 60

* - Setting
  - Value
* - {py:data}`config.CTR_CHANNEL`
  - `ctr0`
* - {py:data}`config.CTR_OUT_TERMINAL`
  - `PFI12` — wire this to the camera's external trigger input
* - {py:data}`config.TTL_HIGH_V` / {py:data}`config.TTL_LOW_V`
  - 5.0 V / 0.0 V
* - {py:data}`config.DEFAULT_FRAME_RATE_HZ`
  - 100.0 Hz
* - {py:data}`config.DEFAULT_EXPOSURE_MS`
  - 5.0 ms
:::

:::{warning}
The counter output terminal is set **explicitly** in
{py:mod}`hardware.daq_config` via `co_pulse_term`. Do not rely on the DAQ's
default terminal routing — it is not dependable across driver versions and
reinstalls, and the failure mode is silent: the counter runs, the camera never
triggers, and nothing in the software reports an error.
:::

### Why 100 Hz

100 Hz divides evenly into the 20 kHz sample rate — one TTL period is exactly
200 samples, so there is no integer rounding error. Other frame rates are
supported but get rounded to the nearest whole number of samples per period;
{py:func}`utils.stimulus_generator.get_actual_frame_rate` returns the rate you
will actually get after rounding.

### Exposure

Exposure must be shorter than the TTL period (1000 / frame_rate ms), or
consecutive trigger pulses overlap. At the 100 Hz default the period is 10 ms
and the default 5 ms exposure leaves comfortable headroom.

## Model cell

The weekly alignment check expects an Axon Instruments Patch-1U model cell in
**CELL mode at 500 MΩ**, patched in place of a pipette. See
{doc}`../qc/alignment-check`.

## Verifying the rig

```bash
uv run python -c "import config; print(config.DEVICE_NAME, config.SAMPLE_RATE)"
```

If the device name printed here does not match what NI MAX shows, update
{py:data}`config.DEVICE_NAME`. For a full electrical check of loopback latency,
crosstalk, TTL stability, and clamp scaling, run the alignment check.

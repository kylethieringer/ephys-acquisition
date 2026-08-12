# Installation

## Requirements

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Hardware
- NI PCIe-6323 (or a compatible NI DAQ)
- Basler Pylon camera
- Patch-clamp amplifier with command input
:::

:::{grid-item-card} Software
- **Python 3.14** or newer
- Windows
- NI-DAQmx driver
- Basler Pylon runtime
:::

::::

:::{warning}
The NI-DAQmx driver and the Basler Pylon runtime are separate installers from
National Instruments and Basler. `nidaqmx` and `pypylon` are only Python
bindings — they install fine without the drivers present, but the application
will not find hardware until the drivers are installed.
:::

## Install

The project is managed with [uv](https://docs.astral.sh/uv/). It reads
`pyproject.toml` and `uv.lock` and creates the environment for you:

```bash
git clone git@github.com:kylethieringer/ephys-acquisition.git
cd ephys-acquisition
uv sync
```

That installs the locked dependency set into `.venv`. Verify it worked:

```bash
uv run python -c "import config; print(config.DEVICE_NAME, config.SAMPLE_RATE)"
```

Expected output — the device name as it appears in NI MAX, and the sample rate:

```text
Dev1 20000
```

## Run

```bash
uv run python main.py
```

The project also installs a console script, so this works too:

```bash
uv run ephys-gui
```

## Configure the rig

All hardware constants live in {py:mod}`config` — channel names, scaling
factors, sample rate, and TTL parameters. There is no separate settings file
and no GUI for these; `config.py` is the single authoritative source.

The values you are most likely to change:

:::{list-table}
:header-rows: 1
:widths: 26 14 60

* - Constant
  - Default
  - Meaning
* - {py:data}`config.DEVICE_NAME`
  - `"Dev1"`
  - NI DAQ device name exactly as shown in NI MAX
* - {py:data}`config.SAMPLE_RATE`
  - `20000`
  - AI/AO sample rate in Hz — 50 µs resolution
* - {py:data}`config.CHUNK_SIZE`
  - `200`
  - Samples read per loop iteration (~10 ms at 20 kHz)
* - {py:data}`config.DISPLAY_SECONDS`
  - `5`
  - Width of the live rolling display window
:::

:::{tip}
If the application reports *"Device not found"*, open NI MAX and check the
device name there against {py:data}`config.DEVICE_NAME`. A fresh NI install
often enumerates the card as `Dev1`, but a second card or a reinstall can
shift it to `Dev2`.
:::


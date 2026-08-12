# Files on disk

Every recording produces a small family of files that share a stem. They live
together in a per-experiment folder under the save directory.

## Naming

```text
{save_dir}/{expt_id}/{expt_id}_{genotype}_{YYYYMMDD}.h5
```

The experiment ID comes from the subject card; it names both the folder and the
file prefix. Genotype defaults to `unknown` if left blank, and the experiment ID
defaults to `ephys`.

## What gets written

```text
D:/data/KT001/
├── KT001_CS_20260812.h5                 recording
├── KT001_CS_20260812.bin                raw float64 backup
├── KT001_CS_20260812_metadata.json      sidecar
├── KT001_CS_20260812.avi                video
├── KT001_CS_20260812_qc_report.html     QC report
├── KT001_CS_20260812_qc_report.json     QC data, machine-readable
└── KT001_CS_20260812_acquisition.log    only if buffer-fill events occurred
```

:::{list-table}
:header-rows: 1
:widths: 22 12 66

* - File
  - Always?
  - Purpose
* - `.h5`
  - yes
  - The recording. See {doc}`hdf5-format`.
* - `.bin`
  - yes
  - Raw float64 data written during acquisition, kept as a backup.
* - `_metadata.json`
  - yes
  - Sidecar of acquisition settings, independent of the HDF5.
* - `.avi`
  - yes
  - Video. Trial mode writes one per trial.
* - `_qc_report.html` / `.json`
  - yes
  - Written by the automatic QC pass. See {doc}`../qc/post-recording`.
* - `_acquisition.log`
  - **no**
  - Only written if the DAQ buffer crossed its fill threshold during
    recording. Its presence is itself a signal worth reading.
:::

## Why binary first

Data is appended to the `.bin` file during acquisition because a flat write is
the cheapest thing that can happen on the acquisition path. HDF5 conversion
runs afterwards in a background thread, so it never competes with sampling.

**The `.bin` is never deleted.** If conversion fails or the application dies
mid-run, the raw data is still on disk:

```python
import numpy as np
import config

raw = np.fromfile("KT001_CS_20260812.bin", dtype=np.float64)
data = (raw.reshape(-1, config.N_AI_CHANNELS, config.CHUNK_SIZE)
           .transpose(1, 0, 2)
           .reshape(config.N_AI_CHANNELS, -1))
```

Trial mode's binary layout differs — consecutive per-trial blocks rather than
fixed-size chunks:

```python
raw = np.frombuffer(raw_bytes, dtype=np.float64)
data = raw.reshape(n_channels, n_samples)   # one trial block
```

The trial index table that maps byte offsets to trials is held in memory during
the run and written into the HDF5 at close, so recovering individual trials from
a `.bin` alone requires knowing the per-trial sample counts.

:::{warning}
The `.bin` is raw **volts**, with no scaling applied. Multiply by the
per-channel `display_scale` from the metadata to get physical units — see
{doc}`../guide/clamp-modes`.
:::

## The sidecar

`_metadata.json` duplicates the acquisition settings outside the HDF5, so
tooling can read them without opening the recording. It is written when
recording starts and finalised when it stops:

```json
{
  "subject": { "expt_id": "KT001", "genotype": "CS", "age": "5", "sex": "F" },
  "start_time": "2026-08-12T14:02:11.481200",
  "end_time": "2026-08-12T14:19:40.002913",
  "duration_samples": 20980000,
  "duration_seconds": 1049.0,
  "clamp_mode": "current_clamp",
  "sample_rate_hz": 20000,
  "channels": [
    { "name": "ScAmpOut", "ni_channel": "ai0", "terminal_config": "differential",
      "display_scale": 10.0, "units": "mV" }
  ],
  "camera": { "frame_rate_hz": 100.0, "exposure_ms": 5.0 },
  "files": { "ephys_h5": "...h5", "ephys_bin": "...bin", "video": "...avi" },
  "protocols": []
}
```

`end_time`, `duration_samples`, and `duration_seconds` are `null` until the
recording closes.

:::{note}
A sidecar whose `end_time` is still `null` means that recording never closed
cleanly. The `.bin` will still hold the data.
:::

QC cross-checks the sidecar against the HDF5 — a disagreement between them is a
reported failure, not a silent inconsistency.

The sidecars are also what
{doc}`../analysis/experiment-log` scans to build the experiment log CSV.

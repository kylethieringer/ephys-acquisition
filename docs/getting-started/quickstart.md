# Quickstart

This walks through a single continuous recording, from launching the
application to the QC report appearing next to your data.

## 1. Launch

```bash
uv run python main.py
```

The main window opens on the **Acquire** page with live traces (empty until you
start acquisition) and the camera preview.

## 2. Set the subject metadata

Fill in the subject card on the right of the Acquire page — experiment ID,
genotype, age, sex. The experiment ID becomes the folder name and the file-name
prefix for everything this session writes, so set it before recording.

:::{tip}
The experiment ID also shows in the session label in the top chrome bar, which
is the quickest way to confirm you are not about to overwrite yesterday's
session.
:::

## 3. Choose mode and clamp

- **Mode pill** (top chrome bar): `Continuous` or `Trial`
- **Clamp pill**: `CC` or `VC` — continuous mode only

For a first recording, use `Continuous` + `CC`. See {doc}`../guide/clamp-modes`
for what the clamp setting changes.

## 4. Start acquisition

Click **Start** in the recording bar at the bottom.

At this point:

- The DAQ begins sampling all analog input channels at 20 kHz — traces go live
- The camera opens in hardware-triggered mode
- **No TTL is firing yet**, so no frames arrive and nothing is saved

This is the state you patch in. Traces are live, nothing is being written.

## 5. Record

Click **● Record**.

- The TTL pulse train starts on `ctr0` → `PFI12`, triggering the camera
- The HDF5 file and the video file open
- Data is appended to a flat `.bin` file as it arrives

## 6. Stop recording

Click **Stop Recording**.

The TTL stops immediately, but the HDF5 file stays open for a further
{py:data}`config.CAMERA_GUARD_DELAY_MS` milliseconds (2 s by default). This
guard window captures the trailing exposure-return signal from the last
triggered frame before the file closes.

The camera stays open and armed, so you can record again without restarting
acquisition.

## 7. Stop acquisition

Click **Stop** when you're finished with the cell. The camera closes and the
DAQ shuts down.

## What you end up with

```text
D:/data/KT001/
├── KT001_CS_20260812.h5              recording
├── KT001_CS_20260812.bin             raw float64 backup, always kept
├── KT001_CS_20260812_metadata.json   sidecar
├── KT001_CS_20260812.avi             video
├── KT001_CS_20260812_qc_report.html  QC report — open this
└── KT001_CS_20260812_qc_report.json  same data, machine-readable
```

The `.h5` is written in a background thread by converting the `.bin`, so the
GUI stays responsive. QC then runs automatically on the finished file — see
{doc}`../qc/post-recording`.

:::{note}
The `.bin` file is never deleted. If HDF5 conversion ever fails, the raw data is
still on disk and recoverable with `np.fromfile` — see
{doc}`../data/file-layout`.
:::

## Next steps

- Run a stimulus protocol instead of a bare recording → {doc}`../guide/protocols`
- Understand what the QC report is telling you → {doc}`../qc/interpreting-reports`
- Load the recording in Python → {doc}`../data/loading-data`

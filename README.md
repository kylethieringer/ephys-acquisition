# Ephys Acquisition

A real-time electrophysiology data acquisition system with integrated camera triggering, live visualization, and protocol-driven stimulation. Built with PySide6 and NI DAQ hardware (NI PCIe-6323).

## Features

- **Real-Time Data Acquisition**: Continuous analog input sampling via NI DAQ at 20 kHz
- **Live Visualization**: 5-second rolling window display of all analog input channels
- **Camera Integration**: Basler Pylon camera with TTL triggering and exposure control
- **Current Clamp & Voltage Clamp Modes**: Switch between CC and VC with automatic channel relabelling and scaling
- **Protocol Builder**: Design staircase (CC) and voltage-step (VC) stimulus protocols with a GUI editor
- **Protocol Dropdown**: Load saved protocols from `D:/protocols` directly from the main window
- **Continuous Protocol Mode**: Run a stimulus protocol within a single unbroken recording; stimulus timing is saved as sample-accurate events in the HDF5 file for post-processing
- **Trial-Based Mode**: Per-trial HDF5 recording with pre-allocated datasets for fast sequential reads
- **Binary-First Save**: Raw data is written to a `.bin` file during acquisition for minimal overhead; converted to HDF5 in a background thread when recording stops. The `.bin` file is always preserved as a backup
- **Automatic Post-Recording QC**: Every saved recording triggers a background QC pass that emits a self-contained HTML report (`*_qc_report.html`) with sample-count consistency, finite-value check, stimulus-event integrity, TTL ↔ video frame-count drift, an interactive multi-channel overview, and a per-stimulus commanded-vs-recorded overlay (trial mode)
- **Standalone Hardware Alignment Check**: `python -m analysis.qc_alignment` drives the rig directly with the Axon Patch-1U model cell to verify AO→AI loopback latency, channel crosstalk, TTL clock stability, CC/VC scaling and linearity, noise floor, and the analysis pipeline. Each run appends to `qc_alignment_history.csv` for long-term drift tracking
- **Buffer-Fill Instrumentation**: The DAQ worker watches `avail_samp_per_chan` after every chunk read; near-overflow events are drained at recording stop into `*_acquisition.log` and surfaced as warnings in the QC report
- **Dark UI**: Easy-on-the-eyes Qt interface optimised for lab environments

## System Requirements

**Hardware**
- NI PCIe-6323 (or compatible NI DAQ)
- Basler Pylon camera

**Software**
- Python 3.10+
- Windows

## Installation

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Hardware is configured in `config.py` — channel names, scaling factors, sample rate, and TTL parameters are all defined there.

## Usage

```bash
python main.py
```

### Main Window Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  TopChromeBar: [Session]  [Continuous|Trial]  [CC|VC]  [Status]      │
├──────┬───────────────────────────────────────────────────────────────┤
│  S   │                                                               │
│  i   │                    Page content (stacked)                     │
│  d   │                                                               │
│  e   ├───────────────────────────────────────────────────────────────┤
│  b   │  Recording bar: [Start] [Stop] [● Record] [Stop Recording]    │
│  a   │                                                               │
│  r   │                                                               │
└──────┴───────────────────────────────────────────────────────────────┘
```

The sidebar has four tabs: **Acquire**, **Protocol**, **Channels**, **Setup**. The recording bar and TopChromeBar are always visible.

**Acquire page**
- Left (65%): Live rolling traces for all analog input channels
- Right (35%):
  - Camera preview (fixed, 300 px)
  - Subject card (experiment ID, genotype, age, sex, cell type)
  - Protocol widget — dropdown to select a `.json` from `D:/protocols`, **Run Protocol** / **Stop Protocol** buttons
  - Stimulus panel — ad-hoc staircase stimulus (continuous mode only); labels switch between pA (CC) and mV (VC) automatically

**Protocol page**
- Left: Saved-protocol list with filter box and Refresh / New buttons
- Right: Inline Protocol Builder editor

**Channels page**
- Per-channel table: color swatch, port, signal name/units, Y-min/Y-max spinboxes, Auto-range toggle, Save checkbox

**Setup page** (2×2 card grid)
- DAQ Device: device name, sample rate, chunk size, counter, AO command channel
- Channel Mapping: AI/AO/CTR port → signal → scale → units table
- Data Save Location: save directory picker
- Camera: TTL frame rate and exposure settings

**TopChromeBar**
- Session label (experiment ID)
- Mode pill — toggle Continuous / Trial-based
- Clamp pill — toggle Current clamp / Voltage clamp (visible in continuous mode only)
- Status badge — live acquisition state

### Continuous Protocol Mode

1. Select **Continuous** mode and set the clamp mode
2. Load a protocol from the dropdown (or build one with the builder)
3. Click **Run Protocol**
   - Recording starts automatically
   - Stimulus waveforms are applied at the correct sample offsets
   - Each stimulus onset and offset is logged to `/stimulus_events/` in the HDF5 file
   - Recording stops automatically when the protocol finishes

The resulting file can be sliced into pseudo-trials in post-processing using the `sample_index` values in `/stimulus_events/`.

### Trial-Based Mode

1. Select **Trial-based** mode
2. Load or build a protocol
3. Click **Run Protocol**
   - Each trial is saved as a separate group (`/trial_001/`, `/trial_002/`, …)
   - Per-trial video files are recorded alongside the HDF5

### Clamp Modes

| Channel | CC scale | VC scale |
|---------|----------|----------|
| ai0 (amp out) | 10.0 mV/V → mV | 100.0 pA/V → pA |
| ai1 (raw out) | 2.0 nA/V → nA | 1000.0 mV/V → mV |
| ai2 (AmpCmd) | 400.0 pA/V → pA | 20.0 mV/V → mV |
| ai3 (Camera TTL) | 1.0 V/V | 1.0 V/V |
| ai4 (TTL loopback) | 1.0 V/V | 1.0 V/V |

## Quality Control

### Post-recording QC (automatic)

When a recording finishes saving, [`analysis.qc.hook.schedule_qc`](analysis/qc/hook.py) spawns a daemon thread that runs the full QC pipeline against the freshly-written HDF5. The GUI thread is never blocked.

Outputs land next to the recording:

| File | Contents |
|------|----------|
| `<stem>_qc_report.html` | Self-contained report — status badges per check, JSON-formatted metrics, interactive plotly overview of every channel (min/max-decimated above 600 k samples per channel so spikes stay visible at all zoom levels), and (trial mode) commanded-vs-recorded `AmpCmd` overlays |
| `<stem>_qc_report.json` | Same data, machine-readable |
| `<stem>_acquisition.log` | Buffer-fill warnings drained from the DAQ worker (only written if events occurred) |

Checks are grouped into three sections (see [`analysis/qc/`](analysis/qc/)):

- **Acquisition integrity** — sample-count consistency across HDF5/bin/sidecar, HDF5↔sidecar metadata agreement, finite values, stimulus event table (continuous) or trial table (trial), camera TTL ↔ video frame-count drift, acquisition-log severity
- **Signal sanity** — per-channel range/RMS sanity ([`analysis/qc/signal.py`](analysis/qc/signal.py))
- **Commanded vs recorded** — protocol playback fidelity ([`analysis/qc/stimulus.py`](analysis/qc/stimulus.py))

Every check returns one of `pass / warn / fail / skip`; checks catch their own exceptions and downgrade to `fail` so the report always renders.

### Hardware alignment check (manual, weekly)

Run with the Axon Instruments Patch-1U model cell (CELL mode, 500 MΩ) patched in place of a real pipette:

```bash
python -m analysis.qc_alignment --save-dir D:/data
# Phase A only (no model cell required):
python -m analysis.qc_alignment --save-dir D:/data --no-phase-b
```

Output goes under `D:/data/_alignment_checks/`:

- `qc_alignment_<timestamp>.html` / `.json` — per-run report
- `qc_alignment_history.csv` — one row per run with key numeric metrics (loopback lag, observed TTL rate, fitted CC slope MΩ, R², τ, computed Ri…) for long-term drift plotting

**Phase A — rig only** (no model cell): AO → AmpCmd loopback latency, inter-channel crosstalk, counter-TTL period stability.

**Phase B — model cell**: resting baseline, noise floor (incl. 60/120/180 Hz line fraction), CC scaling and linearity (ΔV = I·R staircase), VC scaling, capacitance τ, and an end-to-end self-test that feeds a fresh CC recording through `analysis.analyze_steps.compute_input_resistance`.

## Project Structure

```
ephys_acquisition/
├── main.py                               # Application entry point
├── config.py                             # Hardware constants and channel definitions
├── requirements.txt
│
├── ui/
│   ├── main_window.py                   # Top-level Qt window
│   ├── control_panel.py                 # Mode selector, protocol dropdown, recording bar
│   ├── camera_panel.py                  # Camera preview and TTL settings
│   ├── stimulus_panel.py                # Ad-hoc staircase stimulus (continuous mode)
│   ├── trace_panel.py                   # Rolling trace display and Y-range controls
│   ├── protocol_builder.py              # Protocol editor dialog
│   └── __init__.py
│
├── hardware/
│   ├── daq_worker.py                    # NI DAQ AI/AO/CTR worker (QThread)
│   ├── daq_config.py                    # DAQ task configuration helpers
│   ├── camera_worker.py                 # Basler camera worker (QThread)
│   ├── camera_config.py                 # Camera settings
│   └── __init__.py
│
├── acquisition/
│   ├── continuous_mode.py               # Continuous acquisition controller
│   ├── continuous_protocol_runner.py    # Flat event timeline for continuous protocols
│   ├── trial_mode.py                    # Trial-based acquisition state machine
│   ├── trial_protocol.py               # TrialProtocol dataclasses + JSON serialization
│   ├── trial_waveforms.py              # AO waveform builders (CC and VC)
│   ├── data_buffer.py                   # Ring buffer for live display
│   ├── data_saver.py                    # ContinuousSaver (binary → HDF5)
│   ├── trial_saver.py                   # TrialSaver (binary → per-trial HDF5)
│   └── __init__.py
│
├── analysis/
│   ├── analyze_steps.py                 # Input-resistance / step analysis
│   ├── align_video.py                   # Align video frames to TTL edges
│   ├── qc_alignment.py                  # CLI for the standalone hardware alignment check
│   ├── qc_report.py                     # CLI for re-running post-recording QC on an existing file
│   └── qc/
│       ├── alignment.py                 # Phase A/B alignment checks (Axon model cell)
│       ├── descriptions.py              # Human-readable text for the HTML report
│       ├── hook.py                      # Fire-and-forget QC runner + acquisition-log writer
│       ├── integrity.py                 # Sample-count, finite-values, TTL↔video, …
│       ├── load.py                      # Recording loader (HDF5 + sidecar + video + log)
│       ├── report.py                    # Orchestrator + Plotly/matplotlib plot builders
│       ├── signal.py                    # Per-channel signal-sanity checks
│       ├── stimulus.py                  # Commanded-vs-recorded checks
│       └── templates/                   # Jinja2 HTML report templates
│
└── utils/
    ├── stimulus_generator.py            # Waveform generation utilities
    ├── data_loader.py                   # Load saved HDF5 files
    └── __init__.py
```

## Data Format

### Continuous Recording (`.h5`)

```
/metadata/
    sample_rate      int
    start_time       ISO-8601 string
    channel_names    string array
    display_scales   float64 array
    units            string array
/subject/            (attributes: expt_id, genotype, age, sex, targeted_cell_type)
/data/
    analog_input     float64 (N_channels × N_samples), LZF compressed
/stimulus_events/    (present when a protocol was run in continuous mode)
    sample_index     int64
    event_type       string  ("apply" or "clear")
    stimulus_name    string
    stimulus_index   int32
```

A companion `.bin` file (raw float64 data) and `metadata.json` sidecar are always written alongside the `.h5`. After save, QC adds `*_qc_report.html` + `*_qc_report.json`, and (only if the DAQ worker recorded buffer-fill events) `*_acquisition.log`.

### Trial Recording (`_trials.h5`)

```
/metadata/
    protocol         full JSON protocol definition
    trial_order      int32 array
    ...
/subject/
/trial_001/
    analog_input     float64 (N_channels × N_samples)
    attrs: stimulus_name, stimulus_index, trial_index, onset_time, video_file
/trial_002/ ...
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Device not found" | Check device name in NI MAX; update `DEVICE_NAME` in `config.py` |
| Camera not triggering | Verify TTL levels (`TTL_HIGH_V`, `TTL_LOW_V`) and PFI12 wiring |
| HDF5 conversion failed | Raw `.bin` file preserved — re-run conversion manually using `np.fromfile` |
| Protocol not in dropdown | Place `.json` protocol files in `D:/protocols`; click **↻** to refresh |
| `*_acquisition.log` written | DAQ driver buffer crossed 70 % of capacity at least once; see line(s) for the offending sample index. A few isolated events are tolerable; sustained warnings indicate the GUI/save thread is starving the AI reader |
| QC report shows TTL ↔ video drift | Check camera frame rate matches `TTL_FRAME_RATE_HZ`; verify the trigger cable from PFI12 → camera Line1 |
| `analysis.qc_alignment` Phase B failing | Verify the Patch-1U is in CELL mode (not HEAD), 500 MΩ; check amplifier gain settings haven't drifted |

## Dependencies

- **PySide6** — Qt UI framework
- **pyqtgraph** — Real-time data visualization
- **nidaqmx** — NI DAQ hardware interface
- **pypylon** — Basler camera SDK
- **numpy** — Numerical computing
- **h5py** — HDF5 file I/O
- **scipy** — Signal processing utilities
- **opencv-python** — Video recording (and reading frame counts during QC)
- **plotly** — Interactive multi-channel overview in QC reports
- **matplotlib** — Static plots in QC reports
- **jinja2** — HTML report templating

## Contact

Kyle Thieringer

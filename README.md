# Ephys Acquisition

A real-time electrophysiology data acquisition system with integrated camera triggering, live visualization, and protocol-driven stimulation. Built with PySide6 and NI DAQ hardware (NI PCIe-6323).

## Features

- **Real-Time Data Acquisition**: Continuous analog input sampling via NI DAQ at 20 kHz
- **Live Visualization**: 5-second rolling window display of all analog input channels
- **Camera Integration**: Basler Pylon camera with TTL triggering and exposure control
- **Current Clamp & Voltage Clamp Modes**: Switch between CC and VC with automatic channel relabelling and scaling
- **Protocol Builder**: Design staircase (CC) and voltage-step (VC) stimulus protocols with a GUI editor
- **Protocol Dropdown**: Load saved protocols from `E:/protocols` directly from the main window
- **Continuous Protocol Mode**: Run a stimulus protocol within a single unbroken recording; stimulus timing is saved as sample-accurate events in the HDF5 file for post-processing
- **Trial-Based Mode**: Per-trial HDF5 recording with pre-allocated datasets for fast sequential reads
- **Binary-First Save**: Raw data is written to a `.bin` file during acquisition for minimal overhead; converted to HDF5 in a background thread when recording stops. The `.bin` file is always preserved as a backup
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
┌────────────────────────────────────────────────────────────────┐
│  Live Traces (left, 65%)          │  Controls (right, 35%)     │
│                                   │  ┌──────────────────────┐  │
│  5 rolling AI traces at 20 kHz    │  │  Acquisition Tab     │  │
│                                   │  │  Experiment Tab      │  │
│                                   │  └──────────────────────┘  │
├───────────────────────────────────────────────────────────────-┤
│  Bottom bar:  [Start] [Stop] [Record] [Stop Recording] Status  │
└────────────────────────────────────────────────────────────────┘
```

**Acquisition Tab**
- Acquisition mode (Continuous / Trial-based)
- Clamp mode (Current clamp / Voltage clamp)
- Protocol dropdown — select a `.json` from `E:/protocols`; click **↻** to refresh
- Open Protocol Builder button — design new protocols
- **Run Protocol** button — starts the protocol in whichever mode is active
- Save directory and subject metadata
- Camera TTL settings
- Channel visibility toggles
- Per-channel Y-range controls

**Experiment Tab**
- Camera preview
- Stimulus panel (ad-hoc staircase stimulus for continuous mode)
  - Labels and ranges switch between pA (CC) and mV (VC) automatically

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

A companion `.bin` file (raw float64 data) and `metadata.json` sidecar are always written alongside the `.h5`.

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
| Protocol not in dropdown | Place `.json` protocol files in `E:/protocols`; click **↻** to refresh |

## Dependencies

- **PySide6** — Qt UI framework
- **pyqtgraph** — Real-time data visualization
- **nidaqmx** — NI DAQ hardware interface
- **pypylon** — Basler camera SDK
- **numpy** — Numerical computing
- **h5py** — HDF5 file I/O
- **opencv-python** (optional) — Video recording

## Contact

Kyle Thieringer

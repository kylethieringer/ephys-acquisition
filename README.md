# Ephys Acquisition

A real-time electrophysiology data acquisition system with integrated camera triggering and live visualization. Built with PySide6 and NI DAQ hardware.

## Features

- **Real-Time Data Acquisition**: Continuous analog input sampling via NI DAQ at 20 kHz
- **Live Visualization**: 5-second rolling window display of all analog input channels
- **Camera Integration**: Basler Pylon camera with TTL triggering and exposure control
- **Data Recording**: HDF5-based data storage with metadata
- **TTL Synchronization**: Configurable TTL pulses for camera triggering and external stimulus control
- **Multi-Channel Support**: 5 analog input channels with configurable scaling and units
- **Dark UI**: Easy-on-the-eyes Qt interface optimized for lab environments

## System Requirements

- **Hardware**:
  - NI DAQ device (e.g., NI USB-6001)
  - Basler camera (or other Pylon SDK compatible camera)
  - Analog input signals for electrophysiology recordings

- **Software**:
  - Python 3.8+
  - Windows or Linux

## Installation

1. Clone or download the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure your hardware in `config.py`:
   - Set `DEVICE_NAME` to your NI DAQ device name (check NI MAX)
   - Adjust `SAMPLE_RATE` and `CHUNK_SIZE` as needed (default: 20 kHz, 200 samples)
   - Configure `AI_CHANNELS` with your channel mappings and scaling factors

## Usage

Run the application:
```bash
python main.py
```

### Main Window

The interface is split into two panels:

**Left Panel** — Live Trace Display
- Real-time plots of all 5 analog input channels
- Configurable Y-axis ranges per channel
- Scrolling window showing the last 5 seconds of data

**Right Panel** — Controls
- **Acquisition Tab**: 
  - Start/Stop data acquisition
  - Select continuous acquisition mode
  - Record/Stop recording to HDF5
  - Configure TTL delay before/after recording

- **Experiment Tab**:
  - Camera preview and controls
  - Frame rate and exposure settings
  - Y-axis range adjustment for each channel
  - Channel visibility toggles

### Channel Configuration

Channels are defined in `config.py`:

```python
AI_CHANNELS = [
    ("ScAmpOut",    "ai0", "differential", 10.0,  "mV"),     # Scaled amp output
    ("RawAmpOut",   "ai1", "differential",  2.0,  "nA"),     # Raw amp output
    ("AmpCmd",      "ai2", "differential", 400.0, "pA"),     # Amp command signal
    ("Camera",      "ai3", "rse",           1.0,  "V"),      # Camera feedback
    ("TTLLoopback", "ai4", "differential",  1.0,  "V"),      # TTL verification
]
```

Each entry specifies:
- Display name
- NI channel (ai0–ai4)
- Terminal configuration (differential/rse)
- Display scale (raw voltage multiplier)
- Units

## Project Structure

```
ephys_acquisition/
├── main.py                      # Application entry point
├── config.py                    # Hardware constants and channel definitions
├── requirements.txt             # Python dependencies
│
├── ui/                          # User interface
│   ├── main_window.py          # Top-level Qt window
│   ├── control_panel.py        # Acquisition controls tab
│   ├── camera_panel.py         # Camera settings tab
│   ├── stimulus_panel.py       # Stimulus controls
│   ├── trace_panel.py          # Real-time trace visualization
│   └── __init__.py
│
├── hardware/                    # Hardware interface layer
│   ├── daq_worker.py           # NI DAQ reader (QThread)
│   ├── daq_config.py           # DAQ task configuration
│   ├── camera_worker.py        # Basler camera interface (QThread)
│   ├── camera_config.py        # Camera settings
│   └── __init__.py
│
├── acquisition/                 # Data acquisition logic
│   ├── continuous_mode.py      # Main acquisition controller
│   ├── data_buffer.py          # Ring buffer for streaming data
│   ├── data_saver.py           # HDF5 file writing
│   └── __init__.py
│
└── utils/                       # Utilities
    ├── stimulus_generator.py    # TTL stimulus pattern generation
    └── __init__.py
```

### Key Components

**DAQWorker** (`hardware/daq_worker.py`)
- Runs in a separate QThread
- Continuously reads analog inputs and emits data signals
- Handles TTL output for triggering

**CameraWorker** (`hardware/camera_worker.py`)
- Independent QThread for camera frame grabbing
- Triggered via TTL pulse from DAQ
- Emits frame timestamps and metadata

**ContinuousAcquisition** (`acquisition/continuous_mode.py`)
- Orchestrates DAQ, camera, and data saving
- Manages acquisition lifecycle (start → record → stop)
- Handles guard delays for clean signal recording

**RingBuffer** (`acquisition/data_buffer.py`)
- Fixed-size circular buffer for streaming data
- Efficient memory usage for real-time display

**HDF5Saver** (`acquisition/data_saver.py`)
- Writes incoming data to HDF5 files
- Stores metadata (channel info, timestamps, settings)
- Handles recording start/stop events

## Workflow

1. **Start Acquisition**: DAQ begins sampling at configured rate; traces appear in left panel
2. **Adjust Settings**: Configure camera frame rate, exposure, Y-axis ranges in Experiment tab
3. **Start Recording**: 
   - Guard delay begins (baseline capture)
   - Camera triggers via TTL pulse after delay
   - Data stream written to timestamped HDF5 file
4. **Stop Recording**: Camera stops, final guard period captured, HDF5 file closed
5. **Stop Acquisition**: DAQ shuts down

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Device not found" error | Check device name in `config.py` using NI MAX; update `DEVICE_NAME` to match |
| Noisy traces | Reduce `SAMPLE_RATE` or check cable shielding |
| Camera not triggering | Verify TTL voltage levels in `config.py` (`TTL_HIGH_V`, `TTL_LOW_V`) |
| Files not saved | Check disk space and file permissions; verify `DEVICE_NAME` |
| UI freezes during recording | Ensure DAQWorker and CameraWorker are running in separate threads |

## Dependencies

- **PySide6** — Qt UI framework
- **pyqtgraph** — Real-time data visualization
- **nidaqmx** — NI DAQ hardware interface
- **pypylon** — Basler camera SDK
- **numpy** — Numerical computing
- **scipy** — Signal processing
- **h5py** — HDF5 file I/O

## Data Format

Recorded data is stored in HDF5 with the following structure:

```
/data                  # Main data group
  /ai0, /ai1, ...     # Channel datasets (dtype: float32)
  /timestamps         # Sample timestamps
  /metadata
    /channel_info     # Channel names, units, scaling factors
    /acquisition_settings  # Sample rate, recording start/stop times
```

## Performance Notes

- **Latency**: ~50 ms at 20 kHz sample rate with 200-sample chunk size
- **CPU**: Single-threaded DAQ reader + separate camera thread keeps UI responsive
- **Memory**: Ring buffer holds ~5 seconds of 5-channel data (~4 MB)

## License

[Add your license here]

## Contact

For questions or issues, contact me, kyle thieringer

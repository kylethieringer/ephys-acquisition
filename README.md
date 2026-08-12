# Ephys Acquisition

A real-time electrophysiology data acquisition system with integrated camera triggering, live visualization, and protocol-driven stimulation. Built with PySide6 and NI DAQ hardware (NI PCIe-6323).

📖 **[Documentation](https://kylethieringer.github.io/ephys-acquisition/)**

## What it does

- **Real-time acquisition** — continuous analog input at 20 kHz via NI DAQ, with a 5-second rolling display of every channel
- **Camera integration** — Basler Pylon camera with hardware TTL triggering and exposure control
- **Current and voltage clamp** — switch modes with automatic channel relabelling and rescaling
- **Protocol builder** — design current-step (CC) and voltage-step (VC) protocols in the GUI, or write the JSON directly
- **Two acquisition modes** — continuous recording with sample-accurate stimulus events, or per-trial HDF5 with pre-allocated datasets
- **Binary-first saving** — raw data goes to a `.bin` during acquisition and converts to HDF5 in the background; the `.bin` is always kept as a backup
- **Automatic QC** — every recording gets a self-contained HTML report covering sample-count consistency, signal sanity, stimulus fidelity, and TTL ↔ video drift
- **Rig alignment check** — a standalone weekly check against an Axon Patch-1U model cell, with long-term drift tracking

## Requirements

**Hardware** — NI PCIe-6323 (or compatible), Basler Pylon camera, patch-clamp amplifier

**Software** — Windows, Python 3.14+, NI-DAQmx driver, Basler Pylon runtime

## Install

The project is managed with [uv](https://docs.astral.sh/uv/):

```bash
git clone git@github.com:kylethieringer/ephys-acquisition.git
cd ephys-acquisition
uv sync
```

Hardware is configured in `config.py` — channel names, scaling factors, sample rate, and TTL parameters are all defined there.

## Run

```bash
uv run python main.py
```

## Documentation

Full documentation lives at **<https://kylethieringer.github.io/ephys-acquisition/>**:

| Section | Covers |
|---|---|
| [Getting started](https://kylethieringer.github.io/ephys-acquisition/getting-started/installation.html) | Installation, wiring, first recording |
| [User guide](https://kylethieringer.github.io/ephys-acquisition/guide/main-window.html) | Interface, clamp modes, protocols, acquisition modes |
| [Data](https://kylethieringer.github.io/ephys-acquisition/data/file-layout.html) | File layout, HDF5 schemas, loading recordings |
| [Quality control](https://kylethieringer.github.io/ephys-acquisition/qc/post-recording.html) | Automatic QC, reading reports, rig alignment |
| [Analysis](https://kylethieringer.github.io/ephys-acquisition/analysis/experiment-log.html) | Experiment log, intrinsics, summary figures |
| [API reference](https://kylethieringer.github.io/ephys-acquisition/autoapi/index.html) | Every module, class, and function |

### Building the docs

Sphinx sources are in `docs/`. The API reference is parsed statically, so no DAQ driver, camera SDK, or Qt runtime is needed to build:

```bash
uv run --group docs sphinx-build -W -b html docs docs/_build/html
```

Live reload while writing:

```bash
uv run --group docs sphinx-autobuild docs docs/_build/html --open-browser
```

Pushing to `main` builds and deploys to GitHub Pages automatically.

## License

See [LICENSE](LICENSE).

## Contact

Kyle Thieringer

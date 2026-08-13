---
sd_hide_title: true
---

# Ephys Acquisition

::::{grid} 1
:::{grid-item}
:class: sd-text-center sd-fs-3 sd-font-weight-bold

Ephys Acquisition
:::
::::

::::{grid} 1
:::{grid-item}
:class: sd-text-center sd-fs-5 sd-text-secondary

Real-time electrophysiology acquisition with camera triggering,
live visualization, and protocol-driven stimulation.
:::
::::

---

Built on PySide6 and NI DAQ hardware (PCIe-6323). Continuous analog input at
20 kHz, TTL-triggered Basler camera, current- and voltage-clamp protocols, and
an automatic quality-control pass on every recording.

::::{grid} 1 2 2 3
:gutter: 3

:::{grid-item-card} {octicon}`rocket;1.5em;sd-mr-1` Getting started
:link: getting-started/installation
:link-type: doc

Requirements, installation, wiring, and your first recording.
:::

:::{grid-item-card} {octicon}`beaker;1.5em;sd-mr-1` Running experiments
:link: guide/main-window
:link-type: doc

The interface, clamp modes, protocols, and the two acquisition modes.
:::

:::{grid-item-card} {octicon}`database;1.5em;sd-mr-1` Data
:link: data/file-layout
:link-type: doc

What gets written, the HDF5 layouts, and loading recordings in Python.
:::

:::{grid-item-card} {octicon}`checklist;1.5em;sd-mr-1` Quality control
:link: qc/post-recording
:link-type: doc

The automatic QC pass, how to read it, and the weekly rig alignment check.
:::

:::{grid-item-card} {octicon}`graph;1.5em;sd-mr-1` Analysis
:link: analysis/experiment-log
:link-type: doc

The experiment log, intrinsic properties, summary figures, video alignment.
:::

:::{grid-item-card} {octicon}`code;1.5em;sd-mr-1` API reference
:link: autoapi/index
:link-type: doc

Every module, class, and function in the codebase.
:::

::::

## Where things happen

:::{list-table}
:header-rows: 1
:widths: 26 74

* - Component
  - What it does
* - `main.py`
  - Qt application entry point — `python main.py`
* - `config.py`
  - Single authoritative source for channels, scaling, and hardware IDs
* - `acquisition/`
  - Continuous and trial-based recording controllers, savers, protocol runner
* - `hardware/`
  - NI DAQ and Basler camera worker threads
* - `analysis/`
  - Step analysis, video alignment, and the QC pipeline
* - `utils/`
  - Waveform generation, data loading, and the experiment-log tooling
:::

A fuller tour is in {doc}`reference/code-map`.

:::{note}
The API reference is parsed statically from the source tree, so it stays
accurate without anyone remembering to regenerate it.
:::

```{toctree}
:hidden:
:caption: Getting started

getting-started/installation
getting-started/quickstart
getting-started/hardware-setup
```

```{toctree}
:hidden:
:caption: User guide

guide/main-window
guide/clamp-modes
guide/protocols
guide/continuous-mode
guide/trial-mode
guide/experiment-checklist
```

```{toctree}
:hidden:
:caption: Data

data/file-layout
data/hdf5-format
data/loading-data
```

```{toctree}
:hidden:
:caption: Quality control

qc/post-recording
qc/interpreting-reports
qc/alignment-check
```

```{toctree}
:hidden:
:caption: Analysis

analysis/experiment-log
analysis/intrinsics
analysis/summary-figures
analysis/video-alignment
```

```{toctree}
:hidden:
:caption: Reference

reference/configuration
reference/troubleshooting
reference/code-map
autoapi/index
```

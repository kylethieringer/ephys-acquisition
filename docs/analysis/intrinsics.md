# Intrinsic properties

Resting membrane potential, input resistance, and spike counts from
current-injection step protocols — one recording at a time, or across a whole
cell type.

## One recording, interactively

```bash
uv run python -m analysis.analyze_steps
```

A file picker opens. {py:mod}`analysis.analyze_steps` then:

1. Loads the continuous-mode HDF5
2. Detects step-protocol stimuli, using protocol metadata when available
3. Computes resting membrane potential and input resistance
4. Lets you browse and save overlay plots of the step responses

Figures are written under `D:\results`.

Non-interactively, {py:func}`analysis.analyze_steps.process_file` is the batch
core: it loads, detects, computes, and writes `{stem}_intrinsics.csv` —
returning a dict of all intermediate results (or `None` if loading or step
detection failed) so callers can plot too.

## Spike detection

```python
from analysis.detect_spikes import detect_spikes

idx = detect_spikes(vm_mV, sr=20000)
n_spikes = len(idx)
```

{py:func}`~analysis.detect_spikes.detect_spikes` returns sample indices of
detected action potentials in a 1-D membrane-voltage trace. Tunable parameters:

:::{list-table}
:header-rows: 1
:widths: 26 14 60

* - Parameter
  - Default
  - Meaning
* - `method`
  - `"find_peaks"`
  - Detection strategy. Only `find_peaks` exists today; the branch structure is
    there so a `"dvdt"` method can be added without touching call sites.
* - `height_mV`
  - `None`
  - Absolute threshold; `None` means unconstrained
* - `prominence_mV`
  - `7.0`
  - Peak prominence — the main selectivity knob
* - `min_distance_ms`
  - `2.0`
  - Refractory window between accepted peaks
:::

## Across many recordings

{py:func}`analysis.batch_intrinsics.collect_intrinsics` reads the experiment
log, filters to the requested cell types where `keep` is truthy, and ensures
each has an `{stem}_intrinsics.csv` — computing any that are missing.

```python
from analysis.batch_intrinsics import collect_intrinsics

df = collect_intrinsics("DNa01")
```

One row per kept recording, with `expt_id`, `targeted_cell_type`, `h5_path`,
`intrinsics_csv_path`, and `status` — where status is `cached`, `computed`, or
`error`.

From the command line:

```bash
uv run python -m analysis.batch_intrinsics DNa01
uv run python -m analysis.batch_intrinsics dvmn DNa01
uv run python -m analysis.batch_intrinsics DNa01 --log D:/data/_experiment_log.csv
```

Options: `--log`, `--data-root`, `--results-csv-dir`.

:::{list-table}
:header-rows: 1
:widths: 42 58

* - Default path
  - Holds
* - `D:\data\_experiment_log.csv`
  - The experiment log
* - `D:\results\csv`
  - Per-recording `_intrinsics.csv` files
:::

Because results are cached, re-running is cheap — only new or changed
recordings are computed. `status` tells you which was which.

:::{note}
`collect_intrinsics` raises if the log has no `keep` column. Curate first — see
{doc}`experiment-log`.
:::

## Next

{doc}`summary-figures` aggregates these per-recording intrinsics into per-cell
F-I curves and summary plots.

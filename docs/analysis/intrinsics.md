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

**Resting potential** is the median Vm over a baseline window that ends just
before each protocol's first pulse. The window starts at whichever is earlier:
500 ms (`BASELINE_MS`) before that pulse, or the moment the protocol was
applied. For protocols run from the protocol panel, the protocol is applied well
before its first pulse, so the window can be several seconds long. For manual
and waveform steps, it is just the 500 ms look-back. The window never starts earlier than 50 ms (`BASELINE_SETTLE_MS`) after the end of
the previous pulse. Without that limit, protocols run back to back would pull
the previous protocol's last depolarising step into the baseline and report
driven Vm instead of rest. If fewer than 50 ms (`MIN_BASELINE_MS`) remain, RMP
is `NaN` rather than a contaminated value. Input resistance is computed from
this baseline, so it would carry the same error.

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
detected action potentials in a 1-D membrane-voltage trace. It never uses an
absolute voltage threshold, because fly motor-neuron APs often peak well below
0 mV.

The default `method="adaptive"` sets its threshold from each trace's own noise:

1. **Detrend.** A 20 ms median filter removes slow baseline humps and drift.
2. **Prominence bar.** A peak must stand 6 robust SDs above the detrended
   residual, and never less than 2.5 mV.
3. **Rate-of-rise veto.** The peak must also rise as fast as an AP: 4 robust
   SDs of the lightly smoothed derivative. A slow hump can be as tall as an
   attenuated AP but never rises that quickly.

A single fixed prominence can't serve every recording. APs shrink under strong
current injection (in `fre071`, from about 9–12 mV at 150 pA to 3–9 mV at
300 pA), so a bar high enough to reject artifacts in noisy recordings drops
real spikes wherever the cell is driven hardest. The 2.5 mV floor stops the
adaptive bar from sinking into the noise on quiet, silent cells.

:::{list-table}
:header-rows: 1
:widths: 26 14 60

* - Parameter
  - Default
  - Meaning
* - `method`
  - `"adaptive"`
  - `"adaptive"`, or `"find_peaks"` for the original fixed-prominence detector
* - `prominence_mV`
  - `7.0`
  - Fixed prominence bar. `find_peaks` only.
* - `height_mV`
  - `None`
  - Optional absolute peak-height floor; `None` disables it
* - `min_distance_ms`
  - `2.0`
  - Refractory window between accepted peaks
* - `detrend_ms`
  - `20.0`
  - Median-filter window for drift removal. Adaptive only.
* - `noise_k`
  - `6.0`
  - Prominence bar in robust SDs of the residual. Adaptive only.
* - `dvdt_k`
  - `4.0`
  - Rate-of-rise bar in robust SDs of the derivative. Adaptive only.
* - `dvdt_smooth_ms`
  - `0.5`
  - Smoothing applied before differentiating. Adaptive only.
* - `prominence_floor_mV`
  - `2.5`
  - Lower bound on the adaptive prominence bar. Adaptive only.
:::

:::{note}
The adaptive constants were calibrated on recordings with known answers from
this preparation: attenuated APs, step-transition artifacts, silent cells and
clean spiking. They are empirical, not derived from first principles. Check
them against a few traces before using them on a different cell type.
:::

When counting spikes per step,
{py:func}`~analysis.analyze_steps.compute_step_firing_rates` also ignores any
detection in the first 2 ms after step onset (`SPIKE_BLANK_MS`). At large
amplitudes the capacitive transient is tall and fast enough to pass as a
spike, whereas real first spikes arrive no earlier than about 3.6 ms.

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

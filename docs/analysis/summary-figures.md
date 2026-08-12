# Summary figures

Per-cell F-I curves, input resistance, and resting membrane potential,
aggregated across recordings for a cell type.

```bash
uv run python -m analysis.summary_figures dvmn
uv run python -m analysis.summary_figures dvmn DNa01
uv run python -m analysis.summary_figures dvmn --include-drug
```

Options: `--fi-protocols`, `--fig-dir`, `--summary-dir`, `--no-show`,
`--include-drug`.

{py:mod}`analysis.summary_figures` walks the experiment log via
{py:func}`analysis.batch_intrinsics.collect_intrinsics`, so everything in
{doc}`experiment-log` and {doc}`intrinsics` applies first: only `keep`-flagged
recordings are included, and missing intrinsics are computed on the way.

## The F-I side-car

F-I curves need to know **which** step protocols in a recording are the F-I
repeats. That cannot be inferred, so it comes from a hand-maintained CSV:

```text
D:\data\_fi_protocols.csv
```

Override the path with `--fi-protocols`.

:::{warning}
If the side-car is missing, the tool says so and the F-I plot comes out empty —
it does not fail. An empty F-I panel almost always means a missing or
mismatched side-car rather than missing data.
:::

The side-car may also carry an explicit `cell_id` column, which overrides the
derived grouping described below.

## What counts as a cell

By default a "cell" is the group:

```text
(expt_id, targeted_cell_type, condition)
```

where *condition* is either the baseline or a specific `drug_name`.

Splitting on drug name matters: several experiments applied both caffeine and
octopamine to the same cell, and a bare drug/no-drug key would average them
together. `drug_concentration` is deliberately **not** part of the key, because
the log records it in inconsistent units — it is reported in the manifest
instead.

The physical cell behind those conditions is the `pair_key`. It drives the
colour map, so one cell keeps a single colour across its baseline and drug
points, and the strip plots join that cell's conditions with a line.

## Drug recordings

Excluded by default. `--include-drug` plots them alongside the drug-free ones,
and output filenames gain a `_withdrug` suffix so both sets can coexist.

In the figures, drug points are hollow and drug F-I curves dashed.

## Averaging

Within a cell, recordings are averaged in two stages:

1. **Per HDF5 file first** — so one long recording cannot dominate the cell's mean
2. **Then across files** — so session-to-session drift stays visible as thin lines

This is why a cell with one 40-minute recording and one 5-minute recording does
not get pulled toward the long one.

## Programmatic use

```python
from analysis.summary_figures import summarize_cell_type

summarize_cell_type("DNa01", show=False, include_drug=False)
```

{py:func}`~analysis.summary_figures.summarize_cell_type` takes
`fi_protocols_path`, `fig_dir`, `summary_dir`, `show`, and `include_drug`,
building the per-cell summaries and figures for one cell type.

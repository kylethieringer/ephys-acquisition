# The experiment log

Every recording writes a `_metadata.json` sidecar. The experiment log rolls all
of them into one CSV you can sort, filter, annotate, and use to drive batch
analysis.

```text
recordings/*_metadata.json  →  _experiment_log.csv  →  dashboard / batch analysis
```

## Building the log

```bash
uv run python utils/update_experiment_log.py
uv run python utils/update_experiment_log.py --data-dir D:/data
uv run python utils/update_experiment_log.py --data-dir D:/data --output D:/data/_experiment_log.csv
```

Defaults: scans `D:/data`, writes `<data-dir>/_experiment_log.csv`.

It scans for `*_metadata.json` sidecars and merges their contents into the CSV.
Re-run it whenever you have new recordings — it is a refresh, not a rebuild.

:::{important}
**Columns you add by hand are never overwritten.** Notes, quality scores, the
`keep` flag — the merge preserves them and only refreshes the acquisition-derived
columns. That is what makes the CSV safe to use as a working document.
:::

:::{warning}
Close the CSV in LibreOffice or Excel before refreshing. An open spreadsheet
holds a lock file (`.~lock._experiment_log.csv#`) and keeps its own in-memory
copy — if you refresh while it is open and then save from the spreadsheet, the
spreadsheet's stale copy silently overwrites everything the refresh just wrote.
Nothing warns you. Check for the lock file if you are unsure.
:::

## Browsing it

An interactive Streamlit browser over the same CSV:

```bash
uv run streamlit run utils/experiment_dashboard.py
uv run streamlit run utils/experiment_dashboard.py -- --data-dir D:/data
```

Note the bare `--`: everything after it goes to the script rather than to
Streamlit.

The dashboard reads the log, lists the files in each experiment folder, and
shows the parsed JSON metadata for a selected recording — the fastest way to
answer "what did I actually record on that cell?" without opening HDF5 files.

## The `keep` column

`keep` is the curation flag that drives batch analysis.
{py:func}`analysis.batch_intrinsics.collect_intrinsics` processes only rows
where it is truthy — accepted values are `1`, `true`, `yes`, `y`, `keep`, `t`
(case-insensitive).

Add the column yourself; it is not created by the refresh. Once present, it
survives every subsequent refresh.

:::{note}
If `keep` is missing entirely, `collect_intrinsics` raises rather than silently
processing everything. That is deliberate — an empty curation state should not
look like "analyse all of it".
:::

## Where it leads

- {doc}`intrinsics` — batch intrinsic properties from kept recordings
- {doc}`summary-figures` — per-cell F-I, input resistance, and RMP summaries

Both read the log directly, so curation in the CSV is what determines what gets
analysed.

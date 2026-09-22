# Experiment-day checklist

A small PySide6 helper that shows the day's tasks in order and launches the
relevant tool for the ones that have one.

```bash
uv run python utils/experiment_checklist.py
```

## What it does

Tasks are listed in order. Checking one persists it **per calendar day** — the
checked set is stored in `utils/.checklist_state.json` and resets automatically
when a new day begins, so you start each experiment day with a clean list
without having to clear anything.

Some tasks carry a one-click launcher that starts the relevant GUI or script.

## Default task list

:::{list-table}
:header-rows: 1
:widths: 44 56

* - Task
  - Launcher
* - Pull electrodes
  - —
* - Filter saline
  - —
* - Make solutions
  - —
* - Get ice
  - —
* - Prep fly
  - —
* - Record experiments
  - Opens the acquisition GUI
* - Dissect brain
  - —
* - Stain brain
  - —
* - Update experiment log
  - **Refresh** rebuilds the log CSV · **Open** opens it
* - Copy data to Google Drive
  - **Dry run** previews the sync · **Sync** performs it (in a Cmder window, see below)
* - Visualize data
  - Opens the analysis GUI
* - Analyze steps
  - Runs the step-analysis tool
:::

## Google Drive sync

**Dry run** and **Sync** run `utils/sync_to_gdrive.sh` (with and without
`--dry-run`). It is a bash script that shows rclone's live progress, so it
opens in a [Cmder](https://cmder.app/) terminal window rather than in the
background. The window stays open after rclone finishes, so you can read the
result, and closes when you press Enter.

The checklist looks for Cmder in the `CMDER_ROOT` environment variable, then on
`PATH`, then in `C:\Users\<you>\cmder`. If it can't find Cmder, it shows a
dialog with the command to run by hand:

```bash
./utils/sync_to_gdrive.sh --dry-run
./utils/sync_to_gdrive.sh
```

## Editing the list

The tasks are defined in `utils/checklist_tasks.json`, which is meant to be
hand-edited. Each entry needs an `id` and a `label`; a launcher is optional:

```json
{ "id": "prep_fly", "label": "Prep fly" },
{ "id": "record_experiments", "label": "Record experiments", "action": "ephys_gui" },
{ "id": "update_experiment_log", "label": "Update experiment log",
  "actions": [
    { "key": "update_log", "label": "Refresh" },
    { "key": "open_log",   "label": "Open" }
  ]
}
```

Use `action` for a single button, `actions` for several. The `id` is what gets
persisted in the state file.

:::{warning}
Changing a task's `id` orphans its saved state for the current day — the task
will show as unchecked even if you had already ticked it. Changing the `label`
is safe.
:::

The `key` values map to launch commands defined in
{py:mod}`utils.experiment_checklist`. Adding a *new* key means adding it there
too; reusing an existing key needs only the JSON edit.

## Related

- {doc}`../analysis/experiment-log` — what "Update experiment log" actually does
- {doc}`../qc/post-recording` — QC runs on its own, no checklist step needed

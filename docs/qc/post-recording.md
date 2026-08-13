# Post-recording QC

Every recording that finishes saving triggers a quality-control pass
automatically. Nothing needs to be run by hand, and the GUI never blocks
waiting for it — {py:func}`analysis.qc.hook.schedule_qc` spawns a daemon thread
that runs the pipeline against the freshly written HDF5 file.

:::{note}
QC validates the *recording*, not the *physiology*. It cross-checks saved data
against acquisition metadata, flags obvious signal problems, and verifies the
amplifier command actually matched what was commanded. Whether the cell was
healthy is still a judgement call for a human.
:::

## What lands next to the recording

:::{list-table}
:header-rows: 1
:widths: 32 68

* - File
  - Contents
* - `<stem>_qc_report.html`
  - Self-contained report — status badge per check, formatted metrics, an
    interactive Plotly overview of every channel, and (trial mode)
    commanded-vs-recorded `AmpCmd` overlays
* - `<stem>_qc_report.json`
  - The same data, machine-readable
* - `<stem>_acquisition.log`
  - Buffer-fill warnings drained from the DAQ worker — **only written if
    events actually occurred**
:::

The HTML report embeds its own plots as base64 PNGs, so it can be emailed or
copied to another machine and still render.

:::{tip}
Above 600 000 samples per channel the overview plot is min/max-decimated rather
than subsampled, so spikes stay visible at every zoom level instead of
disappearing between sample points.
:::

## The three check sections

::::{tab-set}

:::{tab-item} Acquisition integrity
Implemented in {py:mod}`analysis.qc.integrity`.

- Sample-count consistency across the HDF5, the `.bin` backup, and the
  `metadata.json` sidecar
- HDF5 ↔ sidecar metadata agreement
- Finite values (no `NaN` or `inf` samples)
- Stimulus event table (continuous mode) or trial table (trial mode)
- Camera TTL ↔ video frame-count drift
- Acquisition-log severity
:::

:::{tab-item} Signal sanity
Implemented in {py:mod}`analysis.qc.signal`.

Per-channel range and RMS checks: DAQ rail saturation, DC offset, baseline
RMS, 60/120/180 Hz line noise fraction, and baseline drift.

Thresholds are deliberately permissive — they catch a saturated channel, a
railed DC level, or bad mains hum, not subtle problems.
:::

:::{tab-item} Commanded vs recorded
Implemented in {py:mod}`analysis.qc.stimulus`.

Compares the `AmpCmd` loopback channel against the protocol that was
requested, confirming the DAQ actually played back what the protocol asked
for.
:::

::::

## Reading the status badges

Every check returns exactly one status. The banner at the top of the report is
the **worst** status across all checks, computed by
{py:func}`analysis.qc.worst`.

:::{list-table}
:header-rows: 1
:widths: 18 82

* - Status
  - Meaning
* - <span class="qc-status qc-status-pass">pass</span>
  - The check ran and found nothing wrong.
* - <span class="qc-status qc-status-warn">warn</span>
  - Something is off but the recording is probably usable. Worth a look.
* - <span class="qc-status qc-status-fail">fail</span>
  - The check found a real problem, **or** the check itself raised an
    exception. Read the message.
* - <span class="qc-status qc-status-skip">skip</span>
  - Not applicable to this recording — e.g. stimulus checks on a recording
    with no protocol.
:::

:::{important}
No check ever raises. Each one catches its own exceptions and downgrades to
`fail` with the exception message attached, so the orchestrator can always
produce a report — a broken check never costs you the whole report.
:::

## Re-running QC on an existing file

The automatic pass runs once, at save time. To re-run it later — after
changing a threshold, or on a recording saved before a check existed — use the
CLI:

```bash
python -m analysis.qc_report path/to/recording.h5
```

This rewrites `*_qc_report.html` and `*_qc_report.json` in place.

## Calling the pipeline directly

The package exposes a small public surface for use from a notebook:

```python
from analysis.qc.report import run_qc

result = run_qc("D:/data/KT001/KT001_CS_20260812.h5", write=False)

print(result["status"])          # worst status across every check

for section, checks in result["sections"].items():
    for check in checks:
        if check["status"] != "pass":
            print(f"[{section}] {check['name']}: {check['status']}")
            print("   ", check["message"], check["metrics"])
```

{py:func}`~analysis.qc.report.run_qc` returns a dict with `status`, `sections`,
`html_path`, and `json_path`. Pass `write=False` to compute without writing
report files next to the recording.

Each entry in a section comes from {py:class}`analysis.qc.Check` — a name, a
{py:class}`analysis.qc.Status`, a short message, and a `metrics` dict of numbers
that also lands in the JSON report.

:::{note}
`run_qc` lives in {py:mod}`analysis.qc.report`, not in {py:mod}`analysis.qc`
itself. The package deliberately stays cheap to import; pulling in `run_qc`
would bring matplotlib, plotly, and jinja2 along with it.
:::

:::{dropdown} Where the interpretation text comes from
:icon: info

The prose shown beside each check in the HTML report lives in
{py:mod}`analysis.qc.descriptions`, keyed by the exact `Check.name` string used
where the check is constructed. This keeps interpretation guidance out of the
check modules (which stay focused on computation) and out of the Jinja template
(which stays focused on layout).

Edit the strings there and the next rendered report picks them up. If a check's
name is missing from the map, the report still renders it — just without the
interpretation paragraph.
:::

## Buffer-fill warnings

The DAQ worker watches `avail_samp_per_chan` after every chunk read. If the
driver buffer crosses 70 % of capacity, the event is recorded and drained to
`*_acquisition.log` when recording stops.

:::{warning}
A few isolated buffer-fill events are tolerable. Sustained warnings mean the
GUI or save thread is starving the analog-input reader — that is a real risk of
dropped samples, not a cosmetic complaint.
:::

## See also

- {doc}`../autoapi/analysis/qc/index` — full API for the QC package
- {py:func}`analysis.qc.hook.write_acquisition_log` — how the log file is written

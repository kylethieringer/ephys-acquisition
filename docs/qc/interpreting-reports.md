# Interpreting QC reports

A reference for what each check actually tests and what to do when it is not
green.

The interpretation text shown in the HTML report itself lives in
{py:mod}`analysis.qc.descriptions`, keyed by exact check name. If you find
better wording while reading a report, edit it there and the next rendered
report picks it up.

## Status meanings

:::{list-table}
:header-rows: 1
:widths: 14 86

* - Status
  - Meaning
* - <span class="qc-status qc-status-pass">pass</span>
  - Metric is within expected bounds.
* - <span class="qc-status qc-status-warn">warn</span>
  - Value is unusual but may still be acceptable.
* - <span class="qc-status qc-status-fail">fail</span>
  - Value is outside acceptable bounds, **or the check itself could not be
    computed**. Review before using the recording.
* - <span class="qc-status qc-status-skip">skip</span>
  - The inputs for this check were not present — e.g. no video file next to
    the recording.
:::

The banner at the top of the report is the worst status across every check.

:::{note}
`fail` covering both "bad value" and "check crashed" is deliberate: a check
that cannot run is not evidence of health. The message text distinguishes them.
:::

## Acquisition integrity

Verifies the recording file is complete and internally consistent.

Sample-count consistency
: Sample counts agree across the HDF5, the `.bin` backup, and the sidecar. A
  mismatch means a write path was interrupted — the `.bin` is the authority.

HDF5 ↔ sidecar metadata
: Acquisition settings recorded in two places agree. Disagreement usually means
  a setting changed mid-session.

Finite values (no NaN/Inf)
: No non-finite samples. Failures here point at a hardware or driver problem,
  not physiology.

Stimulus event table
: Continuous mode — the `/stimulus_events/` table is well-formed and its
  `apply`/`clear` events pair up.

Trial table integrity
: Trial mode — every trial group is present with consistent attributes and
  sample counts.

Camera TTL ↔ video frame count
: Rising edges on the TTL channel match the frame count in the video file.
  Drift means dropped frames. Check the frame rate against
  {py:data}`config.DEFAULT_FRAME_RATE_HZ` and the PFI12 → camera trigger cable.

Live acquisition log
: Severity of any buffer-fill events drained to `*_acquisition.log`. The file
  only exists if events occurred.

## Signal sanity

Per-channel checks in {py:mod}`analysis.qc.signal`, operating on raw volts but
thresholding in display units where that is more meaningful.

Saturation (±10 V rails)
: Time spent within 0.5 % of the ±10 V DAQ rails. Warn above 0.01 % of samples,
  fail above 0.1 %. A saturated channel is clipped data — check amplifier gain.

DC offset
: Channel sitting at a large steady offset.

Baseline RMS noise
: Noise amplitude in the baseline period.

Line noise (60 Hz + harmonics)
: Fraction of power at 60, 120, and 180 Hz (±2 Hz bands). Warn above 10 %, fail
  above 30 %. Usually a grounding problem.

Baseline drift
: Slow trend across the recording.

:::{important}
Signal thresholds are deliberately permissive. They catch a saturated channel,
a railed DC level, or bad mains hum — they are not a substitute for looking at
the trace.
:::

## Commanded vs recorded

Confirms the DAQ actually played back what the protocol requested, by comparing
the `AmpCmd` loopback against the commanded waveform.

Commanded vs. recorded stimulus (per trial)
: Trial mode — one comparison per trial. The report overlays them.

Commanded vs. recorded stimulus (continuous)
: Continuous mode — compared against the event timeline.

A failure here means the stimulus the cell received is not the stimulus you
think you delivered, which invalidates the recording for stimulus-locked
analysis. Check AO wiring and the `ai2` loopback connection.

## Alignment-check sections

These appear only in the standalone rig report — see {doc}`alignment-check`.

::::{tab-set}

:::{tab-item} Phase A — rig timing
- AO → AmpCmd loopback latency
- Inter-channel crosstalk
- Counter TTL period stability

No model cell required.
:::

:::{tab-item} Phase B1 — CC
- Model-cell resting baseline (I=0)
- Model-cell amplifier noise floor
- CC scaling & linearity (model cell)
- Analysis pipeline self-test (`compute_input_resistance`)
:::

:::{tab-item} Phase B2 — VC
- VC scaling (model cell)
- Capacitance transient τ (VC step)
:::

::::

## Machine-readable output

Every check's numeric metrics land in `*_qc_report.json` alongside the HTML, so
you can track a metric across sessions:

```python
import json
from pathlib import Path

for p in sorted(Path("D:/data").rglob("*_qc_report.json")):
    report = json.loads(p.read_text())
    for check in report["sections"]["Signal sanity"]:
        if check["name"] == "Line noise (60 Hz + harmonics)":
            print(p.parent.name, check["status"], check["metrics"])
```

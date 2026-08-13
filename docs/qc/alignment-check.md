# Rig alignment check

A periodic health check on the **rig itself**, independent of any recording.
Run it weekly, and after any rig change — rewiring, an amplifier swap, a driver
reinstall.

```bash
uv run python -m analysis.qc_alignment --save-dir D:/data

# Phase A only — no model cell required
uv run python -m analysis.qc_alignment --save-dir D:/data --no-phase-b

# Non-default model cell resistance
uv run python -m analysis.qc_alignment --save-dir D:/data --model-cell-MOhm 500
```

:::{important}
Phase B drives the rig against an **Axon Instruments Patch-1U model cell in
CELL mode at 500 MΩ**, patched in place of a pipette. Running Phase B without
it produces meaningless numbers, not an error — use `--no-phase-b` when the
model cell is not in.
:::

## Output

Everything lands under `{save-dir}/_alignment_checks/`:

:::{list-table}
:header-rows: 1
:widths: 40 60

* - File
  - Contents
* - `qc_alignment_<timestamp>.html`
  - Per-run report, same layout as the post-recording report
* - `qc_alignment_<timestamp>.json`
  - Machine-readable version
* - `qc_alignment_history.csv`
  - **One row appended per run** — the drift-tracking record
:::

The history CSV is the point of the exercise. Any single run tells you whether
the rig is broken today; the CSV tells you whether it is slowly getting worse.
Key metrics per row: loopback lag, observed TTL rate, fitted CC slope in MΩ,
R², τ, computed Ri.

## Phase A — rig only

No model cell needed. Checks the DAQ and its wiring:

AO → AmpCmd loopback latency
: How long a command written to `ao0` takes to appear on `ai2`.

Inter-channel crosstalk
: Drives `ao0` hard and measures narrowband coupling onto the Camera and TTL
  channels.

Counter TTL period stability
: Jitter in the `ctr0` pulse train against the acquisition clock.

:::{dropdown} How crosstalk is measured
:icon: info

The details matter for interpreting the number:

- The TTL counter **runs during the test**, so PFI12 is actively driven rather
  than floating — a floating RSE input gives false-positive pickup.
- The drive frequency (137 Hz) is deliberately offset from the 100 Hz TTL rate
  and from 60 Hz mains, so no harmonic of either lands in the drive bin.
- Amplitude is extracted with a Hann-windowed single-frequency DFT
  (Goertzel-style), suppressing spectral leakage from TTL content by roughly
  30 dB compared with a rectangular window.
- A background bin at 147 Hz is measured too, so the reported coupling is
  relative to the local noise floor rather than an absolute number.
:::

## Phase B — model cell

With the Patch-1U in place:

**B1 — current clamp**

- Model-cell resting baseline (I=0)
- Model-cell amplifier noise floor, including the 60/120/180 Hz line fraction
- CC scaling and linearity — a ΔV = I·R step protocol, fitted for slope and R²
- Analysis pipeline self-test — feeds a fresh CC recording through
  {py:func}`analysis.analyze_steps.compute_input_resistance` and checks the
  answer against the known model-cell resistance

**B2 — voltage clamp**

- VC scaling
- Capacitance transient τ from a VC step

:::{note}
The pipeline self-test is the check worth understanding: it validates the
*analysis* as well as the hardware. A known 500 MΩ resistor should come back as
500 MΩ through the full acquisition-and-analysis chain. If it does not, the
problem may be in the analysis code rather than the rig.
:::

## Reading the results

Statuses mean the same thing as in the post-recording report — see
{doc}`interpreting-reports`, which also lists every Phase A and Phase B check.

For drift, plot the history CSV:

Columns are generated from check names as `{check_name}__{metric}`, plus a
`{check_name}__status` for every check, so it is easier to discover them than to
guess:

```python
import pandas as pd

hist = pd.read_csv("D:/data/_alignment_checks/qc_alignment_history.csv",
                   parse_dates=["timestamp"])

# Which columns carry the fitted CC slope?
cols = [c for c in hist.columns if c.endswith("__fitted_slope_MOhm")]
print(cols)

hist.plot(x="timestamp", y=cols[0], marker="o")
```

The metrics promoted into the CSV for quick plotting are `lag_ms`,
`camera_rms_v`, `ttl_rms_v`, `observed_rate_hz`, `period_jitter_frac`,
`resting_mV`, `fitted_slope_MOhm`, `r_squared`, `implied_R_from_slope`,
`tau_ms`, and `computed_Ri_MOhm`. Everything else stays in the per-run JSON.

A single out-of-range run is worth re-running before acting on. A trend across
several weeks is the rig telling you something.

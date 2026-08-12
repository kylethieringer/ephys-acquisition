# Troubleshooting

## Hardware

Device not found
: The device name in {py:data}`config.DEVICE_NAME` does not match reality. Open
  NI MAX and check what the card is actually enumerated as — a reinstall or a
  second card can shift `Dev1` to `Dev2`.

Camera not triggering
: Verify TTL levels ({py:data}`config.TTL_HIGH_V`,
  {py:data}`config.TTL_LOW_V`) and the PFI12 → camera trigger wiring. The
  counter output terminal is set explicitly via `co_pulse_term` in
  {py:mod}`hardware.daq_config`; if that routing is wrong the counter still
  runs and the camera silently never fires.

Traces are flat or railed
: Check amplifier gain and the clamp mode. A channel pinned at ±10 V is
  saturating the DAQ — the QC report's saturation check will confirm.

## Recording

HDF5 conversion failed
: The raw `.bin` is preserved. Recover it with `np.fromfile` — see
  {doc}`../data/file-layout` for the exact reshape.

Protocol not in the dropdown
: Protocol `.json` files must be in `D:/protocols`. Click **↻** to re-read the
  directory after adding one.

`*_acquisition.log` was written
: The DAQ driver buffer crossed 70 % of capacity at least once; each line gives
  the offending sample index. A few isolated events are tolerable. Sustained
  warnings mean the GUI or save thread is starving the AI reader, which risks
  dropped samples.

Recording never closed cleanly
: A sidecar with `end_time: null` means the recording did not finish. The
  `.bin` still holds the data.

## Quality control

QC report shows TTL ↔ video drift
: Camera frame rate does not match {py:data}`config.DEFAULT_FRAME_RATE_HZ`, or
  frames were dropped. Verify the trigger cable from PFI12 → camera Line1.
  Alignment tooling derives frame times from TTL edges, so it survives dropped
  frames — but the count mismatch is worth understanding first. See
  {doc}`../analysis/video-alignment`.

Commanded vs recorded fails
: The stimulus the cell received is not the one you commanded. Check AO wiring
  and the `ai2` loopback.

`analysis.qc_alignment` Phase B failing
: Verify the Patch-1U is in **CELL** mode (not HEAD) at 500 MΩ, and that
  amplifier gain settings have not drifted from what `config.py` claims.

High line noise
: The 60/120/180 Hz check warns above 10 % of power and fails above 30 %.
  Usually grounding.

## Analysis

`'keep' column not found`
: {py:func}`analysis.batch_intrinsics.collect_intrinsics` requires a `keep`
  column in the experiment log. Add it by hand — see
  {doc}`../analysis/experiment-log`.

F-I plot comes out empty
: The `_fi_protocols.csv` side-car is missing or does not match. The tool
  reports this and continues rather than failing. See
  {doc}`../analysis/summary-figures`.

Experiment log edits disappeared
: The CSV was open in LibreOffice or Excel during a refresh, and saving from
  the spreadsheet overwrote the refreshed file with its stale in-memory copy.
  Check for a `.~lock._experiment_log.csv#` file before refreshing.

`ImportError: cannot import name 'run_qc' from 'analysis.qc'`
: `run_qc` lives in {py:mod}`analysis.qc.report`, not the package root — 
  `from analysis.qc.report import run_qc`. The package stays lightweight on
  purpose.

## Environment

Jupyter kernel will not start ("no ipykernel")
: The venv's `pyvenv.cfg` `home` key points at a path that is no longer trusted
  or valid. Repoint it at the concrete CPython install directory rather than a
  junction.

`VIRTUAL_ENV does not match the project environment path`
: A stale `VIRTUAL_ENV` from another checkout is set in the shell. `uv` ignores
  it and uses the project's `.venv`; the warning is harmless. Use `--active` if
  you genuinely want the other environment.

Writing an HDF5 attribute corrupted the file
: Check free disk space before editing attributes in place. On an
  end-of-attribute corruption, delete the broken attribute and reassign it to
  recover.

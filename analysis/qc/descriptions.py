"""
Human-readable descriptions for the QC report.

Keeps interpretation guidance out of the check modules (which should stay
focused on computation) and out of the HTML template (which should stay
focused on layout).  Edit strings here and the report updates the next
time it renders.

Each ``CHECK_DESCRIPTIONS`` entry is keyed by the exact ``Check.name``
used when the check is constructed in :mod:`analysis.qc.integrity`,
:mod:`analysis.qc.signal`, or :mod:`analysis.qc.stimulus`.  If a check's
name is not in the map, the report falls back to an empty description
(the check still renders, just without interpretation text).
"""

from __future__ import annotations


REPORT_INTRO: str = (
    "This is an automated quality-control (QC) report generated immediately "
    "after an electrophysiology recording. It cross-checks the saved data "
    "against the acquisition metadata, flags obvious signal problems "
    "(saturation, line noise, NaN samples, dropped video frames), and "
    "verifies that the amplifier command waveform the DAQ produced actually "
    "matches the commanded stimulus. It does <b>not</b> validate cell-level "
    "physiology — that still needs a human. Each section collects a handful "
    "of independent checks; each check produces a status (pass/warn/fail/"
    "skip) and a dict of numeric metrics. The top-of-page banner is the "
    "worst status across every check."
)

STATUS_KEY: str = (
    "<b>pass</b> — metric is within expected bounds. "
    "<b>warn</b> — value is unusual but may still be acceptable. "
    "<b>fail</b> — value is outside acceptable bounds or the check itself "
    "could not be computed; review before using this recording. "
    "<b>skip</b> — the inputs for this check were not present "
    "(e.g., no video file next to the recording)."
)


SECTION_DESCRIPTIONS: dict[str, str] = {
    "Acquisition integrity": (
        "Verifies the recording file is complete and internally consistent "
        "— no samples were dropped, metadata matches across files, and "
        "stimulus/video bookkeeping is intact. A fail here means what's "
        "on disk doesn't match what the rig thought it recorded and the "
        "data should not be trusted without manual inspection."
    ),
    "Signal sanity": (
        "Per-channel signal-quality metrics computed on the saved data. "
        "These surface rig-level problems (saturated channel, 60 Hz hum, "
        "DC drift) rather than validate the physiology of the preparation. "
        "Thresholds are intentionally permissive — the goal is to catch "
        "obvious failures, not to substitute for careful review."
    ),
    "Commanded vs recorded": (
        "Cross-checks the commanded amplifier waveform against the "
        "<code>AmpCmd</code> analog input (ai2), which is a hardware "
        "loopback of whatever command the amplifier actually produced. "
        "Disagreement here catches mis-scaled AO output, a stuck DAQ "
        "channel, or an amplifier front-end fault before they show up "
        "in the physiology."
    ),
}


CHECK_DESCRIPTIONS: dict[str, str] = {
    # ------------------------------------------------------------------
    # Acquisition integrity
    # ------------------------------------------------------------------
    "Sample-count consistency": (
        "Cross-checks the number of samples reported by three "
        "independent write paths: the HDF5 <code>/data/analog_input</code> "
        "dataset, the raw <code>.bin</code> backup file, and the "
        "<code>_metadata.json</code> sidecar. All three should agree. "
        "A nonzero Δ in bytes means one of the writers got out of sync, "
        "usually from a DAQ buffer overrun or a crash during close."
    ),
    "HDF5 ↔ sidecar metadata": (
        "Compares the <code>sample_rate</code>, <code>channel_names</code>, "
        "and <code>start_time</code> stored inside the HDF5 with the same "
        "fields in the <code>_metadata.json</code> sidecar. They are "
        "written by different code paths, so a mismatch suggests the "
        "sidecar was overwritten by another recording or the HDF5 was "
        "hand-edited. <code>start_time</code> is compared only to the "
        "second — sub-second differences are expected and ignored."
    ),
    "Finite values (no NaN/Inf)": (
        "Counts samples equal to NaN or ±Inf in every recorded channel. "
        "Real DAQ traces should never contain non-finite values — a "
        "nonzero count points to a driver fault or a math bug somewhere "
        "downstream of the raw read."
    ),
    "Stimulus event table": (
        "Verifies the apply/clear events stored in "
        "<code>/stimulus_events</code> are in chronological order, "
        "inside the recording range, and correctly paired (every "
        "<code>apply</code> has a matching <code>clear</code>). Unclosed "
        "applies usually just mean the recording was stopped mid-stimulus."
    ),
    "Trial table integrity": (
        "Trial-mode only. Confirms the recorded trial count matches the "
        "protocol, trial indices are contiguous 0..N−1, and the channel "
        "layout is the same across every trial. Missing trials almost "
        "always mean a mid-run abort."
    ),
    "Camera TTL ↔ video frame count": (
        "Counts low→high edges on the <code>TTLLoopback</code> channel "
        "(0–5 V trigger pulse train) and compares the total to the AVI "
        "file's frame count. Drift &gt; 1 frame means the camera dropped "
        "or duplicated frames relative to the commanded trigger — "
        "usually a USB-bandwidth problem, a too-short exposure, or a "
        "loose cable."
    ),
    "Live acquisition log": (
        "Parses any <code>_acquisition.log</code> file written during "
        "the recording by the DAQ read loop. Warnings here are "
        "buffer-fill events — the DAQ's driver-side buffer was getting "
        "close to full because the save thread couldn't keep up. A few "
        "are usually harmless; many suggest an I/O bottleneck."
    ),

    # ------------------------------------------------------------------
    # Signal sanity
    # ------------------------------------------------------------------
    "Saturation (±10 V rails)": (
        "Fraction of samples within 0.05 V of the ±10 V DAQ rails. "
        "Clipped samples are permanently lost — the DAQ cannot represent "
        "values outside its range. Warns above 0.01 %, fails above 0.1 %. "
        "If this trips, drop the amplifier gain or reduce the stimulus "
        "amplitude."
    ),
    "DC offset": (
        "Mean voltage per channel, reported in display units "
        "(mV/pA/nA). A nonzero mean on <code>ScAmpOut</code> is just the "
        "cell's resting potential; a nonzero mean on the command "
        "channels reflects any holding current/voltage set on the "
        "amplifier. Informational only — no thresholds."
    ),
    "Baseline RMS noise": (
        "Root-mean-square deviation over a pre-stimulus baseline "
        "segment, per signal channel. For a good patch-clamp seal at "
        "20 kHz, typical values are under ~1 mV on <code>ScAmpOut</code> "
        "and under ~20 pA on <code>RawAmpOut</code>. Values well above "
        "those suggest a bad seal, a noisy electrode, or a ground-loop."
    ),
    "Line noise (60 Hz + harmonics)": (
        "Fraction of total signal power concentrated in narrow bands "
        "around 60, 120, and 180 Hz (US mains harmonics), computed via a "
        "Welch PSD. A few percent is normal. &gt;10 % warns; &gt;30 % "
        "fails — at that point mains hum dominates the recording. Fix by "
        "checking grounding, cable routing, or the Faraday cage."
    ),
    "Baseline drift": (
        "Slope of a linear fit to each signal channel across the full "
        "recording. Small slopes are expected from temperature changes "
        "and junction-potential drift. Large slopes (on <code>ScAmpOut</code> "
        "greater than a few mV/s) may mean the electrode is pulling out "
        "or the amplifier is drifting. Informational only — no threshold."
    ),

    # ------------------------------------------------------------------
    # Commanded vs recorded
    # ------------------------------------------------------------------
    "Commanded vs. recorded stimulus (per trial)": (
        "For every trial, rebuilds the expected <code>ao0</code> waveform "
        "from the saved protocol parameters and compares it to the "
        "recorded <code>AmpCmd</code> trace. Reports three numbers per "
        "trial: <code>rmse_v</code> (overall shape mismatch), "
        "<code>peak_error_v</code> (worst single-sample deviation), and "
        "<code>timing_offset_ms</code> (cross-correlation lag between "
        "expected and recorded). Typical: RMSE &lt; 5 % of peak "
        "commanded amplitude and &lt; 1 ms lag."
    ),
    "Commanded vs. recorded stimulus (continuous)": (
        "Continuous-mode version: we don't store stimulus parameters, so "
        "we only check that <code>AmpCmd</code> is quiet between named "
        "apply/clear windows and is non-zero inside them. A nonzero RMS "
        "outside stimulus windows is usually a static holding current "
        "set on the amplifier — benign. A quiet <code>AmpCmd</code> "
        "<i>inside</i> a stimulus window means ao0 isn't wired or "
        "isn't being driven."
    ),
}


def describe_check(name: str) -> str:
    """Return the long description for a check name, or empty string."""
    return CHECK_DESCRIPTIONS.get(name, "")


def describe_section(name: str) -> str:
    """Return the long description for a section name, or empty string."""
    return SECTION_DESCRIPTIONS.get(name, "")

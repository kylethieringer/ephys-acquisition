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

ALIGNMENT_REPORT_INTRO: str = (
    "This is the <b>standalone hardware-alignment report</b>. It is not "
    "tied to any single recording — it is a periodic health check on the "
    "rig itself, run weekly or after any rig change (rewiring, amplifier "
    "swap, DAQ firmware update). It is run from the CLI with the Axon "
    "Patch-1U <b>model cell</b> in CELL mode (a known 500 MΩ resistor) "
    "patched in place of a real pipette, so every measurement has a "
    "ground-truth answer.<br><br>"
    "The report is split into two phases. <b>Phase A</b> exercises the "
    "rig itself — DAQ timing, channel-to-channel isolation, the camera "
    "trigger counter — and needs no model-cell or amplifier assumptions. "
    "<b>Phase B</b> uses the 500 MΩ model cell as a calibrator: we drive "
    "currents and voltages through it and confirm the amplifier reports "
    "back what Ohm's law predicts (ΔV = I·R, ΔI = ΔV/R). Phase B is "
    "split again into a current-clamp block (B1) and a voltage-clamp "
    "block (B2); the operator is prompted to flip the amplifier's "
    "front-panel mode switch between the two.<br><br>"
    "Numeric metrics for every run are appended to "
    "<code>qc_alignment_history.csv</code> in the same folder, so drift "
    "across weeks is easy to plot. A <b>fail</b> here means a real rig "
    "problem that will corrupt subsequent recordings; a <b>warn</b> "
    "means the value has drifted outside its normal envelope but is "
    "probably still usable. Investigate before running animals."
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

    # ------------------------------------------------------------------
    # Standalone hardware-alignment report
    # ------------------------------------------------------------------
    "Phase A — rig timing": (
        "Pure rig checks — no model cell or amplifier assumptions needed. "
        "Confirms the DAQ's analog-out and analog-in clocks are sample-"
        "aligned, that driving one channel does not bleed into the "
        "others, and that the camera-trigger counter is producing a "
        "stable TTL clock. If anything in this section fails, every "
        "downstream measurement is suspect because the data and the "
        "command waveforms are not lined up."
    ),
    "Phase B1 — model cell, current clamp": (
        "Switch the amplifier to current clamp (I-clamp) for this "
        "section. We inject known currents into the 500 MΩ model cell "
        "and confirm the recorded voltage matches Ohm's law (ΔV = I·R). "
        "These checks together validate the amplifier's I-clamp gain, "
        "the <code>AO_PA_PER_VOLT</code> command scaling, the "
        "<code>ScAmpOut</code> readout scaling, and the analysis code "
        "that turns a recorded step into an input resistance."
    ),
    "Phase B2 — model cell, voltage clamp": (
        "Switch the amplifier to voltage clamp (V-clamp) before this "
        "section. We apply known voltage steps to the model cell and "
        "check the recorded current (ΔI = ΔV/R), and we fit the "
        "capacitance transient to confirm the cap-comp circuit is "
        "behaving. Validates the V-clamp headstage gain, the "
        "<code>AO_MV_PER_VOLT</code> command scaling, and the "
        "<code>I_mem</code> readout scaling."
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

    # ------------------------------------------------------------------
    # Alignment — Phase A (rig timing)
    # ------------------------------------------------------------------
    "AO → AmpCmd loopback latency": (
        "Plays a short square wave on the command output (<code>ao0</code>) "
        "and records the same signal coming back through the "
        "<code>AmpCmd</code> analog input (ai2), which is wired as a "
        "hardware loopback. Cross-correlation gives the lag between the "
        "two. A few samples of delay is normal — the AO and AI tasks "
        "share a sample clock, so the offset should be sub-millisecond. "
        "Lag &gt; 0.5 ms warns; &gt; 2 ms fails. A large lag means "
        "commanded waveforms in your real recordings will not be "
        "sample-aligned with the data — every downstream stimulus "
        "comparison becomes unreliable."
    ),
    "Inter-channel crosstalk": (
        "Drives <code>ao0</code> hard with a 137 Hz sine while the camera "
        "counter is running, then measures how much of that 137 Hz tone "
        "shows up on the quiet input channels. We use a narrowband DFT "
        "and subtract a nearby empty bin (147 Hz) so broadband noise "
        "doesn't get counted as crosstalk. Pass/warn/fail is judged on "
        "<b>TTLLoopback only</b> (a differential channel, trustworthy): "
        "&gt; 10 mV warns, &gt; 50 mV fails. The Camera channel is also "
        "reported but never flagged — it sits next to <code>AmpCmd</code> "
        "in the DAQ's MUX scan order and picks up a known sample-and-hold "
        "settling artifact when ai2 is driven full-swing, which does not "
        "happen in normal recording."
    ),
    "Counter TTL period stability": (
        "The on-board counter drives the camera trigger line (PFI12) at "
        "a commanded rate (default 100 Hz); that line also feeds back "
        "into <code>TTLLoopback</code> on the AI side. We acquire 2 s of "
        "quiet AI, find rising edges, and measure the inter-edge intervals. "
        "Catches a misrouted counter terminal, a free-running counter "
        "that doesn't match the commanded rate, or excess jitter. Warns "
        "if the observed rate is &gt; 0.5 % off the commanded rate, or "
        "if period jitter is &gt; 0.1 % of the period. A flat loopback "
        "(no edges) means the counter never started — usually the "
        "<code>co_pulse_term</code> terminal isn't set."
    ),

    # ------------------------------------------------------------------
    # Alignment — Phase B1 (model cell, current clamp)
    # ------------------------------------------------------------------
    "Model-cell resting baseline (I=0)": (
        "With the command output idle and the 500 MΩ model cell patched "
        "in, the <code>ScAmpOut</code> readout should sit at ~0 mV — a "
        "pure resistor has no resting potential. We report the median "
        "voltage over 1 s. Anything &gt; 10 mV warns and points to an "
        "amplifier DC offset, a bad reference electrode, or a stale "
        "holding-current setting on the front panel that wasn't zeroed."
    ),
    "Model-cell amplifier noise floor": (
        "RMS noise on <code>ScAmpOut</code> (mV) and <code>RawAmpOut</code> "
        "(pA) with the command idle, plus the fraction of total signal "
        "power concentrated in the 60/120/180 Hz mains-harmonic bands "
        "(via Welch PSD). Line-noise fraction &gt; 10 % warns, &gt; 30 % "
        "fails — at that level mains hum dominates the recording. If it "
        "trips, check the Faraday cage door, ground straps, and that no "
        "new mains-powered equipment has been added near the rig."
    ),
    "CC scaling & linearity (model cell)": (
        "Injects a current staircase (default −100 → +100 pA in 20 pA "
        "steps) and measures the steady-state voltage deflection on "
        "<code>ScAmpOut</code> at each step. With the 500 MΩ model cell, "
        "Ohm's law predicts ΔV = I·R, so a +100 pA step should give a "
        "+50 mV deflection. We fit a line through the (I, ΔV) points; "
        "the slope is the implied resistance in MΩ. Warns if any single "
        "step deviates &gt; 5 % from 500 MΩ, if the fitted slope deviates "
        "&gt; 5 %, or if R² &lt; 0.999. Catches a wrong "
        "<code>AO_PA_PER_VOLT</code> constant in <code>config.py</code>, "
        "a clipped DAQ output, or amplifier gain drift."
    ),
    "Analysis pipeline self-test (compute_input_resistance)": (
        "End-to-end check of the analysis code, not the rig: records a "
        "single hyperpolarising current pulse on the model cell, then "
        "feeds the resulting trace into the production "
        "<code>compute_input_resistance</code> function used by the rest "
        "of the analysis pipeline. The answer should be within 5 % of "
        "500 MΩ. Warns otherwise. A failure here without other Phase B1 "
        "failures is almost always a regression in the analysis code "
        "(a unit conversion bug, a baseline-window change), not an "
        "amplifier problem."
    ),

    # ------------------------------------------------------------------
    # Alignment — Phase B2 (model cell, voltage clamp)
    # ------------------------------------------------------------------
    "VC scaling (model cell)": (
        "Applies a set of voltage steps (default ±10, ±20, ±50 mV) and "
        "measures the resulting current on <code>I_mem</code>. With the "
        "500 MΩ model cell, ΔI = ΔV / R, so a 50 mV step should produce "
        "a 100 pA current. We fit a line through the (V, ΔI) points and "
        "report the implied resistance from the slope. Warns if any "
        "single step deviates &gt; 5 % from 500 MΩ. Catches a wrong "
        "<code>AO_MV_PER_VOLT</code> constant, a miscalibrated V-clamp "
        "headstage gain, or the amplifier still being in current clamp."
    ),
    "Capacitance transient τ (VC step)": (
        "Fits a single exponential (I(t) = I_ss + ΔI·exp(−t/τ)) to the "
        "onset transient of <code>I_mem</code> after a small VC step. "
        "With amplifier capacitance compensation <i>off</i>, τ ≈ R·C of "
        "the model cell — for the Patch-1U at CELL (500 MΩ × 33 pF) "
        "this is around 16 ms. With cap-comp <i>on</i>, τ should "
        "collapse to a fraction of a millisecond. Reported for "
        "diagnostic transparency only — this check never fails — but a "
        "consistent shift in τ between weeks is a great early warning "
        "that the cap-comp circuit is drifting."
    ),
}


def describe_check(name: str) -> str:
    """Return the long description for a check name, or empty string."""
    return CHECK_DESCRIPTIONS.get(name, "")


def describe_section(name: str) -> str:
    """Return the long description for a section name, or empty string."""
    return SECTION_DESCRIPTIONS.get(name, "")

"""
Commanded vs. recorded stimulus comparison.

The ``AmpCmd`` analog input (ai2) is a loopback of the amplifier command
signal — effectively the raw Volts written to ao0 after passing through the
amplifier front-end.  For trial mode we can regenerate the expected ao0
waveform from the saved protocol JSON and compare it sample-for-sample
against the recorded AmpCmd trace.

Metrics per trial:
    rmse_v            — root-mean-square error between expected and recorded, in V
    peak_error_v      — largest absolute sample-wise error in V
    timing_offset_ms  — cross-correlation lag (expected → recorded), in ms

The overall check WARNs if any trial's RMSE exceeds a permissive threshold
(5 % of the commanded peak amplitude, or 50 mV absolute noise floor) and
FAILs on catastrophic mismatch (50 %+ RMSE).
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
from scipy.signal import correlate

from analysis.qc import Check, Status
from acquisition.trial_protocol import protocol_from_dict
from acquisition.trial_waveforms import build_trial_waveform


RMSE_WARN_FRAC: float = 0.05        # 5 % of commanded peak
RMSE_FAIL_FRAC: float = 0.50        # 50 % of commanded peak
RMSE_FLOOR_V:   float = 0.050       # 50 mV floor (noise dominates small cmds)
LAG_WARN_MS:    float = 1.0
LAG_FAIL_MS:    float = 5.0


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------

def run_all(bundle: dict[str, Any]) -> list[Check]:
    """Run every commanded-vs-recorded check applicable to the given recording."""
    if bundle["recording_mode"] == "trial":
        return [check_trial_commands(bundle)]
    return [check_continuous_events(bundle)]


# ----------------------------------------------------------------------------
# Trial mode
# ----------------------------------------------------------------------------

def check_trial_commands(bundle: dict[str, Any]) -> Check:
    """Compare the regenerated ao0 waveform to recorded AmpCmd for every trial."""
    name = "Commanded vs. recorded stimulus (per trial)"
    try:
        amp_idx = _channel_index(bundle, "AmpCmd")
        if amp_idx is None:
            return Check(name, Status.SKIP, "no AmpCmd channel in recording")

        protocol_json = bundle.get("protocol_json") or ""
        if not protocol_json:
            return Check(name, Status.SKIP, "no protocol JSON stored in HDF5")
        try:
            protocol = protocol_from_dict(json.loads(protocol_json))
        except Exception as exc:
            return Check(name, Status.FAIL, f"could not parse protocol JSON: {exc}")

        stim_by_name = {s.name: s for s in protocol.stimuli}
        sr = int(bundle["sample_rate"])
        per_trial: list[dict[str, Any]] = []
        worst_status = Status.PASS
        worst_msg = ""

        for t in bundle["trials"]:
            ti = int(t["trial_index"])
            stim_name = t["stimulus_name"]
            stim_def = stim_by_name.get(stim_name)
            if stim_def is None:
                per_trial.append({"trial": ti, "status": "skip",
                                  "reason": f"no stim def named '{stim_name}'"})
                continue

            expected = build_trial_waveform(stim_def, protocol)
            recorded = np.asarray(t["data"][amp_idx], dtype=float)

            # Length mismatch (trial window mis-sized): compare the overlap
            n = min(expected.size, recorded.size)
            if n == 0:
                per_trial.append({"trial": ti, "status": "skip",
                                  "reason": "zero-length trial"})
                continue
            exp = expected[:n]
            rec = recorded[:n]

            peak_v = float(np.max(np.abs(exp)))
            err = rec - exp
            rmse = float(np.sqrt(np.mean(err**2)))
            peak_err = float(np.max(np.abs(err)))
            lag_ms = _xcorr_lag_ms(exp, rec, sr)

            # Threshold relative to peak commanded amplitude, with a noise floor
            rmse_limit_warn = max(peak_v * RMSE_WARN_FRAC, RMSE_FLOOR_V)
            rmse_limit_fail = max(peak_v * RMSE_FAIL_FRAC, RMSE_FLOOR_V * 5)
            if rmse > rmse_limit_fail and worst_status != Status.FAIL:
                worst_status = Status.FAIL
                worst_msg = (f"trial {ti}: RMSE {rmse*1000:.1f} mV "
                             f"(limit {rmse_limit_fail*1000:.1f})")
            elif rmse > rmse_limit_warn and worst_status == Status.PASS:
                worst_status = Status.WARN
                worst_msg = (f"trial {ti}: RMSE {rmse*1000:.1f} mV "
                             f"(limit {rmse_limit_warn*1000:.1f})")
            if abs(lag_ms) > LAG_FAIL_MS and worst_status != Status.FAIL:
                worst_status = Status.FAIL
                worst_msg = f"trial {ti}: timing offset {lag_ms:.1f} ms"
            elif abs(lag_ms) > LAG_WARN_MS and worst_status == Status.PASS:
                worst_status = Status.WARN
                worst_msg = f"trial {ti}: timing offset {lag_ms:.1f} ms"

            per_trial.append({
                "trial":            ti,
                "stimulus":         stim_name,
                "n_samples":        n,
                "peak_cmd_v":       peak_v,
                "rmse_v":           rmse,
                "peak_error_v":     peak_err,
                "timing_offset_ms": lag_ms,
                "status":           "ok",
            })

        metrics = {
            "clamp_mode":   bundle.get("clamp_mode", ""),
            "n_trials":     len(per_trial),
            "per_trial":    per_trial,
        }
        if not per_trial:
            return Check(name, Status.SKIP, "no trials to compare", metrics)
        if worst_status == Status.PASS:
            rmses = [p["rmse_v"] for p in per_trial if p.get("status") == "ok"]
            avg = float(np.mean(rmses)) if rmses else 0.0
            return Check(name, Status.PASS,
                         f"all {len(rmses)} trials within bounds "
                         f"(avg RMSE {avg*1000:.2f} mV)", metrics)
        return Check(name, worst_status, worst_msg, metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


# ----------------------------------------------------------------------------
# Continuous mode
# ----------------------------------------------------------------------------

def check_continuous_events(bundle: dict[str, Any]) -> Check:
    """Sanity check AmpCmd agrees with apply/clear intervals in continuous mode.

    Without per-stimulus parameters in the event table we can only verify that
    AmpCmd is quiet outside of active windows and non-zero (on average) inside
    them.  Skips if the recording has no events.
    """
    name = "Commanded vs. recorded stimulus (continuous)"
    events = bundle.get("stimulus_events") or []
    if not events:
        return Check(name, Status.SKIP, "no stimulus events in continuous recording")
    try:
        amp_idx = _channel_index(bundle, "AmpCmd")
        if amp_idx is None:
            return Check(name, Status.SKIP, "no AmpCmd channel in recording")
        amp = np.asarray(bundle["data"][amp_idx], dtype=float)
        n_samples = amp.size

        active = np.zeros(n_samples, dtype=bool)
        open_at: dict[int, int] = {}
        windows: list[tuple[int, int, str]] = []
        for e in events:
            si = int(e["stimulus_index"])
            idx = int(e["sample_index"])
            if e["event_type"] == "apply":
                open_at[si] = idx
            elif e["event_type"] == "clear" and si in open_at:
                start = open_at.pop(si)
                stop = min(idx, n_samples)
                if stop > start:
                    active[start:stop] = True
                    windows.append((start, stop, e.get("stimulus_name", "")))
        for si, start in open_at.items():
            active[start:n_samples] = True
            windows.append((start, n_samples, f"open_{si}"))

        active_rms   = float(np.sqrt(np.mean(amp[active]**2))) if active.any() else 0.0
        inactive_rms = float(np.sqrt(np.mean(amp[~active]**2))) if (~active).any() else 0.0

        # Convert to display units using the AmpCmd channel's own scale/units
        try:
            scale = float(bundle["display_scales"][amp_idx])
            unit  = str(bundle["units"][amp_idx])
        except (IndexError, KeyError, TypeError):
            scale, unit = 1.0, "V"
        active_display   = active_rms   * scale
        inactive_display = inactive_rms * scale

        metrics = {
            "n_events":              len(events),
            "n_windows":              len(windows),
            "active_rms_v":           active_rms,
            "inactive_rms_v":         inactive_rms,
            "active_rms_display":     active_display,
            "inactive_rms_display":   inactive_display,
            "display_units":          unit,
        }

        # 0.25 V threshold — typical holding currents sit around 0.1–0.2 V
        # loopback and don't need to trip a warning.
        if inactive_rms > 0.25:
            return Check(name, Status.WARN,
                         f"AmpCmd RMS outside stimulus windows = "
                         f"{inactive_rms:.3f} V ({inactive_display:.1f} {unit}) — "
                         f"check for an untracked holding current",
                         metrics)
        if windows and active_rms < 0.01:
            return Check(name, Status.WARN,
                         f"AmpCmd RMS inside stimulus windows = "
                         f"{active_rms:.3f} V ({active_display:.1f} {unit}) — "
                         "loopback quiet, is ao0 wired?",
                         metrics)
        return Check(name, Status.PASS,
                     f"AmpCmd active RMS {active_display:.1f} {unit}, "
                     f"inactive RMS {inactive_display:.1f} {unit}",
                     metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _channel_index(bundle: dict[str, Any], name: str) -> int | None:
    try:
        return bundle["channel_names"].index(name)
    except ValueError:
        return None


def _xcorr_lag_ms(expected: np.ndarray, recorded: np.ndarray, sr: int) -> float:
    """Cross-correlation lag of ``recorded`` relative to ``expected``, in ms.

    Positive values mean the recorded trace trails the expected.  Returns 0
    for flat/zero inputs where correlation is ill-defined.
    """
    if expected.size == 0 or recorded.size == 0:
        return 0.0
    exp = expected - float(np.mean(expected))
    rec = recorded - float(np.mean(recorded))
    if float(np.max(np.abs(exp))) < 1e-9 or float(np.max(np.abs(rec))) < 1e-9:
        return 0.0
    # Cap at 1e5 samples (5 s at 20 kHz) for speed.
    cap = min(exp.size, 100_000)
    exp = exp[:cap]
    rec = rec[:cap]
    full = correlate(rec, exp, mode="full")
    lags = np.arange(-exp.size + 1, rec.size)
    peak = int(np.argmax(np.abs(full)))
    return float(lags[peak]) / sr * 1000.0

"""
Per-channel signal sanity checks.

All operate on the raw-Volts array stored in ``/data/analog_input``.  Where a
metric is more meaningful in display units (mV, pA, nA), we multiply by the
per-channel ``display_scale`` before thresholding.

Thresholds are intentionally permissive — these are meant to catch obvious
problems (saturated channel, 60 Hz hum, DC railed), not to be a substitute
for thoughtful review of the recording.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.signal import welch

from analysis.qc import Check, Status


DAQ_RAIL_V: float = 10.0
SATURATION_BAND_V: float = 0.05          # within 0.5 % of ±10 V rail
SATURATION_FRACTION_WARN: float = 1e-4   # 0.01 %
SATURATION_FRACTION_FAIL: float = 1e-3   # 0.1 %

LINE_FREQS_HZ: tuple[float, ...] = (60.0, 120.0, 180.0)
LINE_BAND_HZ: float = 2.0                # ±2 Hz around each harmonic
LINE_FRACTION_WARN: float = 0.10
LINE_FRACTION_FAIL: float = 0.30

SIGNAL_CHANNELS: tuple[str, ...] = (
    "ScAmpOut", "RawAmpOut", "I_mem", "V_pip",
)


# ----------------------------------------------------------------------------
# Public entry points
# ----------------------------------------------------------------------------

def run_all(bundle: dict[str, Any]) -> list[Check]:
    """Run every signal-sanity check applicable to the given recording."""
    return [
        check_saturation(bundle),
        check_dc_offset(bundle),
        check_baseline_rms(bundle),
        check_line_noise(bundle),
        check_baseline_drift(bundle),
    ]


# ----------------------------------------------------------------------------
# Individual checks
# ----------------------------------------------------------------------------

def check_saturation(bundle: dict[str, Any]) -> Check:
    """Flag channels that spend time within ±(rail − band) of the DAQ rails."""
    name = "Saturation (±10 V rails)"
    try:
        per_channel: dict[str, Any] = {}
        worst_status = Status.PASS
        worst_msg = ""

        for ch_name, row in _each_channel(bundle):
            n = row.size
            if n == 0:
                continue
            lo = float(np.min(row))
            hi = float(np.max(row))
            near_rail = np.count_nonzero(
                (row >= DAQ_RAIL_V - SATURATION_BAND_V)
                | (row <= -DAQ_RAIL_V + SATURATION_BAND_V)
            )
            frac = float(near_rail / n)
            per_channel[ch_name] = {
                "min_v": lo, "max_v": hi,
                "saturated_samples": int(near_rail),
                "saturated_fraction": frac,
            }
            if frac >= SATURATION_FRACTION_FAIL and worst_status != Status.FAIL:
                worst_status = Status.FAIL
                worst_msg = f"{ch_name}: {frac:.2%} of samples near ±10 V rail"
            elif frac >= SATURATION_FRACTION_WARN and worst_status == Status.PASS:
                worst_status = Status.WARN
                worst_msg = f"{ch_name}: {frac:.2%} of samples near ±10 V rail"

        metrics = {"per_channel": per_channel}
        if worst_status == Status.PASS:
            return Check(name, Status.PASS, "no channel near DAQ rails", metrics)
        return Check(name, worst_status, worst_msg, metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_dc_offset(bundle: dict[str, Any]) -> Check:
    """Mean value per channel, in display units.  Report-only (no threshold)."""
    name = "DC offset"
    try:
        scales = np.asarray(bundle["display_scales"], dtype=float)
        units = list(bundle["units"])
        names = list(bundle["channel_names"])
        per_channel: dict[str, Any] = {}

        for i, ch_name in enumerate(names):
            row = _concat_channel(bundle, i)
            if row.size == 0:
                continue
            mean_v = float(np.mean(row))
            per_channel[ch_name] = {
                "mean_v": mean_v,
                "mean_display": mean_v * float(scales[i]),
                "units": units[i] if i < len(units) else "",
            }

        return Check(name, Status.PASS,
                     "per-channel DC offset reported (informational)",
                     {"per_channel": per_channel})
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_baseline_rms(bundle: dict[str, Any]) -> Check:
    """RMS noise on a pre-stimulus baseline window, per signal channel."""
    name = "Baseline RMS noise"
    try:
        start, stop = _baseline_window(bundle)
        if start is None or stop is None or stop - start < 1000:
            return Check(name, Status.SKIP,
                         "no usable pre-stimulus baseline window",
                         {"baseline_samples": 0})

        scales = np.asarray(bundle["display_scales"], dtype=float)
        units = list(bundle["units"])
        per_channel: dict[str, Any] = {}

        for i, ch_name in enumerate(bundle["channel_names"]):
            if ch_name not in SIGNAL_CHANNELS:
                continue
            segment = _concat_channel(bundle, i)[start:stop]
            if segment.size == 0:
                continue
            centered = segment - float(np.mean(segment))
            rms_v = float(np.sqrt(np.mean(centered**2)))
            per_channel[ch_name] = {
                "rms_v":       rms_v,
                "rms_display": rms_v * float(scales[i]),
                "units":       units[i] if i < len(units) else "",
            }

        metrics = {
            "baseline_start": int(start),
            "baseline_stop":  int(stop),
            "per_channel":    per_channel,
        }
        if not per_channel:
            return Check(name, Status.SKIP, "no signal channel present", metrics)
        return Check(name, Status.PASS,
                     f"baseline RMS computed on {stop - start:,} samples",
                     metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_line_noise(bundle: dict[str, Any]) -> Check:
    """Welch PSD ratio of 60/120/180 Hz power to total broadband power."""
    name = "Line noise (60 Hz + harmonics)"
    try:
        sr = int(bundle["sample_rate"])
        per_channel: dict[str, Any] = {}
        worst_status = Status.PASS
        worst_msg = ""

        for i, ch_name in enumerate(bundle["channel_names"]):
            if ch_name not in SIGNAL_CHANNELS:
                continue
            segment = _decimate_for_psd(_concat_channel(bundle, i), sr)
            if segment.size < sr:
                continue
            nperseg = min(segment.size, sr)
            freqs, psd = welch(segment, fs=sr, nperseg=nperseg)
            total = float(np.trapezoid(psd, freqs))
            if total <= 0:
                continue
            line_power = 0.0
            per_harmonic: dict[str, float] = {}
            for f0 in LINE_FREQS_HZ:
                band = (freqs >= f0 - LINE_BAND_HZ) & (freqs <= f0 + LINE_BAND_HZ)
                if not np.any(band):
                    continue
                p = float(np.trapezoid(psd[band], freqs[band]))
                per_harmonic[f"{int(f0)}Hz"] = p / total
                line_power += p
            fraction = line_power / total
            per_channel[ch_name] = {
                "line_fraction": fraction,
                "per_harmonic":  per_harmonic,
            }
            if fraction >= LINE_FRACTION_FAIL and worst_status != Status.FAIL:
                worst_status = Status.FAIL
                worst_msg = f"{ch_name}: {fraction:.1%} line-noise fraction"
            elif fraction >= LINE_FRACTION_WARN and worst_status == Status.PASS:
                worst_status = Status.WARN
                worst_msg = f"{ch_name}: {fraction:.1%} line-noise fraction"

        metrics = {"per_channel": per_channel}
        if not per_channel:
            return Check(name, Status.SKIP,
                         "no signal channel available for PSD", metrics)
        if worst_status == Status.PASS:
            return Check(name, Status.PASS,
                         "line-noise fraction within bounds on all channels",
                         metrics)
        return Check(name, worst_status, worst_msg, metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_baseline_drift(bundle: dict[str, Any]) -> Check:
    """Slope of a linear fit over the full recording, per signal channel."""
    name = "Baseline drift"
    try:
        sr = int(bundle["sample_rate"])
        scales = np.asarray(bundle["display_scales"], dtype=float)
        units = list(bundle["units"])
        per_channel: dict[str, Any] = {}

        for i, ch_name in enumerate(bundle["channel_names"]):
            if ch_name not in SIGNAL_CHANNELS:
                continue
            row = _concat_channel(bundle, i)
            if row.size < sr:
                continue
            # Decimate to at most ~20 k points for speed.
            step = max(1, row.size // 20_000)
            y = row[::step]
            x = np.arange(y.size, dtype=float) * step / sr  # seconds
            slope_v_per_s, intercept = np.polyfit(x, y, 1)
            y_fit = slope_v_per_s * x + intercept
            residual = float(np.std(y - y_fit))
            per_channel[ch_name] = {
                "slope_v_per_s":        float(slope_v_per_s),
                "slope_display_per_s":  float(slope_v_per_s) * float(scales[i]),
                "total_change_display":
                    float(slope_v_per_s) * float(scales[i]) * (row.size / sr),
                "residual_std_v":       residual,
                "units":                units[i] if i < len(units) else "",
            }

        metrics = {"per_channel": per_channel}
        if not per_channel:
            return Check(name, Status.SKIP, "no signal channel present", metrics)
        return Check(name, Status.PASS,
                     "per-channel drift reported (informational)", metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _each_channel(bundle: dict[str, Any]):
    """Yield (channel_name, full-trace-in-volts) across continuous or trial."""
    for i, ch_name in enumerate(bundle["channel_names"]):
        yield ch_name, _concat_channel(bundle, i)


def _concat_channel(bundle: dict[str, Any], ch_index: int) -> np.ndarray:
    if bundle["recording_mode"] == "continuous":
        return np.asarray(bundle["data"][ch_index], dtype=float)
    parts = [np.asarray(t["data"][ch_index], dtype=float) for t in bundle["trials"]]
    if not parts:
        return np.empty(0, dtype=float)
    return np.concatenate(parts)


def _baseline_window(bundle: dict[str, Any]) -> tuple[int | None, int | None]:
    """Return (start, stop) sample indices of a pre-stimulus baseline.

    Continuous mode: [0, first stimulus_events apply) or [0, full length) if no
    events.  Trial mode: first trial, [0, first pulse edge on AmpCmd) or the
    first 10 % of the trial as fallback.
    """
    if bundle["recording_mode"] == "continuous":
        n = int(bundle["data"].shape[1])
        events = bundle.get("stimulus_events") or []
        applies = [int(e["sample_index"]) for e in events if e.get("event_type") == "apply"]
        if applies:
            return 0, max(0, min(applies) - 1)
        # No stimulus: use the whole recording, cap at 60 s to keep it cheap.
        cap = int(bundle["sample_rate"]) * 60
        return 0, min(n, cap)

    trials = bundle.get("trials") or []
    if not trials:
        return None, None
    first = trials[0]["data"]
    # Use the first 10 % of the first trial as a proxy baseline.
    return 0, max(1, int(first.shape[1] * 0.1))


def _decimate_for_psd(x: np.ndarray, sr: int, max_seconds: float = 10.0) -> np.ndarray:
    """Trim the signal to at most ``max_seconds`` for Welch PSD."""
    max_samples = int(max_seconds * sr)
    if x.size <= max_samples:
        return x
    return x[:max_samples]

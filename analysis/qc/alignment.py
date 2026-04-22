"""
Standalone hardware alignment check.

This module drives the NI DAQ directly (independent of the GUI's
:class:`~hardware.daq_worker.DAQWorker`) using one-shot finite-sample
tasks.  Intended to be run manually from the CLI
(:mod:`analysis.qc_alignment`) on a weekly cadence or after any rig
change, with the Axon Instruments Patch-1U model cell in ``CELL`` mode
(500 MΩ) patched in place of a real pipette.

Organisation
------------
Phase A checks need only the rig itself — no model cell or amplifier
assumptions:

* :func:`check_ao_ai_latency`       — AO0 → AmpCmd loopback delay
* :func:`check_inter_channel_crosstalk` — drive AO0, measure quiet channels
* :func:`check_ttl_period_stability` — counter TTL clock drift

Phase B checks use the 500 MΩ model cell as ground truth:

* :func:`check_cc_scaling_and_linearity` — ΔV = I · R across a staircase
* :func:`check_vc_scaling`          — ΔI = ΔV / R across voltage steps
* :func:`check_resting_baseline`    — zero-stim DC offset on ScAmpOut
* :func:`check_noise_floor`         — zero-stim RMS + line-noise fraction
* :func:`check_capacitance_tau`     — exponential τ fit of step onset
* :func:`check_analysis_pipeline_self_test` — feed a CC recording through
  :func:`analysis.analyze_steps.compute_input_resistance`

Each check returns an :class:`~analysis.qc.Check` object.  All checks
catch their own exceptions and downgrade to FAIL, so the orchestrator
always produces a report.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import curve_fit

from analysis.qc import Check, Status, worst
from config import (
    AI_CHANNELS,
    AI_CHANNELS_VC,
    AO_COMMAND_CH,
    AO_MV_PER_VOLT,
    AO_PA_PER_VOLT,
    DEVICE_NAME,
    N_AI_CHANNELS,
    SAMPLE_RATE,
)


# ============================================================================
# One-shot DAQ acquisition helpers
# ============================================================================

@dataclass
class AcquisitionResult:
    """Bundle returned by :func:`_acquire_one_shot`."""

    data: np.ndarray          # shape (N_AI_CHANNELS, N) in raw Volts
    sample_rate: int
    channel_names: list[str]
    display_scales: np.ndarray
    units: list[str]


def _build_ai_finite(n_samples: int, channels) -> "nidaqmx.Task":
    """Build a one-shot (finite) analog-input task of length n_samples."""
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, TerminalConfiguration

    term_map = {
        "differential": TerminalConfiguration.DIFF,
        "rse":          TerminalConfiguration.RSE,
    }
    task = nidaqmx.Task("qc_align_ai")
    for name, ch, term_cfg, scale, units in channels:
        task.ai_channels.add_ai_voltage_chan(
            f"{DEVICE_NAME}/{ch}",
            terminal_config=term_map[term_cfg],
            min_val=-10.0, max_val=10.0,
        )
    task.timing.cfg_samp_clk_timing(
        rate=SAMPLE_RATE,
        sample_mode=AcquisitionType.FINITE,
        samps_per_chan=n_samples,
    )
    return task


def _build_ao_finite(n_samples: int) -> "nidaqmx.Task":
    """Build a one-shot (finite) analog-output task, slaved to the AI clock."""
    import nidaqmx
    from nidaqmx.constants import AcquisitionType

    task = nidaqmx.Task("qc_align_ao")
    task.ao_channels.add_ao_voltage_chan(
        f"{DEVICE_NAME}/{AO_COMMAND_CH}",
        min_val=-10.0, max_val=10.0,
    )
    task.timing.cfg_samp_clk_timing(
        rate=SAMPLE_RATE,
        source=f"/{DEVICE_NAME}/ai/SampleClock",
        sample_mode=AcquisitionType.FINITE,
        samps_per_chan=n_samples,
    )
    return task


def _acquire_one_shot(
    ao_waveform_v: np.ndarray,
    clamp_mode: str = "current_clamp",
    trail_samples: int = 0,
) -> AcquisitionResult:
    """Synchronously drive ao0 and capture all 5 AI channels.

    Builds finite AI and AO tasks, writes the waveform, starts AO then AI,
    waits for completion, reads the full buffer, and closes both tasks.

    Args:
        ao_waveform_v: 1-D array of AO0 voltages to output.
        clamp_mode: ``"current_clamp"`` or ``"voltage_clamp"`` — selects
            channel definitions for the AI task.
        trail_samples: Extra quiet samples acquired after the waveform
            ends.  Useful to capture post-step settling on AI.

    Returns:
        :class:`AcquisitionResult` containing the raw data and metadata.
    """
    import nidaqmx
    from nidaqmx.stream_readers import AnalogMultiChannelReader
    from nidaqmx.stream_writers import AnalogMultiChannelWriter

    ao_waveform_v = np.asarray(ao_waveform_v, dtype=np.float64)
    n_wf = int(ao_waveform_v.size)
    n_total = n_wf + int(trail_samples)
    channels = AI_CHANNELS_VC if clamp_mode == "voltage_clamp" else AI_CHANNELS

    # Pad AO with zeros for the trail so AO+AI have identical lengths.
    ao_out = np.zeros(n_total, dtype=np.float64)
    ao_out[:n_wf] = ao_waveform_v

    ai_task = _build_ai_finite(n_total, channels)
    ao_task = _build_ao_finite(n_total)
    try:
        writer = AnalogMultiChannelWriter(ao_task.out_stream)
        writer.write_many_sample(ao_out.reshape(1, n_total))

        reader = AnalogMultiChannelReader(ai_task.in_stream)
        buf = np.zeros((N_AI_CHANNELS, n_total), dtype=np.float64)

        # Start AO first (it waits on the AI clock), then start AI.
        ao_task.start()
        ai_task.start()

        timeout = max(5.0, 2.0 + n_total / SAMPLE_RATE)
        ai_task.wait_until_done(timeout=timeout)
        reader.read_many_sample(buf, n_total, timeout=timeout)
    finally:
        for t in (ao_task, ai_task):
            try:
                t.stop()
                t.close()
            except Exception:
                pass

    return AcquisitionResult(
        data           = buf,
        sample_rate    = int(SAMPLE_RATE),
        channel_names  = [c[0] for c in channels],
        display_scales = np.asarray([c[3] for c in channels], dtype=float),
        units          = [c[4] for c in channels],
    )


# ============================================================================
# Phase A — rig-only checks
# ============================================================================

def check_ao_ai_latency() -> Check:
    """Measure AO → AmpCmd loopback delay via cross-correlation.

    Writes a short square wave to ao0 and looks at the ai2 loopback.
    The amplifier/DAQ loopback path is a handful of samples end-to-end;
    anything more than ~1 ms of lag is suspicious.
    """
    name = "AO → AmpCmd loopback latency"
    try:
        sr = int(SAMPLE_RATE)
        # 200 ms square wave at 50 Hz, 0.5 V peak
        n = int(0.2 * sr)
        t = np.arange(n) / sr
        waveform = 0.5 * np.sign(np.sin(2 * np.pi * 50 * t))
        res = _acquire_one_shot(waveform, clamp_mode="current_clamp", trail_samples=0)

        amp_idx = res.channel_names.index("AmpCmd")
        rec = res.data[amp_idx]
        exp = waveform
        # Zero-mean for xcorr
        rec = rec - float(np.mean(rec))
        exp = exp - float(np.mean(exp))
        from scipy.signal import correlate
        corr = correlate(rec, exp, mode="full")
        lags = np.arange(-len(exp) + 1, len(rec))
        lag = int(lags[np.argmax(np.abs(corr))])
        lag_ms = lag / sr * 1000.0

        metrics = {"lag_samples": lag, "lag_ms": lag_ms}
        if abs(lag_ms) > 2.0:
            return Check(name, Status.FAIL,
                         f"loopback lag {lag_ms:.2f} ms (>2 ms fails)", metrics)
        if abs(lag_ms) > 0.5:
            return Check(name, Status.WARN,
                         f"loopback lag {lag_ms:.2f} ms (>0.5 ms warns)", metrics)
        return Check(name, Status.PASS, f"loopback lag {lag_ms:.2f} ms", metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_inter_channel_crosstalk() -> Check:
    """Drive AO0 hard and measure narrowband coupling onto the Camera/TTL channels.

    Mechanics:
    - TTL counter runs during the test so PFI12 is actively driven (not
      floating); floating RSE inputs give false-positive pickup.
    - Drive frequency (137 Hz) is offset from the 100 Hz TTL rate and the
      60 Hz mains so their harmonics don't overlap the drive bin.
    - Amplitude is extracted with a Hann-windowed single-frequency DFT
      (Goertzel-style), which suppresses spectral leakage from TTL content
      by ~30 dB versus a rectangular window.
    - A background bin at 147 Hz is measured too.  The reported "coupled"
      value is sqrt(drive_bin² − background_bin²), so residual broadband
      noise in the bin isn't counted as crosstalk.

    Pass/warn/fail is driven by **TTLLoopback only** (ai4, differential).
    Camera (ai3, RSE) sits directly adjacent to AmpCmd (ai2) in the MUX
    scan; ai2 carries a full-swing loopback of AO0 during this test, so
    ai3 picks up a settling-time "ghost" of the drive through the
    sample-and-hold amp.  That artifact is real DAQ behaviour but not an
    actionable rig problem — in normal recording ai2 carries the amp
    command (few hundred mV, not 5 V) and ai3 is overwhelmed by the PFI12
    TTL pulses used for frame detection.  We report Camera for diagnostic
    transparency but do not flag on it.
    """
    name = "Inter-channel crosstalk"
    try:
        from hardware.daq_config import build_ttl_counter_task

        sr = int(SAMPLE_RATE)
        n = int(0.5 * sr)  # 0.5 s
        drive_hz = 137.0       # offset from TTL fundamental/harmonics and mains
        background_hz = 147.0  # nearby bin, expected empty → noise floor
        t = np.arange(n) / sr
        waveform = 5.0 * np.sin(2 * np.pi * drive_hz * t)

        ctr_task = build_ttl_counter_task(frame_rate_hz=100.0, exposure_ms=2.0)
        ctr_task.start()
        try:
            res = _acquire_one_shot(waveform, clamp_mode="current_clamp")
        finally:
            try:
                ctr_task.stop()
                ctr_task.close()
            except Exception:
                pass

        hann = np.hanning(n)
        coh_gain = float(np.mean(hann))  # coherent-gain correction for Hann

        def _narrowband_rms(sig: np.ndarray, freq_hz: float) -> float:
            k = np.exp(-1j * 2 * np.pi * freq_hz * np.arange(n) / sr)
            amp_peak = 2.0 * np.abs(np.mean(sig * hann * k)) / coh_gain
            return float(amp_peak / np.sqrt(2))

        def _coupled_rms(sig: np.ndarray) -> tuple[float, float, float]:
            drive = _narrowband_rms(sig, drive_hz)
            bg    = _narrowband_rms(sig, background_hz)
            coupled = float(np.sqrt(max(0.0, drive * drive - bg * bg)))
            return drive, bg, coupled

        cam_idx = res.channel_names.index("Camera")
        ttl_idx = res.channel_names.index("TTLLoopback")
        cam_drive, cam_bg, cam_coupled = _coupled_rms(res.data[cam_idx])
        ttl_drive, ttl_bg, ttl_coupled = _coupled_rms(res.data[ttl_idx])

        metrics = {
            "drive_channel":      AO_COMMAND_CH,
            "drive_freq_hz":      drive_hz,
            "background_freq_hz": background_hz,
            "drive_rms_v":        float(np.sqrt(np.mean(waveform**2))),
            "camera": {
                "drive_bin_v":      cam_drive,
                "background_bin_v": cam_bg,
                "coupled_v":        cam_coupled,
            },
            "ttl": {
                "drive_bin_v":      ttl_drive,
                "background_bin_v": ttl_bg,
                "coupled_v":        ttl_coupled,
            },
        }
        # Pass/warn/fail thresholds use TTLLoopback only (differential —
        # trustworthy). Camera is reported but not flagged (RSE adjacent
        # to the AO loopback → known MUX-settling artifact).
        detail = (f"TTL {ttl_coupled*1000:.2f} mV (bg {ttl_bg*1000:.2f}); "
                  f"Camera {cam_coupled*1000:.1f} mV (bg {cam_bg*1000:.1f}, "
                  f"MUX-settling artifact, not flagged)")
        if ttl_coupled > 0.05:
            return Check(name, Status.FAIL,
                         f"TTL coupled RMS {ttl_coupled*1000:.1f} mV at {drive_hz:.0f} Hz "
                         f"[{detail}]", metrics)
        if ttl_coupled > 0.01:
            return Check(name, Status.WARN,
                         f"TTL coupled RMS {ttl_coupled*1000:.1f} mV at {drive_hz:.0f} Hz "
                         f"[{detail}]", metrics)
        return Check(name, Status.PASS,
                     f"TTL coupled RMS {ttl_coupled*1000:.2f} mV at {drive_hz:.0f} Hz "
                     f"(good isolation) [{detail}]", metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_ttl_period_stability(frame_rate_hz: float = 100.0,
                               duration_s: float = 2.0) -> Check:
    """Starts the counter TTL and verifies the looped-back period is stable.

    Requires the counter task to be driving the camera TTL line at
    ``frame_rate_hz`` with its output landing on the ``TTLLoopback`` AI.
    We read ``duration_s`` of quiet AI (ao0 idle at 0 V) and measure
    rising-edge intervals on the TTLLoopback channel.
    """
    name = "Counter TTL period stability"
    try:
        import nidaqmx
        from nidaqmx.constants import AcquisitionType, Level
        from hardware.daq_config import build_ttl_counter_task

        sr = int(SAMPLE_RATE)
        n = int(duration_s * sr)

        ctr_task = build_ttl_counter_task(frame_rate_hz, exposure_ms=2.0)
        ctr_task.start()
        try:
            res = _acquire_one_shot(np.zeros(n, dtype=np.float64),
                                    clamp_mode="current_clamp")
        finally:
            try:
                ctr_task.stop()
                ctr_task.close()
            except Exception:
                pass

        ttl_idx = res.channel_names.index("TTLLoopback")
        ttl = res.data[ttl_idx]
        # Adaptive midpoint threshold
        lo, hi = float(np.min(ttl)), float(np.max(ttl))
        if hi - lo < 0.5:
            return Check(name, Status.FAIL,
                         f"TTL loopback flat (swing {hi-lo:.2f} V) — counter not running?")
        mid = 0.5 * (lo + hi)
        high = ttl > mid
        edges = np.flatnonzero(~high[:-1] & high[1:])
        if edges.size < 3:
            return Check(name, Status.FAIL,
                         f"only {edges.size} rising edges detected in {duration_s} s")

        periods = np.diff(edges) / sr
        mean_period = float(np.mean(periods))
        std_period  = float(np.std(periods))
        observed_rate = 1.0 / mean_period
        drift_frac = std_period / mean_period

        metrics = {
            "commanded_rate_hz":  float(frame_rate_hz),
            "observed_rate_hz":   observed_rate,
            "n_edges":            int(edges.size),
            "mean_period_s":      mean_period,
            "std_period_s":       std_period,
            "period_jitter_frac": drift_frac,
        }
        if abs(observed_rate - frame_rate_hz) / frame_rate_hz > 0.005:
            return Check(name, Status.WARN,
                         f"observed rate {observed_rate:.2f} Hz vs commanded "
                         f"{frame_rate_hz:.2f} Hz", metrics)
        if drift_frac > 1e-3:
            return Check(name, Status.WARN,
                         f"period jitter {drift_frac*100:.3f}% of period",
                         metrics)
        return Check(name, Status.PASS,
                     f"{observed_rate:.2f} Hz, jitter {drift_frac*100:.4f}%",
                     metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


# ============================================================================
# Phase B — model-cell checks (Axon Patch-1U CELL mode, 500 MΩ)
# ============================================================================

def check_resting_baseline(duration_s: float = 1.0) -> Check:
    """With AO idle, ScAmpOut should read near 0 mV on the 500 MΩ model cell."""
    name = "Model-cell resting baseline (I=0)"
    try:
        sr = int(SAMPLE_RATE)
        n = int(duration_s * sr)
        res = _acquire_one_shot(np.zeros(n), clamp_mode="current_clamp")
        idx = res.channel_names.index("ScAmpOut")
        vm_mv = float(np.median(res.data[idx]) * res.display_scales[idx])
        metrics = {"resting_mV": vm_mv, "duration_s": duration_s}
        if abs(vm_mv) > 10.0:
            return Check(name, Status.WARN,
                         f"idle V_m = {vm_mv:.2f} mV — expected ~0 on model cell",
                         metrics)
        return Check(name, Status.PASS, f"idle V_m = {vm_mv:.2f} mV", metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_noise_floor(duration_s: float = 2.0) -> Check:
    """RMS noise and 60/120/180 Hz line fraction with AO idle."""
    name = "Model-cell amplifier noise floor"
    try:
        from scipy.signal import welch
        sr = int(SAMPLE_RATE)
        n = int(duration_s * sr)
        res = _acquire_one_shot(np.zeros(n), clamp_mode="current_clamp")
        per_channel: dict[str, Any] = {}
        for ch in ("ScAmpOut", "RawAmpOut"):
            i = res.channel_names.index(ch)
            trace = (res.data[i] - float(np.mean(res.data[i]))) * float(res.display_scales[i])
            rms = float(np.sqrt(np.mean(trace**2)))
            nperseg = min(trace.size, sr)
            freqs, psd = welch(trace, fs=sr, nperseg=nperseg)
            total = float(np.trapezoid(psd, freqs))
            line = 0.0
            for f0 in (60.0, 120.0, 180.0):
                band = (freqs >= f0 - 2) & (freqs <= f0 + 2)
                if np.any(band):
                    line += float(np.trapezoid(psd[band], freqs[band]))
            per_channel[ch] = {
                "rms_display":   rms,
                "units":         res.units[i],
                "line_fraction": line / total if total > 0 else 0.0,
            }

        metrics = {"duration_s": duration_s, "per_channel": per_channel}
        worst_line = max(pc["line_fraction"] for pc in per_channel.values())
        msg = ", ".join(
            f"{ch}={pc['rms_display']:.3f}{pc['units']}"
            for ch, pc in per_channel.items()
        )
        if worst_line > 0.30:
            return Check(name, Status.FAIL,
                         f"{msg}; line-noise fraction up to {worst_line:.1%}",
                         metrics)
        if worst_line > 0.10:
            return Check(name, Status.WARN,
                         f"{msg}; line-noise fraction up to {worst_line:.1%}",
                         metrics)
        return Check(name, Status.PASS, msg, metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_cc_scaling_and_linearity(
    R_MOhm: float = 500.0,
    min_pA: float = -100.0,
    max_pA: float =  100.0,
    step_pA: float = 20.0,
    width_ms: float = 500.0,
    gap_ms: float   = 500.0,
) -> Check:
    """Inject a current staircase; verify ΔV = I · R per step and overall slope.

    With the model cell at CELL (500 MΩ), a +100 pA step should produce
    a +50 mV deflection.  A per-step mismatch >5 % flags WARN; a slope
    fit that deviates >5 % from ``R_MOhm`` or R² < 0.999 flags WARN.
    """
    name = "CC scaling & linearity (model cell)"
    try:
        from utils.stimulus_generator import get_step_amplitudes, generate_ao0_waveform
        amps_pA = get_step_amplitudes(min_pA, max_pA, step_pA)
        waveform = generate_ao0_waveform(
            min_pa=min_pA, max_pa=max_pA, step_pa=step_pA,
            width_ms=width_ms, gap_ms=gap_ms,
        )
        res = _acquire_one_shot(waveform, clamp_mode="current_clamp",
                                trail_samples=int(0.2 * SAMPLE_RATE))

        sr = res.sample_rate
        vm_idx = res.channel_names.index("ScAmpOut")
        vm_mv = res.data[vm_idx] * float(res.display_scales[vm_idx])

        width_samples = int(width_ms / 1000.0 * sr)
        gap_samples = int(gap_ms / 1000.0 * sr)
        step_samples = width_samples + gap_samples

        # Baseline: from the pre-step gap (first 10 % of gap, just before step)
        per_step = []
        for i, amp in enumerate(amps_pA):
            start = i * step_samples
            # Use last 30 ms of the gap (or from t=0) as baseline
            bl_start = max(0, start - int(0.03 * sr))
            bl_stop = start
            # Plateau: last 50 % of the pulse
            p_start = start + width_samples // 2
            p_stop = start + width_samples
            if bl_stop <= bl_start or p_stop > vm_mv.size:
                continue
            bl = float(np.median(vm_mv[bl_start:bl_stop]))
            plat = float(np.median(vm_mv[p_start:p_stop]))
            dv = plat - bl
            implied_R = (dv / amp) * 1000.0 if amp != 0 else float("nan")  # mV/pA → MΩ
            per_step.append({
                "amplitude_pA":    float(amp),
                "baseline_mV":     bl,
                "plateau_mV":      plat,
                "delta_mV":        dv,
                "implied_R_MOhm":  implied_R,
                "deviation_frac":  abs(implied_R - R_MOhm) / R_MOhm
                                    if amp != 0 and np.isfinite(implied_R) else float("nan"),
            })

        # Drop the zero-amplitude step (if any) before fitting
        fit_steps = [s for s in per_step if s["amplitude_pA"] != 0]
        if len(fit_steps) < 2:
            return Check(name, Status.FAIL,
                         "not enough non-zero steps for a linear fit",
                         {"per_step": per_step})

        x = np.asarray([s["amplitude_pA"] for s in fit_steps], dtype=float)
        y = np.asarray([s["delta_mV"]     for s in fit_steps], dtype=float)
        slope, intercept = np.polyfit(x, y, 1)          # mV per pA
        slope_MOhm = float(slope) * 1000.0               # mV/pA × 1000 pA/nA = MΩ
        y_fit = slope * x + intercept
        ss_res = float(np.sum((y - y_fit) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

        metrics = {
            "expected_R_MOhm":    float(R_MOhm),
            "fitted_slope_MOhm":  slope_MOhm,
            "slope_deviation_frac": abs(slope_MOhm - R_MOhm) / R_MOhm,
            "intercept_mV":       float(intercept),
            "r_squared":          float(r_squared),
            "per_step":           per_step,
        }

        worst_step = max((abs(s["deviation_frac"]) for s in fit_steps
                          if np.isfinite(s["deviation_frac"])), default=0.0)
        issues: list[str] = []
        if abs(slope_MOhm - R_MOhm) / R_MOhm > 0.05:
            issues.append(f"slope {slope_MOhm:.1f} MΩ vs expected {R_MOhm:.0f} MΩ")
        if r_squared < 0.999:
            issues.append(f"R² = {r_squared:.4f}")
        if worst_step > 0.05:
            issues.append(f"worst step deviates {worst_step*100:.1f}% from {R_MOhm:.0f} MΩ")

        if issues:
            return Check(name, Status.WARN, "; ".join(issues), metrics)
        return Check(name, Status.PASS,
                     f"slope {slope_MOhm:.1f} MΩ, R² = {r_squared:.4f}", metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_vc_scaling(
    R_MOhm: float = 500.0,
    step_mv_values: tuple[float, ...] = (-50, -20, -10, 10, 20, 50),
    step_ms: float = 400.0,
    gap_ms: float  = 400.0,
) -> Check:
    """Apply a set of voltage steps; verify ΔI = ΔV / R against the model cell."""
    name = "VC scaling (model cell)"
    try:
        sr = int(SAMPLE_RATE)
        step_samp = int(step_ms / 1000.0 * sr)
        gap_samp  = int(gap_ms  / 1000.0 * sr)
        per_step_samples = step_samp + gap_samp

        # Build AO waveform in Volts: step_mV / AO_MV_PER_VOLT
        total = per_step_samples * len(step_mv_values)
        ao = np.zeros(total, dtype=np.float64)
        for i, mv in enumerate(step_mv_values):
            start = i * per_step_samples
            ao[start : start + step_samp] = mv / AO_MV_PER_VOLT

        res = _acquire_one_shot(ao, clamp_mode="voltage_clamp",
                                trail_samples=int(0.2 * sr))
        i_idx = res.channel_names.index("I_mem")
        i_pa  = res.data[i_idx] * float(res.display_scales[i_idx])

        per_step: list[dict[str, Any]] = []
        for i, mv in enumerate(step_mv_values):
            start = i * per_step_samples
            bl_start = max(0, start - int(0.05 * sr))
            bl_stop  = start
            p_start  = start + step_samp // 2
            p_stop   = start + step_samp
            if bl_stop <= bl_start or p_stop > i_pa.size:
                continue
            bl   = float(np.median(i_pa[bl_start:bl_stop]))
            plat = float(np.median(i_pa[p_start:p_stop]))
            di = plat - bl
            expected_di = mv / R_MOhm * 1000.0       # mV / MΩ → nA → pA (×1000)
            # Actually: I[pA] = V[mV] / R[MΩ] → 50 mV / 500 MΩ = 0.1 nA = 100 pA. ✓
            implied_R = (mv / di) * 1000.0 if di != 0 else float("nan")  # MΩ
            per_step.append({
                "step_mV":         float(mv),
                "expected_dI_pA":  expected_di,
                "recorded_dI_pA":  di,
                "implied_R_MOhm":  implied_R,
                "deviation_frac":  abs(implied_R - R_MOhm) / R_MOhm
                                    if di != 0 and np.isfinite(implied_R) else float("nan"),
            })

        # Linear fit: I (pA) vs V (mV); slope should be 1/R (pA/mV) = 1000/R[MΩ]
        xs = np.asarray([s["step_mV"]        for s in per_step], dtype=float)
        ys = np.asarray([s["recorded_dI_pA"] for s in per_step], dtype=float)
        slope, intercept = np.polyfit(xs, ys, 1)
        implied_R_from_slope = 1000.0 / slope if slope != 0 else float("nan")

        metrics = {
            "expected_R_MOhm":      float(R_MOhm),
            "implied_R_from_slope": float(implied_R_from_slope),
            "slope_pA_per_mV":      float(slope),
            "intercept_pA":         float(intercept),
            "per_step":             per_step,
        }

        worst_step = max((abs(s["deviation_frac"]) for s in per_step
                          if np.isfinite(s["deviation_frac"])), default=0.0)
        if worst_step > 0.05:
            return Check(name, Status.WARN,
                         f"worst step deviates {worst_step*100:.1f}% from {R_MOhm:.0f} MΩ "
                         f"(slope R = {implied_R_from_slope:.1f} MΩ)",
                         metrics)
        return Check(name, Status.PASS,
                     f"all steps within 5% of {R_MOhm:.0f} MΩ "
                     f"(slope R = {implied_R_from_slope:.1f} MΩ)",
                     metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_capacitance_tau(
    step_mV: float = 20.0,
    step_ms: float = 200.0,
    fit_ms: float  = 20.0,
) -> Check:
    """Exponential τ fit of the I_mem onset response to a VC step.

    With no amplifier capacitance compensation, τ = R·C where C is the
    model cell's own capacitance (often 33 pF → τ ~16.5 ms at 500 MΩ).
    With compensation applied, τ should collapse dramatically.  This
    check is informational — it reports τ per step but does not fail.
    """
    name = "Capacitance transient τ (VC step)"
    try:
        sr = int(SAMPLE_RATE)
        step_samp = int(step_ms / 1000.0 * sr)
        pre_samp  = int(0.05 * sr)
        total = pre_samp + step_samp + int(0.05 * sr)
        ao = np.zeros(total, dtype=np.float64)
        ao[pre_samp : pre_samp + step_samp] = step_mV / AO_MV_PER_VOLT

        res = _acquire_one_shot(ao, clamp_mode="voltage_clamp")
        i_idx = res.channel_names.index("I_mem")
        i_pa = res.data[i_idx] * float(res.display_scales[i_idx])

        fit_samp = int(fit_ms / 1000.0 * sr)
        onset = pre_samp
        seg = i_pa[onset : onset + fit_samp]
        t = np.arange(seg.size) / sr

        # I(t) = I_ss + (I_0 - I_ss) · exp(-t/τ)
        def model(t, I_ss, dI, tau):
            return I_ss + dI * np.exp(-t / max(tau, 1e-6))

        p0 = [float(seg[-1]), float(seg[0] - seg[-1]), 0.005]
        try:
            popt, _ = curve_fit(model, t, seg, p0=p0, maxfev=4000)
            tau_ms = float(popt[2]) * 1000.0
            i_ss   = float(popt[0])
            di     = float(popt[1])
        except Exception as exc:
            return Check(name, Status.WARN, f"exponential fit failed: {exc}",
                         {"step_mV": step_mV})

        metrics = {
            "step_mV":  float(step_mV),
            "tau_ms":   tau_ms,
            "I_ss_pA": i_ss,
            "delta_I_pA": di,
        }
        return Check(name, Status.PASS,
                     f"τ = {tau_ms:.2f} ms, I_ss = {i_ss:.1f} pA, ΔI₀ = {di:.1f} pA",
                     metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_analysis_pipeline_self_test(
    R_MOhm: float = 500.0,
    hyperpol_pA: float = -50.0,
    hyperpol_ms: float = 300.0,
) -> Check:
    """Exercise the analysis pipeline: record a CC hyperpol pulse, run R_i.

    Runs :func:`analysis.analyze_steps.compute_input_resistance` on a
    fresh model-cell recording.  If the pipeline is healthy the result
    should be within 5 % of ``R_MOhm``.
    """
    name = "Analysis pipeline self-test (compute_input_resistance)"
    try:
        from analysis.analyze_steps import compute_input_resistance

        sr = int(SAMPLE_RATE)
        pre_samp   = int(0.2 * sr)
        hyper_samp = int(hyperpol_ms / 1000.0 * sr)
        post_samp  = int(0.2 * sr)

        ao = np.zeros(pre_samp + hyper_samp + post_samp, dtype=np.float64)
        ao[pre_samp : pre_samp + hyper_samp] = hyperpol_pA / AO_PA_PER_VOLT
        res = _acquire_one_shot(ao, clamp_mode="current_clamp")

        pulse = {
            "onset":        pre_samp,
            "offset":       pre_samp + hyper_samp,
            "amplitude_pA": hyperpol_pA,
        }
        vm_idx = res.channel_names.index("ScAmpOut")
        rmp_mV = float(np.median(
            res.data[vm_idx, :pre_samp] * float(res.display_scales[vm_idx])
        ))
        Ri = compute_input_resistance(
            data           = res.data,
            display_scales = res.display_scales,
            hyperpol_pulse = pulse,
            rmp_mV         = rmp_mV,
            sr             = sr,
            vm_ch          = vm_idx,
        )

        metrics = {
            "computed_Ri_MOhm": float(Ri),
            "expected_R_MOhm":  float(R_MOhm),
            "rmp_mV":           rmp_mV,
        }
        dev = abs(Ri - R_MOhm) / R_MOhm
        if dev > 0.05:
            return Check(name, Status.WARN,
                         f"Ri = {Ri:.1f} MΩ vs expected {R_MOhm:.0f} MΩ "
                         f"({dev*100:.1f}% off)", metrics)
        return Check(name, Status.PASS,
                     f"Ri = {Ri:.1f} MΩ (expected {R_MOhm:.0f}, within 5%)",
                     metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


# ============================================================================
# Orchestrator
# ============================================================================

def _prompt_mode_switch(mode: str, interactive: bool = True) -> None:
    """Pause for the user to set the amplifier's front-panel clamp mode.

    Called between phases so the operator can manually flip the amplifier
    between current-clamp and voltage-clamp.  No-op when ``interactive``
    is False (scripted/CI runs).
    """
    if not interactive:
        return
    label = {
        "current_clamp": "CURRENT CLAMP (I-clamp)",
        "voltage_clamp": "VOLTAGE CLAMP (V-clamp)",
    }.get(mode, mode)
    bar = "=" * 64
    print()
    print(bar)
    print(f"  >>> Set amplifier to {label}, then press Enter <<<")
    print(bar)
    try:
        input()
    except EOFError:
        pass


def run_alignment(
    include_phase_b: bool = True,
    model_cell_MOhm: float = 500.0,
    interactive: bool = True,
) -> dict[str, list[Check]]:
    """Run every alignment check and return a dict of Check lists by section.

    Phase B is split into a current-clamp block (inject I, measure V) and
    a voltage-clamp block (apply V, measure I).  When ``interactive`` is
    True the operator is prompted to flip the amplifier's front-panel
    mode switch between blocks; set it False to run non-interactively.
    """
    _prompt_mode_switch("current_clamp", interactive)
    sections: dict[str, list[Check]] = {
        "Phase A — rig timing": [
            check_ao_ai_latency(),
            check_inter_channel_crosstalk(),
            check_ttl_period_stability(),
        ],
    }
    if include_phase_b:
        sections["Phase B1 — model cell, current clamp"] = [
            check_resting_baseline(),
            check_noise_floor(),
            check_cc_scaling_and_linearity(R_MOhm=model_cell_MOhm),
            check_analysis_pipeline_self_test(R_MOhm=model_cell_MOhm),
        ]
        _prompt_mode_switch("voltage_clamp", interactive)
        sections["Phase B2 — model cell, voltage clamp"] = [
            check_vc_scaling(R_MOhm=model_cell_MOhm),
            check_capacitance_tau(),
        ]
    return sections


def write_alignment_report(
    sections: dict[str, list[Check]],
    save_dir: str | Path,
) -> dict[str, str]:
    """Write HTML + JSON report and append a drift-tracking CSV row.

    Report files land under ``{save_dir}/_alignment_checks/`` with a
    timestamped prefix (``qc_alignment_YYYYMMDD_HHMMSS``).  The master
    CSV (``qc_alignment_history.csv``) accumulates key numeric metrics
    across runs so drift over weeks is trivially plottable.
    """
    save_dir = Path(save_dir)
    out_dir = save_dir / "_alignment_checks"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = out_dir / f"qc_alignment_{ts}"
    html_path = prefix.with_suffix(".html")
    json_path = prefix.with_suffix(".json")
    csv_path  = out_dir / "qc_alignment_history.csv"

    overall = worst([c for cs in sections.values() for c in cs])
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "overall_status": overall.value,
        "sections": {
            name: [c.to_dict() for c in cs]
            for name, cs in sections.items()
        },
    }
    json_path.write_text(json.dumps(payload, indent=2, default=_json_default))

    # ---- HTML (reuse main report template, lighter plot set) ----------
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from analysis.qc.descriptions import (
        ALIGNMENT_REPORT_INTRO, STATUS_KEY, describe_check, describe_section,
    )
    tmpl_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(tmpl_dir)),
        autoescape=select_autoescape(["html"]),
    )
    tmpl = env.get_template("report.html.j2")
    html = tmpl.render(
        h5_path="(hardware alignment run — not tied to a recording)",
        mode="alignment",
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        overall_status=overall.value,
        report_intro=ALIGNMENT_REPORT_INTRO,
        status_key=STATUS_KEY,
        sections={
            name: {
                "description": describe_section(name),
                "checks": [
                    {
                        "name":        c.name,
                        "status":      c.status.value,
                        "message":     c.message,
                        "metrics":     json.dumps(c.metrics, indent=2,
                                                  default=_json_default),
                        "description": describe_check(c.name),
                    }
                    for c in cs
                ],
            }
            for name, cs in sections.items()
        },
        plots={},
        sample_rate=SAMPLE_RATE,
        channel_names=[c[0] for c in AI_CHANNELS],
        start_time="",
    )
    html_path.write_text(html, encoding="utf-8")

    # ---- Master CSV ----------------------------------------------------
    row = _csv_row(ts, sections)
    _append_csv(csv_path, row)

    return {
        "html":    str(html_path),
        "json":    str(json_path),
        "csv":     str(csv_path),
        "status":  overall.value,
    }


def _csv_row(ts: str, sections: dict[str, list[Check]]) -> dict[str, Any]:
    """Flatten key metrics into a single CSV row for drift tracking."""
    row: dict[str, Any] = {
        "timestamp":      ts,
        "overall_status": worst(
            [c for cs in sections.values() for c in cs]
        ).value,
    }
    for cs in sections.values():
        for c in cs:
            key = (c.name.replace(" ", "_")
                         .replace("(", "").replace(")", "")
                         .replace(",", "").replace("/", "_"))
            row[f"{key}__status"] = c.status.value
            # Promote a handful of numeric metrics for quick plotting
            for mk in ("lag_ms", "camera_rms_v", "ttl_rms_v",
                       "observed_rate_hz", "period_jitter_frac",
                       "resting_mV", "fitted_slope_MOhm", "r_squared",
                       "implied_R_from_slope", "tau_ms", "computed_Ri_MOhm"):
                if mk in c.metrics:
                    row[f"{key}__{mk}"] = _scalar(c.metrics[mk])
    return row


def _append_csv(path: Path, row: dict[str, Any]) -> None:
    """Append ``row`` to ``path``, writing header if missing or if columns changed."""
    header_needed = not path.exists()
    existing_fields: list[str] = []
    if not header_needed:
        with path.open("r", newline="", encoding="utf-8") as f:
            existing_fields = next(csv.reader(f), [])
        if set(row.keys()) != set(existing_fields):
            header_needed = True

    if header_needed:
        all_fields = sorted(set(existing_fields) | set(row.keys()))
        rows: list[dict[str, Any]] = []
        if path.exists():
            with path.open("r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=all_fields)
            w.writeheader()
            for r in rows:
                w.writerow(r)
            w.writerow(row)
    else:
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=existing_fields)
            w.writerow(row)


def _scalar(x: Any) -> Any:
    if isinstance(x, (int, float, str)):
        return x
    if isinstance(x, np.generic):
        return x.item()
    return str(x)


def _json_default(x: Any) -> Any:
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (np.integer, np.floating)):
        return x.item()
    if isinstance(x, Path):
        return str(x)
    raise TypeError(f"not JSON-serialisable: {type(x).__name__}")

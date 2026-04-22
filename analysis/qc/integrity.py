"""
Acquisition integrity checks.

All checks catch their own exceptions and downgrade to FAIL — the report
should always render, even if one check chokes on unexpected input.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from analysis.qc import Check, Status


# ----------------------------------------------------------------------------
# Public entry points
# ----------------------------------------------------------------------------

def run_all(bundle: dict[str, Any]) -> list[Check]:
    """Run every integrity check applicable to the given recording."""
    checks: list[Check] = [
        check_sample_count_consistency(bundle),
        check_hdf5_sidecar_agreement(bundle),
        check_finite_values(bundle),
    ]

    if bundle["recording_mode"] == "continuous":
        checks.append(check_stimulus_events(bundle))
    else:
        checks.append(check_trial_integrity(bundle))

    checks.append(check_camera_ttl_frame_count(bundle))
    checks.append(check_acquisition_log(bundle))
    return checks


# ----------------------------------------------------------------------------
# Individual checks
# ----------------------------------------------------------------------------

def check_sample_count_consistency(bundle: dict[str, Any]) -> Check:
    """HDF5 sample count matches .bin file size and sidecar JSON duration."""
    name = "Sample-count consistency"
    try:
        n_ch = len(bundle["channel_names"])
        sr = bundle["sample_rate"]

        if bundle["recording_mode"] == "continuous":
            n_samples_h5 = int(bundle["data"].shape[1])
        else:
            n_samples_h5 = int(sum(t["data"].shape[1] for t in bundle["trials"]))

        metrics: dict[str, Any] = {
            "n_samples_hdf5": n_samples_h5,
            "n_channels":     n_ch,
            "sample_rate":    sr,
        }
        issues: list[str] = []

        bin_path: Path = bundle["bin_path"]
        if bin_path.exists():
            bin_bytes = bin_path.stat().st_size
            expected = n_samples_h5 * n_ch * 8
            metrics["n_samples_bin"]  = bin_bytes // (n_ch * 8)
            metrics["bin_bytes"]      = bin_bytes
            metrics["bin_bytes_expected"] = expected
            if bin_bytes != expected:
                issues.append(
                    f".bin size {bin_bytes} B != expected {expected} B "
                    f"(Δ = {bin_bytes - expected} B)"
                )

        sidecar = bundle.get("sidecar")
        if sidecar is not None:
            if "duration_samples" in sidecar and sidecar["duration_samples"] is not None:
                metrics["n_samples_sidecar"] = int(sidecar["duration_samples"])
                if int(sidecar["duration_samples"]) != n_samples_h5:
                    issues.append(
                        f"sidecar duration_samples {sidecar['duration_samples']} "
                        f"!= HDF5 {n_samples_h5}"
                    )
            if "duration_seconds" in sidecar and sidecar["duration_seconds"] is not None:
                expected_from_seconds = int(round(float(sidecar["duration_seconds"]) * sr))
                metrics["n_samples_from_seconds"] = expected_from_seconds
                if abs(expected_from_seconds - n_samples_h5) > 1:
                    issues.append(
                        f"sidecar duration_seconds implies {expected_from_seconds} samples "
                        f"!= HDF5 {n_samples_h5}"
                    )

        if issues:
            return Check(name, Status.FAIL, "; ".join(issues), metrics)
        return Check(name, Status.PASS,
                     f"HDF5/bin/sidecar agree: {n_samples_h5} samples "
                     f"({n_samples_h5 / sr:.2f} s)", metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_hdf5_sidecar_agreement(bundle: dict[str, Any]) -> Check:
    """HDF5 metadata and _metadata.json sidecar describe the same recording."""
    name = "HDF5 ↔ sidecar metadata"
    sidecar = bundle.get("sidecar")
    if sidecar is None:
        return Check(name, Status.SKIP, "no _metadata.json sidecar found")
    try:
        issues: list[str] = []
        metrics: dict[str, Any] = {}

        h5_sr = int(bundle["sample_rate"])
        sc_sr = int(sidecar.get("sample_rate_hz", -1))
        metrics["hdf5_sample_rate"] = h5_sr
        metrics["sidecar_sample_rate"] = sc_sr
        if sc_sr != h5_sr:
            issues.append(f"sample_rate mismatch: {h5_sr} vs {sc_sr}")

        sc_channels = sidecar.get("channels", [])
        sc_names = [c.get("name", "") for c in sc_channels]
        h5_names = list(bundle["channel_names"])
        metrics["hdf5_channels"]    = h5_names
        metrics["sidecar_channels"] = sc_names
        if sc_names and sc_names != h5_names:
            issues.append(f"channel names differ: {h5_names} vs {sc_names}")

        h5_start = str(bundle.get("start_time", ""))
        sc_start = str(sidecar.get("start_time", ""))
        if sc_start and h5_start and _trunc_to_seconds(h5_start) != _trunc_to_seconds(sc_start):
            issues.append(f"start_time differs (past the second): '{h5_start}' vs '{sc_start}'")

        if issues:
            return Check(name, Status.WARN, "; ".join(issues), metrics)
        return Check(name, Status.PASS, "HDF5 and sidecar metadata agree", metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_finite_values(bundle: dict[str, Any]) -> Check:
    """No NaN or Inf values anywhere in recorded data."""
    name = "Finite values (no NaN/Inf)"
    try:
        if bundle["recording_mode"] == "continuous":
            arr = bundle["data"]
            n_nonfinite = int(np.count_nonzero(~np.isfinite(arr)))
            total = int(arr.size)
        else:
            n_nonfinite = 0
            total = 0
            for t in bundle["trials"]:
                a = t["data"]
                n_nonfinite += int(np.count_nonzero(~np.isfinite(a)))
                total += int(a.size)

        metrics = {"n_nonfinite": n_nonfinite, "n_samples_total": total}
        if n_nonfinite == 0:
            return Check(name, Status.PASS,
                         f"all {total:,} samples finite", metrics)
        return Check(name, Status.FAIL,
                     f"{n_nonfinite:,} non-finite samples out of {total:,}", metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_stimulus_events(bundle: dict[str, Any]) -> Check:
    """Stimulus events are monotonic, in-range, and apply/clear pair correctly."""
    name = "Stimulus event table"
    events = bundle.get("stimulus_events", [])
    if not events:
        return Check(name, Status.SKIP, "no stimulus events logged")
    try:
        n_samples = int(bundle["data"].shape[1])
        indices = [int(e["sample_index"]) for e in events]
        metrics: dict[str, Any] = {
            "n_events":     len(events),
            "min_index":    min(indices),
            "max_index":    max(indices),
            "n_samples":    n_samples,
        }
        issues: list[str] = []

        if any(indices[i] > indices[i + 1] for i in range(len(indices) - 1)):
            issues.append("sample_index not monotonic")
        out_of_range = [i for i in indices if i < 0 or i >= n_samples]
        if out_of_range:
            issues.append(f"{len(out_of_range)} events outside [0,{n_samples})")

        active: dict[int, int] = {}
        for e in events:
            si = int(e["stimulus_index"])
            et = e["event_type"]
            if et == "apply":
                if si in active:
                    issues.append(f"stimulus_index={si} applied twice without clear")
                active[si] = 1
            elif et == "clear":
                if si not in active:
                    issues.append(f"stimulus_index={si} cleared without a prior apply")
                active.pop(si, None)
        if active:
            metrics["unclosed_apply_indices"] = sorted(active.keys())

        if issues:
            return Check(name, Status.WARN, "; ".join(issues[:3]), metrics)
        return Check(name, Status.PASS,
                     f"{len(events)} events, monotonic and paired", metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_trial_integrity(bundle: dict[str, Any]) -> Check:
    """Trial count matches protocol, indices are contiguous, channel counts uniform."""
    name = "Trial table integrity"
    trials = bundle.get("trials") or []
    if not trials:
        return Check(name, Status.FAIL, "no trials found in HDF5")
    try:
        expected = int(bundle.get("n_trials", len(trials)))
        observed = len(trials)
        indices = sorted(int(t["trial_index"]) for t in trials)
        n_channels = {int(t["data"].shape[0]) for t in trials}

        metrics: dict[str, Any] = {
            "expected_trials":    expected,
            "observed_trials":    observed,
            "trial_indices":      indices,
            "channel_counts":     sorted(n_channels),
            "trial_sample_counts": [int(t["data"].shape[1]) for t in trials],
        }
        issues: list[str] = []

        if observed != expected:
            issues.append(f"trial count {observed} != expected {expected}")
        if indices != list(range(observed)):
            missing = [i for i in range(expected) if i not in indices]
            issues.append(f"trial_index not contiguous (missing: {missing[:5]})")
        if len(n_channels) > 1:
            issues.append(f"trials have inconsistent channel counts: {sorted(n_channels)}")

        if issues:
            return Check(name, Status.FAIL, "; ".join(issues), metrics)
        return Check(name, Status.PASS,
                     f"{observed} trials, contiguous, uniform channel count", metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_camera_ttl_frame_count(bundle: dict[str, Any]) -> Check:
    """Count rising edges on the TTL channel and compare to AVI frame count.

    Prefers the ``TTLLoopback`` channel (the commanded 0/5 V trigger) and
    falls back to ``Camera`` (the camera's exposure-out signal, which may
    sit around 3–4 V).  Threshold is adaptive: midpoint between the channel
    min/max, not a hard-coded 2.5 V.
    """
    name = "Camera TTL ↔ video frame count"
    videos = bundle.get("video_paths") or []
    if not videos:
        return Check(name, Status.SKIP, "no video file alongside recording")
    try:
        ttl_name = None
        ttl_idx = _channel_index(bundle, "TTLLoopback")
        if ttl_idx is not None:
            ttl_name = "TTLLoopback"
        else:
            ttl_idx = _channel_index(bundle, "Camera")
            if ttl_idx is not None:
                ttl_name = "Camera"
        if ttl_idx is None:
            return Check(name, Status.SKIP,
                         "no 'TTLLoopback' or 'Camera' channel in recording")

        if bundle["recording_mode"] == "continuous":
            cam = np.asarray(bundle["data"][ttl_idx], dtype=float)
            ttl_count, thresh = _count_rising_edges_adaptive(cam)
        else:
            ttl_count = 0
            thresh_samples: list[float] = []
            for t in bundle["trials"]:
                seg = np.asarray(t["data"][ttl_idx], dtype=float)
                n, th = _count_rising_edges_adaptive(seg)
                ttl_count += n
                if th is not None:
                    thresh_samples.append(th)
            thresh = float(np.mean(thresh_samples)) if thresh_samples else None

        frame_count = 0
        per_video: list[dict[str, Any]] = []
        for v in videos:
            fc = _avi_frame_count(v)
            per_video.append({"file": v.name, "frames": fc})
            frame_count += fc if fc is not None else 0

        unreadable = [pv for pv in per_video if pv["frames"] is None]
        metrics: dict[str, Any] = {
            "ttl_channel":        ttl_name,
            "ttl_threshold_v":    thresh,
            "ttl_rising_edges":   ttl_count,
            "video_frame_count":  frame_count,
            "videos":             per_video,
        }

        if unreadable:
            return Check(name, Status.WARN,
                         f"could not read frame count for {len(unreadable)} video(s); "
                         f"install opencv-python or imageio-ffmpeg to enable",
                         metrics)

        drift = ttl_count - frame_count
        metrics["drift_frames"] = drift
        if drift == 0:
            return Check(name, Status.PASS,
                         f"{ttl_count} TTL edges == {frame_count} frames", metrics)
        if abs(drift) <= 1:
            return Check(name, Status.WARN,
                         f"drift {drift} frame (TTL {ttl_count} vs video {frame_count})",
                         metrics)
        return Check(name, Status.FAIL,
                     f"drift {drift} frames (TTL {ttl_count} vs video {frame_count})",
                     metrics)
    except Exception as exc:
        return Check(name, Status.FAIL, f"check raised: {exc}")


def check_acquisition_log(bundle: dict[str, Any]) -> Check:
    """Surface any buffer-fill or live warnings captured during acquisition."""
    name = "Live acquisition log"
    log_path = bundle.get("acquisition_log_path")
    if log_path is None:
        return Check(name, Status.SKIP, "no _acquisition.log for this recording")
    try:
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
        warn = sum(1 for ln in lines if "WARN" in ln)
        err  = sum(1 for ln in lines if "ERROR" in ln)
        metrics = {"log_lines": len(lines), "warnings": warn, "errors": err,
                   "path": str(log_path)}
        if err > 0:
            return Check(name, Status.FAIL,
                         f"{err} error(s), {warn} warning(s) in acquisition log",
                         metrics)
        if warn > 0:
            return Check(name, Status.WARN,
                         f"{warn} warning(s) in acquisition log", metrics)
        return Check(name, Status.PASS, "acquisition log is clean", metrics)
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


def _trunc_to_seconds(ts: str) -> str:
    """Drop sub-second precision from an ISO timestamp for lenient comparison.

    ``2026-04-21T14:35:07.123456`` → ``2026-04-21T14:35:07``.  If no
    fractional-second separator is present, returns the string unchanged.
    """
    return ts.split(".", 1)[0]


def _count_rising_edges_adaptive(
    signal: np.ndarray,
    min_swing_v: float = 0.5,
) -> tuple[int, float | None]:
    """Count low→high transitions using an adaptive midpoint threshold.

    Returns ``(edge_count, threshold_v)``.  The threshold is the midpoint
    between the signal's min and max, which handles both rail-to-rail TTL
    (0–5 V) and the camera's 3-ish V exposure-out line.  If the total swing
    is less than ``min_swing_v`` the channel is treated as flat and 0 edges
    are returned.
    """
    if signal.size < 2:
        return 0, None
    lo = float(np.min(signal))
    hi = float(np.max(signal))
    if hi - lo < min_swing_v:
        return 0, None
    threshold = 0.5 * (lo + hi)
    high = signal > threshold
    edges = int(np.count_nonzero(~high[:-1] & high[1:]))
    return edges, threshold


def _avi_frame_count(path: Path) -> int | None:
    """Return frame count for an AVI file, or None if no reader is available."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        try:
            if not cap.isOpened():
                return None
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            return n if n >= 0 else None
        finally:
            cap.release()
    except ImportError:
        pass
    try:
        import imageio.v3 as iio
        return int(iio.improps(path).shape[0])
    except Exception:
        return None

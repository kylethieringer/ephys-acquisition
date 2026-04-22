"""
Recording loader for QC.

Opens an HDF5 file produced by :class:`~acquisition.data_saver.ContinuousSaver`
or :class:`~acquisition.trial_saver.TrialSaver`, auto-detects the mode, and
loads every artifact the checks might need (data, metadata, sidecar JSON,
stimulus events, companion files on disk) into a single bundle.

The bundle is a plain dict so it JSON-serialises cleanly for debugging.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np


def load_recording(h5_path: str | Path) -> dict[str, Any]:
    """Load an ephys recording and all companion artifacts for QC.

    Returns a dict with:
        h5_path, bin_path, sidecar_path (Paths; may not all exist)
        video_paths: list[Path] of AVI files associated with this recording
        acquisition_log_path: Path or None (live buffer-fill log if present)
        recording_mode: "continuous" or "trial"
        sample_rate, channel_names, display_scales, units, start_time
        data: (n_ch, n_samp) float64, only for continuous mode
        trials: list of dict for trial mode, each with
                 trial_index, stimulus_index, stimulus_name, onset_time,
                 video_file, data (n_ch, n_samp)
        clamp_mode, n_trials, protocol_json (trial mode only)
        subject: dict of subject attributes (may be empty)
        stimulus_events: list of dicts or [] (continuous mode)
        sidecar: parsed _metadata.json dict or None
    """
    h5_path = Path(h5_path)
    if not h5_path.exists():
        raise FileNotFoundError(f"HDF5 not found: {h5_path}")

    bundle: dict[str, Any] = {
        "h5_path": h5_path,
        "bin_path": h5_path.with_suffix(".bin"),
        "sidecar_path": _find_sidecar(h5_path),
        "video_paths": _find_videos(h5_path),
        "acquisition_log_path": _find_log(h5_path),
        "recording_mode": None,
        "data": None,
        "trials": None,
        "subject": {},
        "stimulus_events": [],
        "sidecar": None,
    }

    with h5py.File(h5_path, "r") as f:
        meta = f["metadata"]
        bundle["sample_rate"]    = int(meta.attrs["sample_rate"])
        bundle["start_time"]     = str(meta.attrs.get("start_time", ""))
        bundle["channel_names"]  = [_decode(n) for n in meta["channel_names"][:]]
        bundle["display_scales"] = np.asarray(meta["display_scales"][:], dtype=float)
        bundle["units"]          = [_decode(u) for u in meta["units"][:]]

        if "subject" in f:
            bundle["subject"] = {k: _decode(v) for k, v in f["subject"].attrs.items()}

        has_continuous_data = "data" in f and "analog_input" in f["data"]
        has_trial_group = any(k.startswith("trial_") for k in f.keys())

        if has_trial_group and not has_continuous_data:
            bundle["recording_mode"] = "trial"
            _load_trials(f, meta, bundle)
        else:
            bundle["recording_mode"] = "continuous"
            bundle["data"] = f["data/analog_input"][:]
            if "stimulus_events" in f:
                ev = f["stimulus_events"]
                bundle["stimulus_events"] = [
                    {
                        "sample_index":   int(ev["sample_index"][i]),
                        "event_type":     _decode(ev["event_type"][i]),
                        "stimulus_name":  _decode(ev["stimulus_name"][i]),
                        "stimulus_index": int(ev["stimulus_index"][i]),
                    }
                    for i in range(len(ev["sample_index"]))
                ]

    if bundle["sidecar_path"] is not None and bundle["sidecar_path"].exists():
        try:
            bundle["sidecar"] = json.loads(bundle["sidecar_path"].read_text())
        except Exception:
            bundle["sidecar"] = None

    return bundle


def _load_trials(f: h5py.File, meta: h5py.Group, bundle: dict[str, Any]) -> None:
    bundle["clamp_mode"]    = str(meta.attrs.get("clamp_mode", ""))
    bundle["n_trials"]      = int(meta.attrs.get("n_trials", 0))
    bundle["protocol_json"] = _decode(meta["protocol"][()]) if "protocol" in meta else ""

    trials: list[dict[str, Any]] = []
    trial_keys = sorted(k for k in f.keys() if k.startswith("trial_"))
    for key in trial_keys:
        grp = f[key]
        trials.append({
            "trial_index":    int(grp.attrs.get("trial_index", -1)),
            "stimulus_index": int(grp.attrs.get("stimulus_index", -1)),
            "stimulus_name":  _decode(grp.attrs.get("stimulus_name", "")),
            "onset_time":     _decode(grp.attrs.get("onset_time", "")),
            "video_file":     _decode(grp.attrs.get("video_file", "")),
            "data":           grp["analog_input"][:],
        })
    bundle["trials"] = trials


def _find_sidecar(h5_path: Path) -> Path | None:
    candidates = [
        h5_path.with_suffix(".json"),
        h5_path.with_name(h5_path.stem + "_metadata.json"),
    ]
    trials_stem = h5_path.stem
    if trials_stem.endswith("_trials"):
        candidates.append(h5_path.with_name(trials_stem[: -len("_trials")] + "_metadata.json"))
    for c in candidates:
        if c.exists():
            return c
    return None


def _find_videos(h5_path: Path) -> list[Path]:
    stem = h5_path.stem
    base_stem = stem[: -len("_trials")] if stem.endswith("_trials") else stem
    videos = sorted(h5_path.parent.glob(f"{base_stem}*.avi"))
    return list(videos)


def _find_log(h5_path: Path) -> Path | None:
    log = h5_path.with_name(h5_path.stem + "_acquisition.log")
    return log if log.exists() else None


def _decode(x: Any) -> str:
    if isinstance(x, bytes):
        return x.decode("utf-8", errors="replace")
    return str(x)

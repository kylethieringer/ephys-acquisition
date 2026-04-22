"""Interactive analysis of current-injection staircase recordings.

Loads a continuous-mode HDF5 recording, detects staircase stimuli (using
protocol metadata when available), computes intrinsic cell properties
(resting membrane potential and input resistance), and lets you
interactively browse and save overlay plots of the step responses.

Usage::

    python analyze_steps.py

A file-picker dialog will open to select the HDF5 file.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

import h5py
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import median_filter

# Add project root so we can import acquisition.trial_protocol
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from acquisition.trial_protocol import protocol_from_dict

# ── Apply custom matplotlib style ────────────────────────────────────────
_STYLE_PATH = Path(__file__).resolve().parent / "kt.mplstyle"
if _STYLE_PATH.exists():
    plt.style.use(_STYLE_PATH)

# ── Constants ─────────────────────────────────────────────────────────────
VM_CH = 0       # ScAmpOut channel index (membrane potential)
CMD_CH = 2      # AmpCmd channel index (command current loopback)
MIN_STEP_MS = 50    # minimum plateau duration to count as a step (ms)
MIN_AMP_PA = 10     # ignore plateaus with |amplitude| below this (pA)
MAX_GAP_MS = 2500   # max silence between steps in the same staircase (ms)
PAD_MS = 100        # pre-step baseline shown before each step onset (ms)
FIG_DIR = r"D:\results"

# Black -> medium blue colormap for step-amplitude coloring
CMAP = mcolors.LinearSegmentedColormap.from_list(
    "black_blue", [(0.0, 0.0, 0.0), (0.15, 0.45, 0.85)]
)


# =========================================================================
# Utility
# =========================================================================

def ms_to_samples(ms: float, sr: int) -> int:
    """Convert a duration in milliseconds to an integer sample count.

    Parameters
    ----------
    ms : float
        Duration in milliseconds.
    sr : int
        Sampling rate in Hz.

    Returns
    -------
    int
        Number of samples (at least 0).
    """
    return max(0, int(ms / 1000.0 * sr))


# =========================================================================
# Data loading
# =========================================================================

def select_file() -> Path | None:
    """Open a file-picker dialog and return the selected HDF5 path.

    Returns ``None`` if the user cancels the dialog.
    """
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    path = filedialog.askopenfilename(
        title="Select continuous HDF5 recording",
        filetypes=[("HDF5 files", "*.h5"), ("All files", "*.*")],
        initialdir=r"D:\data",
    )
    root.destroy()
    return Path(path) if path else None


def load_continuous_h5(h5_path: Path) -> dict:
    """Load a continuous-mode HDF5 recording.

    Parameters
    ----------
    h5_path : Path
        Path to the ``.h5`` file (must contain ``/data/analog_input``).

    Returns
    -------
    dict
        Keys: ``data`` (n_channels x n_samples ndarray), ``sample_rate``,
        ``channel_names``, ``display_scales``, ``units``,
        ``stimulus_events`` (dict or None), ``subject`` (dict).

    Raises
    ------
    ValueError
        If the file is trial-based rather than continuous.
    """
    result = {}
    with h5py.File(h5_path, "r") as f:
        # Reject trial-based files
        trial_keys = [k for k in f.keys() if k.startswith("trial_")]
        if trial_keys:
            raise ValueError(
                "This is a trial-based file. Only continuous recordings "
                "are supported."
            )

        # Metadata
        meta = f["metadata"]
        result["sample_rate"] = int(meta.attrs["sample_rate"])
        result["channel_names"] = [
            s.decode() if isinstance(s, bytes) else s
            for s in meta["channel_names"][:]
        ]
        result["display_scales"] = meta["display_scales"][:]
        result["units"] = [
            s.decode() if isinstance(s, bytes) else s
            for s in meta["units"][:]
        ]

        # Raw analog data
        result["data"] = f["data/analog_input"][:]

        # Stimulus events (optional)
        if "stimulus_events" in f:
            ev = f["stimulus_events"]
            result["stimulus_events"] = {
                "sample_index": ev["sample_index"][:],
                "event_type": [
                    s.decode() if isinstance(s, bytes) else s
                    for s in ev["event_type"][:]
                ],
                "stimulus_name": [
                    s.decode() if isinstance(s, bytes) else s
                    for s in ev["stimulus_name"][:]
                ],
                "stimulus_index": ev["stimulus_index"][:],
            }
        else:
            result["stimulus_events"] = None

        # Subject metadata
        if "subject" in f:
            result["subject"] = dict(f["subject"].attrs)
        else:
            result["subject"] = {}

    return result


def load_protocol_metadata(h5_path: Path) -> list[dict]:
    """Load protocol run(s) from the sidecar ``_metadata.json`` file.

    Each entry in the returned list has ``start_sample`` (int) and
    ``protocol`` (a ``TrialProtocol`` instance parsed from JSON).

    Parameters
    ----------
    h5_path : Path
        Path to the ``.h5`` file; the sidecar is assumed to be at
        ``<stem>_metadata.json`` in the same directory.

    Returns
    -------
    list[dict]
        One dict per protocol run with keys ``start_sample`` and
        ``protocol``.  Empty list if no sidecar or no protocol entries.
    """
    meta_path = h5_path.with_name(h5_path.stem + "_metadata.json")
    if not meta_path.exists():
        return []
    metadata = json.loads(meta_path.read_text())
    runs = []
    for entry in metadata.get("protocols", []) or []:
        proto_dict = entry.get("protocol")
        if proto_dict is None:
            continue
        runs.append({
            "start_sample": entry.get("start_sample", 0),
            "protocol": protocol_from_dict(proto_dict),
            "protocol_dict": proto_dict,
        })
    return runs


# =========================================================================
# Signal detection
# =========================================================================

def smooth_cmd(data: np.ndarray, sr: int, display_scales: np.ndarray,
               cmd_ch: int = CMD_CH) -> np.ndarray:
    """Median-filter the command-current channel and scale to pA.

    A 1 ms median-filter kernel removes fast transition artifacts while
    preserving the flat plateaus of each current step.

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_samples)
        Raw recording data.
    sr : int
        Sampling rate in Hz.
    display_scales : ndarray
        Per-channel scale factors (raw volts -> display units).
    cmd_ch : int
        Index of the AmpCmd channel.

    Returns
    -------
    ndarray, shape (n_samples,)
        Smoothed command current in pA.
    """
    kernel = max(3, ms_to_samples(1.0, sr) | 1)  # odd, >= 3
    return median_filter(data[cmd_ch] * display_scales[cmd_ch], size=kernel)


def find_pulses(cmd_pA: np.ndarray, sr: int,
                min_amp_pA: float = MIN_AMP_PA,
                min_step_ms: float = MIN_STEP_MS) -> list[dict]:
    """Find contiguous current-injection pulses above a threshold.

    Detects every run of samples where ``|cmd| >= min_amp_pA`` that lasts
    at least ``min_step_ms``.  The amplitude of each pulse is the median
    of the smoothed command signal during the run.

    Parameters
    ----------
    cmd_pA : ndarray
        Smoothed command-current waveform in pA (1-D).
    sr : int
        Sampling rate in Hz.
    min_amp_pA : float
        Minimum absolute amplitude to consider (pA).
    min_step_ms : float
        Minimum duration for a valid pulse (ms).

    Returns
    -------
    list[dict]
        Each dict has ``onset`` (int), ``offset`` (int), and
        ``amplitude_pA`` (float).  Indices are relative to *cmd_pA*.
    """
    min_samples = ms_to_samples(min_step_ms, sr)
    above = np.abs(cmd_pA) >= min_amp_pA

    ab = above.astype(np.int8)
    edges = np.diff(ab)
    rising = np.flatnonzero(edges == 1) + 1
    falling = np.flatnonzero(edges == -1) + 1
    if above[0]:
        rising = np.concatenate(([0], rising))
    if above[-1]:
        falling = np.concatenate((falling, [len(above)]))

    pulses = []
    for onset, offset in zip(rising, falling):
        if offset - onset < min_samples:
            continue
        amp = float(np.median(cmd_pA[onset:offset]))
        pulses.append({
            "onset": int(onset),
            "offset": int(offset),
            "amplitude_pA": amp,
        })
    return pulses


# =========================================================================
# Staircase grouping
# =========================================================================

def _find_protocol_run(apply_sample: int,
                       protocol_runs: list[dict]) -> dict | None:
    """Return the protocol run whose start_sample is closest to (and <=)
    *apply_sample*."""
    best = None
    for run in protocol_runs:
        start = run.get("start_sample", 0)
        if start <= apply_sample:
            if best is None or start > best.get("start_sample", 0):
                best = run
    return best


def _expected_step_count(stim_def: dict,
                         min_amp_pA: float = MIN_AMP_PA) -> int | None:
    """Predict the detectable pulse count for a staircase stimulus.

    Parameters
    ----------
    stim_def : dict
        Raw stimulus definition dict (from protocol JSON).
    min_amp_pA : float
        Detection threshold — amplitudes below this are invisible.

    Returns
    -------
    int or None
        Expected number of pulses, or None if not a staircase.
    """
    if stim_def.get("type") != "staircase":
        return None
    min_pA = stim_def.get("min_pA")
    max_pA = stim_def.get("max_pA")
    step_pA = stim_def.get("step_pA")
    repeats = stim_def.get("staircase_repeats") or 1
    if None in (min_pA, max_pA, step_pA) or step_pA <= 0:
        return None
    n = 0
    val = min_pA
    while val <= max_pA + 1e-9:
        if abs(val) >= min_amp_pA:
            n += 1
        val += step_pA
    return n * repeats


def find_staircases_from_events(
    data: np.ndarray,
    stimulus_events: dict,
    protocol_runs: list[dict],
    sr: int,
    display_scales: np.ndarray,
) -> tuple[list[list[dict]], list[dict]]:
    """Detect one staircase per apply->clear stimulus-event window.

    Pulses are found within each window by thresholding the smoothed AmpCmd
    channel.  When protocol metadata is available, the expected step count
    is included in the per-staircase metadata for validation.

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_samples)
        Full raw recording.
    stimulus_events : dict
        Must contain ``sample_index``, ``event_type``, ``stimulus_name``,
        ``stimulus_index`` arrays.
    protocol_runs : list[dict]
        Parsed protocol runs from the sidecar JSON.
    sr : int
        Sampling rate in Hz.
    display_scales : ndarray
        Per-channel scale factors.

    Returns
    -------
    staircases : list[list[dict]]
        Each staircase is a list of pulse dicts (``onset``, ``offset``,
        ``amplitude_pA``) with sample indices into *data*.
    metadata : list[dict]
        Per-staircase metadata: ``apply_sample``, ``clear_sample``,
        ``stimulus_name``, ``stimulus_index``, ``expected_steps``,
        ``found_steps``.
    """
    cmd_smooth = smooth_cmd(data, sr, display_scales)
    n_total = data.shape[1]

    ev_types = stimulus_events["event_type"]
    ev_samples = stimulus_events["sample_index"]
    ev_names = stimulus_events["stimulus_name"]
    ev_idx = stimulus_events["stimulus_index"]
    n_ev = len(ev_types)

    staircases: list[list[dict]] = []
    meta: list[dict] = []

    for i, etype in enumerate(ev_types):
        if etype != "apply":
            continue
        start_sample = int(ev_samples[i])

        # Find the matching clear (or next apply) to define the window
        end_sample = n_total
        for j in range(i + 1, n_ev):
            if ev_types[j] in ("clear", "apply"):
                end_sample = int(ev_samples[j])
                break

        start_sample = max(0, min(start_sample, n_total))
        end_sample = max(start_sample, min(end_sample, n_total))
        if end_sample <= start_sample:
            continue

        pulses = find_pulses(cmd_smooth[start_sample:end_sample], sr)
        if not pulses:
            continue

        # Shift indices to be relative to the full recording
        steps = [
            {
                "onset": p["onset"] + start_sample,
                "offset": p["offset"] + start_sample,
                "amplitude_pA": p["amplitude_pA"],
            }
            for p in pulses
        ]

        # Check expected step count from protocol if available
        expected = None
        protocol_runs_raw = [
            r.get("protocol_dict") for r in protocol_runs if r.get("protocol_dict")
        ]
        if protocol_runs:
            run = _find_protocol_run(start_sample, protocol_runs)
            if run is not None:
                stim_list = run.get("protocol_dict", {}).get("stimuli", [])
                sidx = int(ev_idx[i])
                if 0 <= sidx < len(stim_list):
                    expected = _expected_step_count(stim_list[sidx])

        staircases.append(steps)
        meta.append({
            "apply_sample": start_sample,
            "clear_sample": end_sample,
            "stimulus_name": ev_names[i],
            "stimulus_index": int(ev_idx[i]),
            "expected_steps": expected,
            "found_steps": len(steps),
        })

    return staircases, meta


def find_staircases_from_waveform(
    data: np.ndarray,
    sr: int,
    display_scales: np.ndarray,
    max_gap_ms: float = MAX_GAP_MS,
) -> tuple[list[list[dict]], list[dict]]:
    """Fallback staircase detection from the AmpCmd waveform alone.

    Detects all pulses in the full recording and groups them into
    staircases separated by silences longer than *max_gap_ms*.

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_samples)
        Full raw recording.
    sr : int
        Sampling rate in Hz.
    display_scales : ndarray
        Per-channel scale factors.
    max_gap_ms : float
        Maximum silence (ms) between consecutive steps in the same
        staircase.

    Returns
    -------
    staircases : list[list[dict]]
        Grouped pulse lists.
    metadata : list[dict]
        Minimal metadata (no stimulus names or expected counts).
    """
    cmd_smooth = smooth_cmd(data, sr, display_scales)
    all_pulses = find_pulses(cmd_smooth, sr)

    max_gap_samples = ms_to_samples(max_gap_ms, sr)
    if not all_pulses:
        return [], []

    staircases: list[list[dict]] = [[all_pulses[0]]]
    for prev, pulse in zip(all_pulses[:-1], all_pulses[1:]):
        if pulse["onset"] - prev["offset"] > max_gap_samples:
            staircases.append([pulse])
        else:
            staircases[-1].append(pulse)

    meta = [
        {
            "apply_sample": sc[0]["onset"],
            "clear_sample": sc[-1]["offset"],
            "stimulus_name": "unknown",
            "stimulus_index": -1,
            "expected_steps": None,
            "found_steps": len(sc),
        }
        for sc in staircases
    ]
    return staircases, meta


# =========================================================================
# Intrinsic property calculations
# =========================================================================

def separate_hyperpol(
    pulses: list[dict],
    protocol_runs: list[dict],
    apply_sample: int,
    stimulus_index: int,
) -> tuple[dict | None, list[dict]]:
    """Separate the hyperpolarization pulse from staircase steps.

    The hyperpolarization pulse is the first negative-amplitude pulse in
    the window, provided the protocol metadata confirms one should exist.
    If no protocol metadata is available, the first negative pulse is
    still treated as hyperpol if it precedes ascending staircase steps.

    Parameters
    ----------
    pulses : list[dict]
        All detected pulses within one apply->clear window.
    protocol_runs : list[dict]
        Parsed protocol runs (may be empty).
    apply_sample : int
        Sample index where the apply event fired.
    stimulus_index : int
        Index into the protocol's stimulus list for this trial.

    Returns
    -------
    hyperpol : dict or None
        The hyperpolarization pulse, or None if not found.
    staircase_steps : list[dict]
        The remaining staircase steps.
    """
    if not pulses:
        return None, []

    # Check if protocol says hyperpol should exist
    expects_hyperpol = False
    if protocol_runs:
        run = _find_protocol_run(apply_sample, protocol_runs)
        if run is not None:
            proto = run.get("protocol")
            if proto is not None and proto.hyperpolarization is not None:
                expects_hyperpol = True

    first = pulses[0]
    if first["amplitude_pA"] < 0 and (expects_hyperpol or len(pulses) > 1):
        return first, pulses[1:]

    return None, pulses


def compute_rmp(
    data: np.ndarray,
    display_scales: np.ndarray,
    baseline_start: int,
    baseline_end: int,
    vm_ch: int = VM_CH,
) -> float:
    """Compute resting membrane potential as the median baseline Vm.

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_samples)
        Raw recording.
    display_scales : ndarray
        Per-channel scale factors.
    baseline_start, baseline_end : int
        Sample indices defining the baseline window.
    vm_ch : int
        Channel index for membrane potential.

    Returns
    -------
    float
        RMP in mV.
    """
    if baseline_end <= baseline_start:
        return float("nan")
    vm = data[vm_ch, baseline_start:baseline_end] * display_scales[vm_ch]
    return float(np.median(vm))


def compute_input_resistance(
    data: np.ndarray,
    display_scales: np.ndarray,
    hyperpol_pulse: dict,
    rmp_mV: float,
    sr: int,
    vm_ch: int = VM_CH,
) -> float:
    """Compute input resistance from the hyperpolarization pulse.

    Uses the last 50% of the pulse to estimate steady-state Vm, then
    calculates Ri = delta_V / delta_I.

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_samples)
        Raw recording.
    display_scales : ndarray
        Per-channel scale factors.
    hyperpol_pulse : dict
        Must have ``onset``, ``offset``, ``amplitude_pA``.
    rmp_mV : float
        Resting membrane potential (mV) from baseline.
    sr : int
        Sampling rate in Hz.
    vm_ch : int
        Channel index for membrane potential.

    Returns
    -------
    float
        Input resistance in MOhm, or NaN if calculation fails.
    """
    onset = hyperpol_pulse["onset"]
    offset = hyperpol_pulse["offset"]
    midpoint = onset + (offset - onset) // 2

    if midpoint >= offset:
        return float("nan")

    vm_during = data[vm_ch, midpoint:offset] * display_scales[vm_ch]
    steady_state_mV = float(np.median(vm_during))

    delta_v_mV = steady_state_mV - rmp_mV
    delta_i_pA = hyperpol_pulse["amplitude_pA"]

    if abs(delta_i_pA) < 1e-9:
        return float("nan")

    # Ri = delta_V (mV) / delta_I (nA)  =>  mV / (pA / 1000) = MOhm
    ri_mohm = delta_v_mV / (delta_i_pA / 1000.0)
    return ri_mohm


def compute_all_intrinsics(
    data: np.ndarray,
    staircases: list[list[dict]],
    staircase_meta: list[dict],
    protocol_runs: list[dict],
    sr: int,
    display_scales: np.ndarray,
) -> list[dict]:
    """Compute RMP and Ri for every detected staircase.

    For each staircase window, the baseline is the silent region between
    the apply event and the first detected pulse.  The hyperpolarization
    pulse (if present) is separated from the staircase steps and used to
    calculate input resistance.

    Parameters
    ----------
    data : ndarray
        Full raw recording.
    staircases : list[list[dict]]
        Each staircase is a list of pulse dicts.
    staircase_meta : list[dict]
        Per-staircase metadata from detection.
    protocol_runs : list[dict]
        Parsed protocol runs.
    sr : int
        Sampling rate in Hz.
    display_scales : ndarray
        Per-channel scale factors.

    Returns
    -------
    list[dict]
        One dict per staircase with keys: ``staircase_index``,
        ``stimulus_name``, ``t_start_s``, ``t_end_s``, ``n_steps``,
        ``min_amp_pA``, ``max_amp_pA``, ``rmp_mV``,
        ``input_resistance_MOhm``.
    """
    margin_samples = ms_to_samples(10, sr)
    results = []

    for idx, (staircase, meta) in enumerate(zip(staircases, staircase_meta)):
        apply_sample = meta["apply_sample"]
        stim_index = meta.get("stimulus_index", -1)

        # Separate hyperpol from staircase steps
        hyperpol, steps = separate_hyperpol(
            staircase, protocol_runs, apply_sample, stim_index
        )

        # Baseline: from apply_sample to first pulse onset (minus margin)
        first_pulse_onset = staircase[0]["onset"]
        baseline_end = max(apply_sample, first_pulse_onset - margin_samples)
        rmp = compute_rmp(data, display_scales, apply_sample, baseline_end)

        # Input resistance from hyperpol pulse
        if hyperpol is not None:
            ri = compute_input_resistance(
                data, display_scales, hyperpol, rmp, sr
            )
        else:
            ri = float("nan")

        # Step amplitudes (from staircase steps only, not hyperpol)
        if steps:
            amps = [s["amplitude_pA"] for s in steps]
            t_start = steps[0]["onset"] / sr
            t_end = steps[-1]["offset"] / sr
        else:
            amps = [s["amplitude_pA"] for s in staircase]
            t_start = staircase[0]["onset"] / sr
            t_end = staircase[-1]["offset"] / sr

        results.append({
            "staircase_index": idx,
            "stimulus_name": meta.get("stimulus_name", "unknown"),
            "t_start_s": round(t_start, 2),
            "t_end_s": round(t_end, 2),
            "n_steps": len(steps),
            "min_amp_pA": round(min(amps), 1) if amps else float("nan"),
            "max_amp_pA": round(max(amps), 1) if amps else float("nan"),
            "rmp_mV": round(rmp, 1),
            "input_resistance_MOhm": round(ri, 1) if not math.isnan(ri) else float("nan"),
        })

    return results


# =========================================================================
# Segment extraction
# =========================================================================

def extract_step_segments(
    data: np.ndarray,
    steps: list[dict],
    sr: int,
    display_scales: np.ndarray,
    pad_ms: float = PAD_MS,
    vm_ch: int = VM_CH,
    cmd_ch: int = CMD_CH,
) -> list[dict]:
    """Extract aligned Vm and I_cmd segments for each step.

    Each segment is aligned so that t=0 corresponds to step onset, with
    *pad_ms* of pre-step baseline shown at negative times.

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_samples)
        Full raw recording.
    steps : list[dict]
        Pulse dicts with ``onset``, ``offset``, ``amplitude_pA``.
    sr : int
        Sampling rate in Hz.
    display_scales : ndarray
        Per-channel scale factors.
    pad_ms : float
        Pre-onset baseline to include (ms).
    vm_ch, cmd_ch : int
        Channel indices.

    Returns
    -------
    list[dict]
        Each dict has ``amplitude_pA``, ``time_ms``, ``vm_mV``,
        ``i_cmd_pA``.
    """
    pad_samples = ms_to_samples(pad_ms, sr)
    n_total = data.shape[1]
    segments = []

    for step in steps:
        onset = step["onset"]
        offset = step["offset"]

        seg_start = max(0, onset - pad_samples)
        seg_end = min(n_total, offset + pad_samples)

        vm = data[vm_ch, seg_start:seg_end] * display_scales[vm_ch]
        i_cmd = data[cmd_ch, seg_start:seg_end] * display_scales[cmd_ch]
        t_ms = (
            (np.arange(seg_end - seg_start) - (onset - seg_start))
            / sr
            * 1000.0
        )

        segments.append({
            "amplitude_pA": step["amplitude_pA"],
            "time_ms": t_ms,
            "vm_mV": vm,
            "i_cmd_pA": i_cmd,
        })

    return segments


# =========================================================================
# Plotting
# =========================================================================

def add_scalebar(
    ax: plt.Axes,
    x_size: float,
    x_unit: str,
    y_size: float,
    y_unit: str,
    x_frac: float = 0.95,
    y_frac: float = -0.01,
) -> None:
    """Draw an L-shaped scale bar inside the axes.

    The bar is anchored at a fractional (x, y) position within the axes
    and sized in data units.

    Parameters
    ----------
    ax : Axes
        Target matplotlib axes.
    x_size, y_size : float
        Scale-bar lengths in data units.
    x_unit, y_unit : str
        Labels for each bar arm.
    x_frac, y_frac : float
        Fractional position of the bar's bottom-right corner
        (0 = left/bottom, 1 = right/top).
    """
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    x_range = xlim[1] - xlim[0]
    y_range = ylim[1] - ylim[0]

    x1 = xlim[0] + x_frac * x_range
    y0 = ylim[0] + y_frac * y_range
    x0 = x1 - x_size
    y1 = y0 + y_size

    ax.plot([x0, x1], [y0, y0], color="black", linewidth=1.5, clip_on=False)
    ax.plot([x1, x1], [y0, y1], color="black", linewidth=1.5, clip_on=False)
    ax.text(
        (x0 + x1) / 2, y0 - 0.02 * y_range, f"{x_size} {x_unit}",
        ha="center", va="top", fontsize=9,
    )
    ax.text(
        x1 + 0.01 * x_range, (y0 + y1) / 2, f"{y_size} {y_unit}",
        ha="left", va="center", fontsize=9,
    )


def plot_step_overlay(
    segment_lists: list[list[dict]],
    title: str = "",
    figsize: tuple[float, float] = (6, 5),
    vm_scalebar: tuple = (100, "ms", 5, "mV"),
    cmd_scalebar: tuple = (100, "ms", 50, "pA"),
    vm_pad_mV: float = 2.0,
    cmd_pad_pA: float = 5.0,
    trace_alpha: float = 0.85,
    sweep_labels: list | None = None,
) -> tuple[plt.Figure, tuple[plt.Axes, plt.Axes]]:
    """Plot overlaid step responses.

    Creates a two-panel figure: membrane potential on top (3:1 height
    ratio) and command current on the bottom, with minimal aesthetics
    (no spines/ticks), L-shaped scale bars, and an RMP marker.

    Coloring:
        - If ``sweep_labels`` is None, traces are colored by injection
          amplitude (black→blue CMAP).
        - If ``sweep_labels`` is provided (one label per sweep), each
          sweep gets a single distinct color sampled from ``Spectral``
          (all traces in that sweep share it). A legend is drawn.

    Parameters
    ----------
    segment_lists : list[list[dict]]
        One inner list per sweep; each entry is a segment dict from
        :func:`extract_step_segments`.
    title : str
        Optional figure title.
    figsize : tuple
        Figure dimensions (width, height) in inches.
    vm_scalebar, cmd_scalebar : tuple
        (x_size, x_unit, y_size, y_unit) for each panel's scale bar.
    vm_pad_mV, cmd_pad_pA : float
        Y-axis padding around data extremes.
    trace_alpha : float
        Opacity of each trace.
    sweep_labels : list or None
        Optional labels, one per sweep. If given, switches coloring
        from amplitude-based to sweep-index-based and draws a legend.

    Returns
    -------
    fig : Figure
    axes : (ax_vm, ax_cmd)
    """
    color_by_sweep = sweep_labels is not None

    if color_by_sweep:
        n_sweeps = len(segment_lists)
        spectral = plt.get_cmap("Spectral")
        sweep_base_colors = [
            spectral(i / max(1, n_sweeps - 1)) for i in range(n_sweeps)
        ]
    else:
        all_amps = sorted({
            seg["amplitude_pA"]
            for sweep_segs in segment_lists
            for seg in sweep_segs
        })
        amp_norm = mcolors.Normalize(vmin=min(all_amps), vmax=max(all_amps))

    fig, (ax_vm, ax_cmd) = plt.subplots(
        2, 1, figsize=figsize, sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.2},
    )

    for sweep_idx, sweep_segs in enumerate(segment_lists):
        for seg in sweep_segs:
            if color_by_sweep:
                color = sweep_base_colors[sweep_idx]
            else:
                color = CMAP(amp_norm(seg["amplitude_pA"]))
            ax_vm.plot(
                seg["time_ms"], seg["vm_mV"],
                color=color, linewidth=0.9, alpha=trace_alpha,
            )
            ax_cmd.plot(
                seg["time_ms"], seg["i_cmd_pA"],
                color=color, linewidth=0.9, alpha=trace_alpha,
            )

    # Tight y-limits from data
    vm_vals = np.concatenate([
        seg["vm_mV"] for segs in segment_lists for seg in segs
    ])
    cmd_vals = np.concatenate([
        seg["i_cmd_pA"] for segs in segment_lists for seg in segs
    ])
    ax_vm.set_ylim(vm_vals.min() - vm_pad_mV, vm_vals.max() + vm_pad_mV)
    ax_cmd.set_ylim(cmd_vals.min() - cmd_pad_pA, cmd_vals.max() + cmd_pad_pA)

    # Remove spines, ticks, and labels
    for ax in (ax_vm, ax_cmd):
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_xlabel("")
        ax.set_ylabel("")

    # RMP marker from pre-step baseline
    baseline_vals = []
    for sweep_segs in segment_lists:
        for seg in sweep_segs:
            mask = seg["time_ms"] < 0
            if np.any(mask):
                baseline_vals.append(np.median(seg["vm_mV"][mask]))
    rmp = np.median(baseline_vals)
    rmp_rounded = round(rmp)

    xlim = ax_vm.get_xlim()
    tick_x = xlim[0]
    ax_vm.plot(tick_x, rmp, marker="_", markersize=8, color="black",
               clip_on=False)
    ax_vm.text(
        tick_x - 0.02 * (xlim[1] - xlim[0]), rmp, f"{rmp_rounded} mV",
        ha="right", va="center", fontsize=9,
    )

    add_scalebar(ax_vm, *vm_scalebar)
    add_scalebar(ax_cmd, *cmd_scalebar)

    if color_by_sweep:
        legend_handles = [
            plt.Line2D([0], [0], color=sweep_base_colors[i], linewidth=1.5,
                       label=str(sweep_labels[i]))
            for i in range(len(segment_lists))
        ]
        ax_vm.legend(
            handles=legend_handles, loc="upper right",
            fontsize=8, frameon=False, title="Trial",
            title_fontsize=8,
        )

    if title:
        fig.suptitle(title, fontsize=13, fontweight="bold")

    fig.tight_layout()
    return fig, (ax_vm, ax_cmd)


# =========================================================================
# Figure saving
# =========================================================================

def save_fig(
    fig: plt.Figure,
    fig_name: str,
    fig_fmt: str,
    fig_dir: str = FIG_DIR,
    fig_size: tuple[float, float] = (6.4, 4),
    dpi: int = 300,
    transparent_png: bool = True,
    overwrite: bool = False,
) -> str | None:
    """Save a figure to ``fig_dir/<fmt>/<date>-<fig_name>.<fmt>``.

    Parameters
    ----------
    fig : Figure
        Matplotlib figure to save.
    fig_name : str
        Base name for the file (no extension).
    fig_fmt : str
        Output format (``"png"``, ``"svg"``, ``"pdf"``).
    fig_dir : str
        Root output directory.
    fig_size : tuple
        Figure dimensions in inches (applied before saving).
    dpi : int
        Resolution for raster formats.
    transparent_png : bool
        If True, save PNG with transparent background.
    overwrite : bool
        If False, skip saving when the file already exists.

    Returns
    -------
    str or None
        The output path, or None if skipped.
    """
    fig.set_size_inches(fig_size, forward=False)
    fig_fmt = fig_fmt.lower()
    out_dir = os.path.join(fig_dir, fig_fmt)
    os.makedirs(out_dir, exist_ok=True)

    fig_date = datetime.today().strftime("%Y-%m-%d")
    pth = os.path.join(out_dir, f"{fig_date}-{fig_name}.{fig_fmt}")

    if os.path.exists(pth) and not overwrite:
        print(f"  (exists, skipping: {pth})")
        return None

    if fig_fmt == "png":
        alpha = 0 if transparent_png else 1
        fig.patch.set_alpha(alpha)
        for ax in fig.get_axes():
            ax.patch.set_alpha(alpha)
        fig.savefig(pth, bbox_inches="tight", dpi=dpi)
    elif fig_fmt == "pdf":
        metadata = {"Creator": "kyle thieringer", "CreationDate": None}
        fig.savefig(pth, bbox_inches="tight", metadata=metadata)
    else:
        fig.savefig(pth, bbox_inches="tight")

    print(f"  Saved: {pth}")
    return pth


def save_fig_both(fig: plt.Figure, fig_name: str, **kwargs) -> None:
    """Save a figure as both PNG and SVG."""
    save_fig(fig, fig_name, "png", **kwargs)
    save_fig(fig, fig_name, "svg", **kwargs)


# =========================================================================
# CSV export
# =========================================================================

def write_csv(intrinsics: list[dict], output_path: Path) -> None:
    """Write intrinsic properties to a CSV file.

    Parameters
    ----------
    intrinsics : list[dict]
        One dict per staircase, as returned by
        :func:`compute_all_intrinsics`.
    output_path : Path
        Destination file path.
    """
    if not intrinsics:
        print("No data to write.")
        return

    os.makedirs(output_path.parent, exist_ok=True)

    fieldnames = [
        "staircase_index",
        "stimulus_name",
        "t_start_s",
        "t_end_s",
        "n_steps",
        "min_amp_pA",
        "max_amp_pA",
        "rmp_mV",
        "input_resistance_MOhm",
    ]

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(intrinsics)

    print(f"\nIntrinsic properties saved to: {output_path}")


# =========================================================================
# Interactive loop
# =========================================================================

def parse_selection(user_input: str, n: int) -> list[int] | None:
    """Parse a user selection string into a list of indices.

    Accepted formats: ``"3"``, ``"0-5"``, ``"0,3,7"``, ``"all"``.

    Parameters
    ----------
    user_input : str
        Raw input string.
    n : int
        Total number of available staircases.

    Returns
    -------
    list[int] or None
        Sorted list of valid indices, or None if input is invalid.
    """
    text = user_input.strip().lower()
    if text == "all":
        return list(range(n))

    indices = set()
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            bounds = part.split("-", 1)
            try:
                lo, hi = int(bounds[0]), int(bounds[1])
            except ValueError:
                return None
            indices.update(range(lo, hi + 1))
        else:
            try:
                indices.add(int(part))
            except ValueError:
                return None

    valid = sorted(i for i in indices if 0 <= i < n)
    return valid if valid else None


def print_summary(
    h5_path: Path,
    info: dict,
    staircases: list[list[dict]],
    staircase_meta: list[dict],
    intrinsics: list[dict],
    detection_source: str,
) -> None:
    """Print a formatted summary of the loaded recording.

    Parameters
    ----------
    h5_path : Path
        Source file.
    info : dict
        Recording info from :func:`load_continuous_h5`.
    staircases : list
        Detected staircases.
    staircase_meta : list
        Per-staircase metadata.
    intrinsics : list[dict]
        Computed intrinsic properties.
    detection_source : str
        How staircases were found (``"stimulus_events"`` or
        ``"waveform"``).
    """
    sr = info["sample_rate"]
    n_ch = info["data"].shape[0]
    duration_s = info["data"].shape[1] / sr

    print(f"\n{'=' * 50}")
    print("  Staircase Analysis")
    print(f"{'=' * 50}")
    print(f"File: {h5_path.name}")
    print(f"  {n_ch} channels, {duration_s:.1f} s @ {sr / 1000:.0f} kHz")
    print(f"  {len(staircases)} staircases detected ({detection_source})")
    print()

    for props in intrinsics:
        idx = props["staircase_index"]
        name = props["stimulus_name"]
        ri_str = (
            f"Ri={props['input_resistance_MOhm']:.0f} MOhm"
            if not math.isnan(props["input_resistance_MOhm"])
            else "Ri=N/A"
        )
        print(
            f"  [{idx:>2d}]  t={props['t_start_s']:.1f}-{props['t_end_s']:.1f}s  "
            f"{props['n_steps']} steps  "
            f"{props['min_amp_pA']:+.0f} to {props['max_amp_pA']:+.0f} pA  "
            f"RMP={props['rmp_mV']:.0f} mV  {ri_str}"
        )
        # Show stimulus name if not trivially "unknown"
        if name and name != "unknown":
            print(f"        [{name}]")

    print()


def interactive_loop(
    h5_path: Path,
    data: np.ndarray,
    staircases: list[list[dict]],
    staircase_meta: list[dict],
    protocol_runs: list[dict],
    intrinsics: list[dict],
    sr: int,
    display_scales: np.ndarray,
) -> None:
    """Run the interactive plot selection loop.

    Prompts the user to select staircases to plot, shows the overlay,
    and asks whether to save each figure.

    Parameters
    ----------
    h5_path : Path
        Source HDF5 file (used for figure naming).
    data : ndarray
        Full raw recording.
    staircases, staircase_meta : list
        Detected staircases and metadata.
    protocol_runs : list[dict]
        Parsed protocol runs.
    intrinsics : list[dict]
        Pre-computed intrinsic properties.
    sr : int
        Sampling rate.
    display_scales : ndarray
        Per-channel scale factors.
    """
    n = len(staircases)
    overlay_mode = True  # toggled with "m"; controls behavior for multi-select

    while True:
        mode_tag = "overlay" if overlay_mode else "separate"
        choice = input(
            f'[{mode_tag}] Enter staircase number(s) to plot '
            f'(e.g. "0", "0-5", "all"), "m" to toggle mode, or "q" to quit: '
        ).strip()

        if choice.lower() == "q":
            break
        if choice.lower() in ("m", "mode"):
            overlay_mode = not overlay_mode
            new_tag = "overlay" if overlay_mode else "separate"
            print(f"  Multi-select mode: {new_tag}\n")
            continue

        indices = parse_selection(choice, n)
        if indices is None:
            print("Invalid selection. Try again.\n")
            continue

        # Single index: plot alone, colored by amplitude.
        # Multi-index in overlay mode: one figure colored by staircase index.
        # Multi-index in separate mode: one figure per staircase (legacy).
        if len(indices) == 1 or not overlay_mode:
            for idx in indices:
                staircase = staircases[idx]
                meta = staircase_meta[idx]
                _, steps = separate_hyperpol(
                    staircase, protocol_runs,
                    meta["apply_sample"], meta.get("stimulus_index", -1),
                )

                if not steps:
                    print(f"  Staircase {idx}: no steps to plot.")
                    continue

                segments = extract_step_segments(data, steps, sr, display_scales)
                fig, _ = plot_step_overlay([segments], title=None)
                plt.show(block=False)
                plt.pause(0.1)

                save_choice = input(
                    f"  Save staircase {idx} figure? [y/n]: "
                ).strip().lower()
                if save_choice == "y":
                    fig_name = f"{h5_path.stem}_staircase{idx}_overlay"
                    save_fig_both(fig, fig_name, overwrite=True)

                plt.close(fig)
        else:
            segment_lists: list[list[dict]] = []
            used_indices: list[int] = []
            for idx in indices:
                staircase = staircases[idx]
                meta = staircase_meta[idx]
                _, steps = separate_hyperpol(
                    staircase, protocol_runs,
                    meta["apply_sample"], meta.get("stimulus_index", -1),
                )
                if not steps:
                    print(f"  Staircase {idx}: no steps, skipping.")
                    continue
                segment_lists.append(
                    extract_step_segments(data, steps, sr, display_scales)
                )
                used_indices.append(idx)

            if not segment_lists:
                print("  No plottable staircases in selection.")
                print()
                continue

            fig, _ = plot_step_overlay(
                segment_lists, title=None,
                sweep_labels=used_indices,
            )
            plt.show(block=False)
            plt.pause(0.1)

            label = "_".join(str(i) for i in used_indices)
            save_choice = input(
                f"  Save overlay of staircases {used_indices}? [y/n]: "
            ).strip().lower()
            if save_choice == "y":
                fig_name = f"{h5_path.stem}_staircases_{label}_overlay"
                save_fig_both(fig, fig_name, overwrite=True)

            plt.close(fig)

        print()


# =========================================================================
# Main
# =========================================================================

def main() -> None:
    """Entry point: load file, detect staircases, compute intrinsics,
    and run the interactive plotting loop."""

    # ── File selection ────────────────────────────────────────────────
    h5_path = select_file()
    if h5_path is None:
        print("No file selected. Exiting.")
        return

    # ── Load data ─────────────────────────────────────────────────────
    try:
        info = load_continuous_h5(h5_path)
    except ValueError as e:
        print(f"Error: {e}")
        return

    data = info["data"]
    sr = info["sample_rate"]
    display_scales = info["display_scales"]
    stimulus_events = info["stimulus_events"]

    # ── Load protocol metadata ────────────────────────────────────────
    protocol_runs = load_protocol_metadata(h5_path)

    # ── Detect staircases ─────────────────────────────────────────────
    if stimulus_events is not None:
        staircases, staircase_meta = find_staircases_from_events(
            data, stimulus_events, protocol_runs, sr, display_scales,
        )
        detection_source = "stimulus_events"
    else:
        staircases, staircase_meta = [], []
        detection_source = None

    # Fallback to waveform detection if no events or no staircases found
    if not staircases:
        if detection_source is None:
            print("No stimulus events found. Falling back to waveform detection.")
        else:
            print("No staircases found via stimulus events. "
                  "Falling back to waveform detection.")
        staircases, staircase_meta = find_staircases_from_waveform(
            data, sr, display_scales,
        )
        detection_source = "waveform"

    if not staircases:
        print("No staircases detected in this recording. Exiting.")
        return

    # ── Compute intrinsic properties ──────────────────────────────────
    intrinsics = compute_all_intrinsics(
        data, staircases, staircase_meta, protocol_runs, sr, display_scales,
    )

    # Check for missing Ri values
    n_missing_ri = sum(
        1 for r in intrinsics if math.isnan(r["input_resistance_MOhm"])
    )
    if n_missing_ri == len(intrinsics):
        print(
            "\nWarning: No hyperpolarization pulse detected in any trial. "
            "Input resistance could not be calculated."
        )
    elif n_missing_ri > 0:
        print(
            f"\nWarning: Input resistance unavailable for {n_missing_ri} "
            f"of {len(intrinsics)} trials."
        )

    # ── Print summary ─────────────────────────────────────────────────
    print_summary(
        h5_path, info, staircases, staircase_meta, intrinsics,
        detection_source,
    )

    # ── Interactive plotting ──────────────────────────────────────────
    interactive_loop(
        h5_path, data, staircases, staircase_meta, protocol_runs,
        intrinsics, sr, display_scales,
    )

    # ── Save CSV ──────────────────────────────────────────────────────
    fig_date = datetime.today().strftime("%Y-%m-%d")
    csv_path = Path(FIG_DIR) / "csv" / f"{h5_path.stem}_intrinsics.csv"
    write_csv(intrinsics, csv_path)

    print("\nDone.")


if __name__ == "__main__":
    main()

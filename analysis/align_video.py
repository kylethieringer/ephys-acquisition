#!/usr/bin/env python3
"""
Align video frames with ephys data using the TTL trigger channel.

Produces a composite video where each frame shows:
  - Top panel:    camera image at time t
  - Bottom panel: membrane potential (mV) trace, ±1 s centered on t,
                  with a centre line marking the current frame time

Frame timestamps are derived from rising edges on channel 4 (TTLLoopback),
which fires one pulse per camera frame.

Usage — continuous-mode HDF5:
    python align_video.py recording.h5 video.avi output.avi

Usage — trial-mode HDF5 (pick one trial):
    python align_video.py trials.h5 video.avi output.avi --trial 3

If output path is omitted, the file is written next to the HDF5 as
<stem>_aligned.avi (continuous) or <stem>_trial<N>_aligned.avi (trial).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import h5py
import numpy as np
import cv2

# tkinter is only needed for the GUI dialog (used when no CLI args given)
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
    _HAS_TK = True
except ImportError:
    _HAS_TK = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TTL_CHANNEL   = 4      # TTLLoopback  — 5th channel (0-indexed)
VM_CHANNEL    = 0      # ScAmpOut     — membrane potential (1st channel)
TTL_THRESHOLD = 2.5    # V  — rising-edge detection threshold
HALF_WIN_S    = 1.0    # seconds displayed either side of current frame
TRACE_HEIGHT  = 200    # pixels — height of trace panel appended below video

# BGR colours (OpenCV convention)
_BG  = (  0,   0,   0)   # black background
_FG  = (255, 191,   0)   # gold trace
_CL  = (200, 200, 200)   # centre-line white
_AX  = (120, 120, 130)   # axis / tick grey
_TXT = (220, 220, 230)   # label text
_RMP = (152, 255, 152)   # mint green — resting membrane potential marker


# ---------------------------------------------------------------------------
# HDF5 loading
# ---------------------------------------------------------------------------

def _is_trial_mode(h5_path: Path) -> bool:
    with h5py.File(h5_path, "r") as f:
        return any(k.startswith("trial_") for k in f.keys())


def _load_continuous(h5_path: Path) -> tuple[np.ndarray, np.ndarray, float]:
    """Return (vm_mV, ttl_V, sample_rate) from a continuous-mode HDF5."""
    with h5py.File(h5_path, "r") as f:
        sr     = float(f["metadata"].attrs["sample_rate"])
        scales = f["metadata"]["display_scales"][:]
        data   = f["data"]["analog_input"][:]
    vm  = data[VM_CHANNEL]  * scales[VM_CHANNEL]
    ttl = data[TTL_CHANNEL] * scales[TTL_CHANNEL]
    return vm, ttl, sr


def _load_trial(h5_path: Path, trial_num: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Return (vm_mV, ttl_V, sample_rate) from trial N of a trial-mode HDF5."""
    key = f"trial_{trial_num:03d}"
    with h5py.File(h5_path, "r") as f:
        available = sorted(k for k in f.keys() if k.startswith("trial_"))
        if key not in f:
            raise KeyError(f"'{key}' not found in {h5_path.name}. Available: {available}")
        sr     = float(f["metadata"].attrs["sample_rate"])
        scales = f["metadata"]["display_scales"][:]
        data   = f[key]["analog_input"][:]
    vm  = data[VM_CHANNEL]  * scales[VM_CHANNEL]
    ttl = data[TTL_CHANNEL] * scales[TTL_CHANNEL]
    return vm, ttl, sr


# ---------------------------------------------------------------------------
# TTL analysis
# ---------------------------------------------------------------------------

def find_frame_samples(ttl: np.ndarray, threshold: float = TTL_THRESHOLD) -> np.ndarray:
    """Return sample indices of rising edges in the TTL trigger signal.

    Each rising edge marks the moment the DAQ fired a trigger pulse to
    the camera, so edge i corresponds to the start of video frame i.
    """
    above = (ttl >= threshold).astype(np.int8)
    diff  = np.diff(above, prepend=np.int8(0))
    return np.where(diff == 1)[0]


# ---------------------------------------------------------------------------
# Trace rendering
# ---------------------------------------------------------------------------

def _y_range(vm: np.ndarray, pad_frac: float = 0.08) -> tuple[float, float]:
    """Y-axis range using the full min/max of the Vm trace."""
    lo = float(np.nanmin(vm))
    hi = float(np.nanmax(vm))
    margin = (hi - lo) * pad_frac
    return lo - margin, hi + margin


def _compute_rmp(vm: np.ndarray) -> float:
    """Estimate resting membrane potential as the median of the Vm trace.

    The median is robust to action potentials, which are brief positive
    deflections that do not strongly affect the middle of the distribution.
    """
    return float(np.nanmedian(vm))


def _minmax_envelope(window: np.ndarray, n_out: int) -> tuple[np.ndarray, np.ndarray]:
    """Downsample *window* to *n_out* columns using a min-max envelope.

    For each output pixel column, the minimum and maximum of all input
    samples that fall in that column's time bin are returned.  This
    preserves the full amplitude of transients (spikes) regardless of
    how many raw samples map to each pixel.

    Returns:
        col_min, col_max — each shape (n_out,), NaN where no valid samples.
    """
    n     = len(window)
    edges = np.linspace(0, n, n_out + 1)
    lo_idx = np.floor(edges[:-1]).astype(int)
    hi_idx = np.minimum(np.ceil(edges[1:]).astype(int), n)

    col_min = np.full(n_out, np.nan)
    col_max = np.full(n_out, np.nan)
    for col in range(n_out):
        seg   = window[lo_idx[col] : hi_idx[col]]
        valid = seg[~np.isnan(seg)]
        if len(valid):
            col_min[col] = valid.min()
            col_max[col] = valid.max()
    return col_min, col_max


def _draw_trace(
    vm:             np.ndarray,
    center_sample:  int,
    sr:             float,
    width:          int,
    y_lo:           float,
    y_hi:           float,
    rmp:            float,
    height:         int = TRACE_HEIGHT,
) -> np.ndarray:
    """Render a BGR trace image (height × width) centred on *center_sample*.

    The x axis spans [center_sample - HALF_WIN_S*sr, center_sample + HALF_WIN_S*sr].
    A dashed vertical line marks the current time (centre, t = 0).
    A faint horizontal line marks the resting membrane potential.
    Regions outside the recording are left as background.
    The trace is rendered using a min-max envelope so spike amplitudes are
    preserved regardless of how many raw samples map to each display pixel.
    """
    half  = int(HALF_WIN_S * sr)
    n     = len(vm)
    lo    = center_sample - half
    hi    = center_sample + half

    # Extract window, padding with NaN where outside the recording
    window = np.full(hi - lo, np.nan)
    src_lo = max(lo, 0)
    src_hi = min(hi, n)
    if src_lo < src_hi:
        window[src_lo - lo : src_hi - lo] = vm[src_lo:src_hi]

    # ---- layout constants ----
    pad_top = 18
    pad_bot = 32
    pad_lft = 48   # room for y-axis labels
    draw_h  = height - pad_top - pad_bot
    draw_w  = width  - pad_lft
    baseline = pad_top + draw_h

    img = np.full((height, width, 3), _BG, dtype=np.uint8)

    def _y_px(v: float) -> int:
        frac = (v - y_lo) / (y_hi - y_lo + 1e-12)
        return int(pad_top + (1.0 - frac) * draw_h)

    def _put(text: str, x: int, y: int, scale: float = 0.38,
             color: tuple = _TXT) -> None:
        cv2.putText(img, text, (x, y),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)

    # ---- y-axis: only limits + RMP ----
    for v, color in [(y_lo, _AX), (rmp, _RMP), (y_hi, _AX)]:
        py = _y_px(v)
        cv2.line(img, (pad_lft - 6, py), (pad_lft, py), color, 1)
        label = f"{v:.0f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.35, 1)
        _put(label, pad_lft - 7 - tw, py + th // 2, scale=0.35, color=color)

    _put("mV", 2, pad_top, scale=0.35)

    # faint dashed horizontal line at RMP across the plot area
    rmp_py = _y_px(rmp)
    for dx in range(pad_lft, width, 8):
        cv2.line(img, (dx, rmp_py), (min(dx + 4, width - 1), rmp_py), _RMP, 1)

    # left axis line
    cv2.line(img, (pad_lft, pad_top), (pad_lft, baseline), _AX, 1)
    # bottom axis line
    cv2.line(img, (pad_lft, baseline), (width - 1, baseline), _AX, 1)

    # ---- x-axis ticks ----
    for t in (-1.0, -0.5, 0.0, 0.5, 1.0):
        frac = (t + HALF_WIN_S) / (2 * HALF_WIN_S)
        px   = pad_lft + int(frac * (draw_w - 1))
        cv2.line(img, (px, baseline), (px, baseline + 4), _AX, 1)
        label = f"{t:+.1f}s" if t != 0.0 else "0"
        (tw, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.35, 1)
        _put(label, px - tw // 2, height - 4, scale=0.35)

    # ---- centre line (dashed) ----
    cx = pad_lft + draw_w // 2
    for dy in range(pad_top, baseline, 6):
        cv2.line(img, (cx, dy), (cx, min(dy + 3, baseline)), _CL, 1)

    # ---- trace (min-max envelope as polylines) ----
    col_min, col_max = _minmax_envelope(window, draw_w)
    seg_max: list[tuple[int, int]] = []
    seg_min: list[tuple[int, int]] = []
    segs_max: list[list[tuple[int, int]]] = []
    segs_min: list[list[tuple[int, int]]] = []
    for col in range(draw_w):
        if np.isnan(col_min[col]):
            if seg_max:
                segs_max.append(seg_max); segs_min.append(seg_min)
                seg_max = []; seg_min = []
            continue
        x = pad_lft + col
        seg_max.append((x, _y_px(float(col_max[col]))))
        seg_min.append((x, _y_px(float(col_min[col]))))
    if seg_max:
        segs_max.append(seg_max); segs_min.append(seg_min)

    for seg in segs_max:
        cv2.polylines(img, [np.array(seg, dtype=np.int32)], False, _FG, 1, cv2.LINE_AA)
    for seg in segs_min:
        cv2.polylines(img, [np.array(seg, dtype=np.int32)], False, _FG, 1, cv2.LINE_AA)

    return img


# ---------------------------------------------------------------------------
# Main alignment routine
# ---------------------------------------------------------------------------

def align(
    h5_path:    Path,
    video_path: Path,
    output_path: Path,
    trial_num:  int | None = None,
) -> None:
    """Create the aligned composite video.

    Args:
        h5_path:     Path to the HDF5 electrophysiology file.
        video_path:  Path to the input camera video (.avi).
        output_path: Destination for the composite video.
        trial_num:   Trial number (1-based) for trial-mode HDF5.
                     Ignored for continuous-mode files.
    """
    # --- load ephys --------------------------------------------------------
    if _is_trial_mode(h5_path):
        if trial_num is None:
            trial_num = 1
            print(f"[info] Trial-mode HDF5 detected; defaulting to trial {trial_num}.")
        print(f"[info] Loading trial {trial_num} from {h5_path.name} …")
        vm, ttl, sr = _load_trial(h5_path, trial_num)
    else:
        print(f"[info] Loading continuous recording from {h5_path.name} …")
        vm, ttl, sr = _load_continuous(h5_path)

    # --- find frame timestamps ---------------------------------------------
    frame_samples = find_frame_samples(ttl)
    n_edges = len(frame_samples)
    if n_edges == 0:
        sys.exit(
            "[error] No TTL rising edges found in channel 4 (TTLLoopback). "
            "Check that the correct HDF5 file and TTL channel are used."
        )
    print(f"[info] Found {n_edges} TTL rising edges → frame timestamps.")

    # --- open video --------------------------------------------------------
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        sys.exit(f"[error] Cannot open video: {video_path}")

    n_video_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps            = cap.get(cv2.CAP_PROP_FPS)
    vid_w          = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vid_h          = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    n_frames = min(n_edges, n_video_frames)
    if n_edges != n_video_frames:
        print(
            f"[warn] TTL edges ({n_edges}) ≠ video frames ({n_video_frames}). "
            f"Using first {n_frames} frames."
        )

    # --- prepare output writer --------------------------------------------
    out_h   = vid_h + TRACE_HEIGHT
    fourcc  = cv2.VideoWriter_fourcc(*"MJPG")
    writer  = cv2.VideoWriter(str(output_path), fourcc, fps, (vid_w, out_h))
    if not writer.isOpened():
        sys.exit(f"[error] Cannot open output video for writing: {output_path}")

    # --- pre-compute y range and RMP from the full Vm trace ---------------
    y_lo, y_hi = _y_range(vm)
    rmp = _compute_rmp(vm)
    print(f"[info] Resting membrane potential: {rmp:.1f} mV")

    # --- render frames -----------------------------------------------------
    print(f"[info] Writing {n_frames} frames to {output_path.name} …")
    for i in range(n_frames):
        ret, frame = cap.read()
        if not ret:
            print(f"[warn] Could not read video frame {i}; stopping early.")
            break

        center_sample = int(frame_samples[i])
        trace_img     = _draw_trace(vm, center_sample, sr, vid_w, y_lo, y_hi, rmp)

        # Resize frame to match video width if needed (should be a no-op)
        if frame.shape[1] != vid_w:
            frame = cv2.resize(frame, (vid_w, vid_h))

        composite = np.vstack([frame, trace_img])
        writer.write(composite)

        # Progress: print every 5% of frames
        step = max(1, n_frames // 20)
        if i % step == 0 or i == n_frames - 1:
            pct = 100 * (i + 1) / n_frames
            print(f"\r  {pct:5.1f}%  frame {i+1}/{n_frames}", end="", flush=True)

    print()  # newline after progress

    cap.release()
    writer.release()
    print(f"[done] Saved → {output_path}")


# ---------------------------------------------------------------------------
# GUI file-picker dialog
# ---------------------------------------------------------------------------

def _gui_select_files() -> tuple[Path, Path, Path, int | None] | None:
    """Show a single window where the user can review and change file selections.

    Returns (h5_path, video_path, output_path, trial_num) or None if cancelled.
    """
    result: list = [None]  # mutable container so nested functions can write it

    root = tk.Tk()
    root.title("Align Video — File Selection")
    root.resizable(False, False)

    pad = dict(padx=8, pady=4)

    # --- row helpers ----------------------------------------------------------
    def _make_row(parent, row: int, label: str, browse_cmd):
        tk.Label(parent, text=label, anchor="w").grid(
            row=row, column=0, sticky="w", **pad,
        )
        var = tk.StringVar()
        entry = tk.Entry(parent, textvariable=var, width=60)
        entry.grid(row=row, column=1, sticky="ew", **pad)
        tk.Button(parent, text="Browse…", command=browse_cmd).grid(
            row=row, column=2, **pad,
        )
        return var, entry

    # --- browse callbacks -----------------------------------------------------
    def _browse_h5():
        path = filedialog.askopenfilename(
            title="Select HDF5 data file",
            filetypes=[("HDF5 files", "*.h5 *.hdf5"), ("All files", "*.*")],
        )
        if path:
            h5_var.set(path)
            _update_trial_row()
            # auto-fill output name based on new h5 selection
            if not out_var.get():
                _suggest_output()

    def _browse_video():
        path = filedialog.askopenfilename(
            title="Select video file",
            filetypes=[("Video files", "*.avi *.mp4"), ("All files", "*.*")],
        )
        if path:
            video_var.set(path)

    def _browse_output():
        init_dir = ""
        h5_str = h5_var.get()
        if h5_str:
            init_dir = str(Path(h5_str).parent)
        path = filedialog.asksaveasfilename(
            title="Save aligned video as",
            defaultextension=".avi",
            filetypes=[("AVI video", "*.avi"), ("All files", "*.*")],
            initialdir=init_dir,
        )
        if path:
            out_var.set(path)

    def _suggest_output():
        h5_str = h5_var.get()
        if h5_str:
            p = Path(h5_str)
            out_var.set(str(p.parent / f"{p.stem}_aligned.avi"))

    # --- trial row visibility -------------------------------------------------
    trial_var = tk.StringVar(value="1")
    trial_widgets: list[tk.Widget] = []

    def _update_trial_row():
        h5_str = h5_var.get()
        is_trial = False
        if h5_str and Path(h5_str).exists():
            try:
                is_trial = _is_trial_mode(Path(h5_str))
            except Exception:
                pass
        for w in trial_widgets:
            if is_trial:
                w.grid()
            else:
                w.grid_remove()

    # --- build rows -----------------------------------------------------------
    h5_var, _    = _make_row(root, 0, "Data file (.h5):", _browse_h5)
    video_var, _ = _make_row(root, 1, "Video file:",      _browse_video)
    out_var, _   = _make_row(root, 2, "Output file:",     _browse_output)

    # trial number row (hidden by default)
    lbl = tk.Label(root, text="Trial number:", anchor="w")
    lbl.grid(row=3, column=0, sticky="w", **pad)
    trial_entry = tk.Entry(root, textvariable=trial_var, width=10)
    trial_entry.grid(row=3, column=1, sticky="w", **pad)
    trial_widgets.extend([lbl, trial_entry])
    for w in trial_widgets:
        w.grid_remove()

    root.columnconfigure(1, weight=1)

    # --- buttons --------------------------------------------------------------
    btn_frame = tk.Frame(root)
    btn_frame.grid(row=4, column=0, columnspan=3, pady=10)

    def _on_run():
        h5_str    = h5_var.get().strip()
        vid_str   = video_var.get().strip()
        out_str   = out_var.get().strip()
        if not h5_str or not vid_str or not out_str:
            messagebox.showwarning("Missing fields", "Please fill in all three file paths.")
            return
        trial_num = None
        h5_path = Path(h5_str).resolve()
        if h5_path.exists():
            try:
                if _is_trial_mode(h5_path):
                    try:
                        trial_num = int(trial_var.get())
                    except ValueError:
                        messagebox.showwarning("Invalid trial", "Trial number must be an integer.")
                        return
            except Exception:
                pass
        result[0] = (h5_path, Path(vid_str).resolve(), Path(out_str).resolve(), trial_num)
        root.destroy()

    def _on_cancel():
        root.destroy()

    tk.Button(btn_frame, text="Run", width=10, command=_on_run).pack(side="left", padx=8)
    tk.Button(btn_frame, text="Cancel", width=10, command=_on_cancel).pack(side="left", padx=8)

    root.protocol("WM_DELETE_WINDOW", _on_cancel)
    root.mainloop()

    return result[0]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_output(h5_path: Path, trial_num: int | None) -> Path:
    stem = h5_path.stem
    if trial_num is not None:
        return h5_path.parent / f"{stem}_trial{trial_num:03d}_aligned.avi"
    return h5_path.parent / f"{stem}_aligned.avi"


def main() -> None:
    global VM_CHANNEL, TTL_CHANNEL, HALF_WIN_S, TRACE_HEIGHT

    # No CLI args → launch GUI file picker
    if len(sys.argv) == 1:
        if not _HAS_TK:
            sys.exit("[error] tkinter is not available. Provide file paths as CLI arguments.")
        result = _gui_select_files()
        if result is None:
            sys.exit("[info] Cancelled.")
        h5_path, video_path, out_path, trial_num = result
        if not h5_path.exists():
            sys.exit(f"[error] HDF5 file not found: {h5_path}")
        if not video_path.exists():
            sys.exit(f"[error] Video file not found: {video_path}")
        if out_path.exists():
            sys.exit(f"[error] Output file already exists: {out_path}\n"
                     "        Rename or move it before running again.")
        align(h5_path, video_path, out_path, trial_num=trial_num)
        return

    parser = argparse.ArgumentParser(
        description="Align camera video with ephys membrane-potential recording.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("h5_file",    type=Path, help="HDF5 ephys recording")
    parser.add_argument("video_file", type=Path, help="Input camera video (.avi)")
    parser.add_argument(
        "output_file", type=Path, nargs="?", default=None,
        help="Output composite video (default: <h5_stem>_aligned.avi next to HDF5)",
    )
    parser.add_argument(
        "--trial", type=int, default=None, metavar="N",
        help="Trial number (1-based) to extract from a trial-mode HDF5",
    )
    parser.add_argument(
        "--vm-channel", type=int, default=VM_CHANNEL, metavar="CH",
        help=f"AI channel index for membrane potential (default: {VM_CHANNEL})",
    )
    parser.add_argument(
        "--ttl-channel", type=int, default=TTL_CHANNEL, metavar="CH",
        help=f"AI channel index for TTL loopback (default: {TTL_CHANNEL})",
    )
    parser.add_argument(
        "--half-window", type=float, default=HALF_WIN_S, metavar="SEC",
        help=f"Seconds displayed either side of current frame (default: {HALF_WIN_S})",
    )
    parser.add_argument(
        "--trace-height", type=int, default=TRACE_HEIGHT, metavar="PX",
        help=f"Pixel height of the trace panel (default: {TRACE_HEIGHT})",
    )
    args = parser.parse_args()

    # Override module-level constants so all functions pick up the values.
    VM_CHANNEL   = args.vm_channel
    TTL_CHANNEL  = args.ttl_channel
    HALF_WIN_S   = args.half_window
    TRACE_HEIGHT = args.trace_height

    h5_path    = args.h5_file.resolve()
    video_path = args.video_file.resolve()
    out_path   = (args.output_file or _default_output(h5_path, args.trial)).resolve()

    if not h5_path.exists():
        sys.exit(f"[error] HDF5 file not found: {h5_path}")
    if not video_path.exists():
        sys.exit(f"[error] Video file not found: {video_path}")
    if out_path.exists():
        sys.exit(f"[error] Output file already exists: {out_path}\n"
                 "        Rename or move it before running again.")

    align(h5_path, video_path, out_path, trial_num=args.trial)


if __name__ == "__main__":
    main()

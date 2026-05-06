from pathlib import Path
import h5py
import numpy as np
import cv2
import sys

# ---------------------------------------------------------------------------
# Constants - set these based on hardware config
# ---------------------------------------------------------------------------

TTL_CHANNEL   = 4      # TTLLoopback  — 5th channel (0-indexed)
DAT_CHANNEL   = 0      # signal channel — 1st channel (0-indexed)
TTL_THRESHOLD = 2.5    # V  — rising-edge detection threshold
HALF_WIN_S    = 1.0    # seconds displayed either side of current frame
TRACE_HEIGHT  = 200    # pixels — height of trace panel appended below video

# BGR colours (OpenCV convention)
_BG  = (  0,   0,   0)   # black background
_FG  = (255, 191,   0)   # gold trace
_CL  = (200, 200, 200)   # centre-line white
_AX  = (120, 120, 130)   # axis / tick grey
_TXT = (220, 220, 230)   # label text


# ---------------------------------------------------------------------------
# loading data: will need to edit this for your data format
# ---------------------------------------------------------------------------

def _load_h5data(h5_path: Path):
    """Return (signal, ttl, sample_rate).

    signal : 1-D float array — the analog signal you want plotted under each frame
    ttl    : 1-D float array, same length as `signal` — TTL trigger trace
    sample_rate : float, Hz — samples per second for both arrays
    """
    with h5py.File(h5_path, "r") as f:
        signal = f["data"][:]
        ttl = f["ttl"][:]
        sr = f["sample_rate"][()]
    
    return signal, ttl, sr


def find_frame_samples(ttl: np.ndarray, threshold: float = TTL_THRESHOLD):
    """Return sample indices of rising edges in the TTL trigger signal.

    Each rising edge marks the moment the DAQ fired a trigger pulse to
    the camera, so edge i corresponds to the start of video frame i.
    """
    above = (ttl >= threshold).astype(np.int8)
    diff  = np.diff(above, prepend=np.int8(0))
    return np.where(diff == 1)[0]

# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _y_range(signal) -> tuple[float, float]:
    """Y-axis range using the full min/max of the signal trace."""
    lo = float(np.nanmin(signal))
    hi = float(np.nanmax(signal))
    margin = (hi - lo) * pad_frac
    return lo - margin, hi + margin


def _minmax_envelope(window: np.ndarray, n_out: int):
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
    data:             np.ndarray,
    center_sample:  int,
    sr:             float,
    width:          int,
    y_lo:           float,
    y_hi:           float,
    height:         int = TRACE_HEIGHT,
) :
    """Render a BGR trace image (height × width) centred on *center_sample*.

    The x axis spans [center_sample - HALF_WIN_S*sr, center_sample + HALF_WIN_S*sr].
    A dashed vertical line marks the current time (centre, t = 0). 
    Regions outside the recording are left as background.
    
    """
    half  = int(HALF_WIN_S * sr)
    n     = len(data)
    lo    = center_sample - half
    hi    = center_sample + half

    # Extract window, padding with NaN where outside the recording
    window = np.full(hi - lo, np.nan)
    src_lo = max(lo, 0)
    src_hi = min(hi, n)
    if src_lo < src_hi:
        window[src_lo - lo : src_hi - lo] = data[src_lo:src_hi]

    # ---- layout constants ----
    pad_top = 18
    pad_bot = 32
    pad_lft = 48   # room for y-axis labels
    draw_h  = height - pad_top - pad_bot
    draw_w  = width  - pad_lft
    baseline = pad_top + draw_h

    img = np.full((height, width, 3), _BG, dtype=np.uint8)

    def _y_px(v: float):
        frac = (v - y_lo) / (y_hi - y_lo + 1e-12)
        return int(pad_top + (1.0 - frac) * draw_h)

    def _put(text: str, x: int, y: int, scale: float = 0.38,
             color: tuple = _TXT) -> None:
        cv2.putText(img, text, (x, y),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)

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

    # ---- trace (filled min-max envelope as vertical lines) ----
    col_min, col_max = _minmax_envelope(window, draw_w)
    for col in range(draw_w):
        if np.isnan(col_min[col]):
            continue
        x = pad_lft + col
        y_top = _y_px(float(col_max[col]))
        y_bot = _y_px(float(col_min[col]))
        cv2.line(img, (x, y_top), (x, y_bot), _FG, 1)

    return img

def align(
    h5_path:    Path,
    video_path: Path,
    output_path: Path,
) :
    """Create the aligned composite video.

    Args:
        h5_path:     Path to the HDF5 electrophysiology file.
        video_path:  Path to the input camera video (.avi).
        output_path: Destination for the composite video.

    """

    # --- load ephys --------------------------------------------------------
   
    print(f"[info] Loading recording from {h5_path.name} …")
    signal, ttl, sr = _load_h5data(h5_path)

    # --- find frame timestamps ---------------------------------------------
    frame_samples = find_frame_samples(ttl)
    n_edges = len(frame_samples)
    if n_edges == 0:
        sys.exit(
            f"[error] No TTL rising edges found in channel {TTL_CHANNEL} (TTLLoopback). "
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

    # --- pre-compute y range from the full signal trace ---------------
    y_lo, y_hi = _y_range(signal)

    # --- render frames -----------------------------------------------------
    print(f"[info] Writing {n_frames} frames to {output_path.name} …")
    for i in range(n_frames):
        ret, frame = cap.read()
        if not ret:
            print(f"[warn] Could not read video frame {i}; stopping early.")
            break

        center_sample = int(frame_samples[i])
        trace_img     = _draw_trace(signal, center_sample, sr, vid_w, y_lo, y_hi)

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


def main() -> None:

    h5_path = ""
    video_path = ""
    out_path = ""

    if out_path.exists():
        sys.exit(f"[error] Output file already exists: {out_path}\n"
                 "        Rename or move it before running again.")

    align(h5_path, video_path, out_path)


if __name__ == "__main__":
    main()
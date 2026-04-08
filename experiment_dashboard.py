"""
experiment_dashboard.py — Interactive experiment browser.

Run with:
    streamlit run experiment_dashboard.py
    streamlit run experiment_dashboard.py -- --data-dir E:/data
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="Ephys Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Global ── */
[data-testid="stAppViewContainer"] { background: #0d1117; }
[data-testid="stSidebar"] { background: #161b22; border-right: 1px solid #30363d; }
[data-testid="stSidebar"] * { color: #c9d1d9 !important; }

/* ── Metrics ── */
[data-testid="stMetric"] {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 12px 16px;
}
[data-testid="stMetricLabel"] { color: #8b949e !important; font-size: 0.78rem !important; }
[data-testid="stMetricValue"] { color: #58a6ff !important; font-size: 1.6rem !important; }

/* ── Section headers ── */
h1 { color: #e6edf3 !important; font-size: 1.6rem !important; font-weight: 700 !important; }
h2, h3 { color: #c9d1d9 !important; }

/* ── Dataframe ── */
[data-testid="stDataFrame"] { border: 1px solid #30363d; border-radius: 8px; }

/* ── Expanders ── */
[data-testid="stExpander"] {
    background: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
}
[data-testid="stExpander"] summary { color: #c9d1d9 !important; }

/* ── Filter labels ── */
.sidebar-label { color: #8b949e; font-size: 0.75rem; text-transform: uppercase;
                 letter-spacing: 0.08em; margin-bottom: 2px; }

/* ── Tag chips ── */
.tag {
    display: inline-block;
    background: #1f6feb33;
    border: 1px solid #1f6feb;
    color: #58a6ff;
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 0.78rem;
    margin: 2px;
}
.tag-green  { background: #23863633; border-color: #238636; color: #3fb950; }
.tag-yellow { background: #9e6a0333; border-color: #9e6a03; color: #d29922; }
.tag-red    { background: #da363633; border-color: #da3633; color: #f85149; }
.tag-gray   { background: #30363d;   border-color: #484f58; color: #8b949e; }

/* ── File list ── */
.file-row {
    display: flex; align-items: center; gap: 8px;
    padding: 5px 0; border-bottom: 1px solid #21262d;
    font-size: 0.84rem; color: #c9d1d9;
}
.file-ext { color: #8b949e; font-family: monospace; min-width: 40px; }
.file-size { color: #8b949e; margin-left: auto; font-size: 0.78rem; }

/* ── Info table ── */
.info-grid { display: grid; grid-template-columns: 140px 1fr; gap: 4px 12px;
             font-size: 0.84rem; }
.info-key  { color: #8b949e; }
.info-val  { color: #e6edf3; font-family: monospace; }

/* ── Divider ── */
hr { border-color: #21262d !important; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_size(n_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


def _file_icon(ext: str) -> str:
    return {
        ".h5": "🗄️", ".hdf5": "🗄️",
        ".bin": "📦", ".json": "📋",
        ".avi": "🎬", ".mp4": "🎬",
    }.get(ext.lower(), "📄")


def _tag(text: str, color: str = "") -> str:
    cls = f"tag tag-{color}" if color else "tag"
    return f'<span class="{cls}">{text}</span>'


@st.cache_data(ttl=30)
def load_csv(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    if "start_time" in df.columns:
        df["start_time"] = pd.to_datetime(df["start_time"], errors="coerce")
    if "end_time" in df.columns:
        df["end_time"] = pd.to_datetime(df["end_time"], errors="coerce")
    if "duration_seconds" in df.columns:
        df["duration_seconds"] = pd.to_numeric(df["duration_seconds"], errors="coerce")
    for col in ("has_video", "has_trials"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.lower().isin(("true", "1", "yes"))
    return df


def list_experiment_files(data_dir: Path, metadata_file: str) -> list[tuple[str, int]]:
    """Return [(filename, size_bytes)] for all files in the experiment folder."""
    meta_path = data_dir / metadata_file
    folder = meta_path.parent
    if not folder.exists():
        return []
    results = []
    for p in sorted(folder.iterdir()):
        if p.is_file():
            results.append((p.name, p.stat().st_size))
    return results


def load_json_metadata(data_dir: Path, metadata_file: str) -> dict:
    try:
        with open(data_dir / metadata_file, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


# ── Resolve data directory and CSV path ──────────────────────────────────────

def get_config() -> tuple[Path, Path]:
    """Return (data_dir, csv_path) from CLI args or defaults."""
    # Streamlit passes its own args; ours come after '--'
    try:
        idx = sys.argv.index("--")
        raw_args = sys.argv[idx + 1:]
    except ValueError:
        raw_args = []

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-dir", type=Path, default=Path("E:/data"))
    parser.add_argument("--csv", type=Path, default=None)
    args, _ = parser.parse_known_args(raw_args)

    data_dir = args.data_dir.resolve()
    csv_path = args.csv.resolve() if args.csv else data_dir / "_experiment_log.csv"
    return data_dir, csv_path


DATA_DIR, CSV_PATH = get_config()

# ── Load data ─────────────────────────────────────────────────────────────────

if not CSV_PATH.exists():
    st.error(
        f"No experiment log found at `{CSV_PATH}`.  "
        f"Run `python update_experiment_log.py` first to generate it."
    )
    st.stop()

df_full = load_csv(str(CSV_PATH))

if df_full.empty:
    st.warning("The experiment log is empty.")
    st.stop()


# ── Sidebar filters ───────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## Filters")
    st.markdown("---")

    # Text search
    search = st.text_input("Search experiment ID", placeholder="e.g. fre042")

    st.markdown("---")

    def multiselect_filter(label: str, col: str) -> list:
        if col not in df_full.columns:
            return []
        opts = sorted(df_full[col].dropna().astype(str).unique())
        return st.multiselect(label, opts)

    sel_genotype    = multiselect_filter("Genotype", "genotype")
    sel_clamp       = multiselect_filter("Clamp mode", "clamp_mode")
    sel_sex         = multiselect_filter("Sex", "sex")
    sel_cell_type   = multiselect_filter("Cell type", "targeted_cell_type")

    st.markdown("---")

    # Date range
    has_dates = "start_time" in df_full.columns and df_full["start_time"].notna().any()
    if has_dates:
        min_date = df_full["start_time"].min().date()
        max_date = df_full["start_time"].max().date()
        date_range = st.date_input("Date range", value=(min_date, max_date),
                                   min_value=min_date, max_value=max_date)
    else:
        date_range = None

    st.markdown("---")

    # Boolean flags
    only_video  = st.checkbox("Has video")
    only_trials = st.checkbox("Has trials")

    st.markdown("---")
    if st.button("↺  Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ── Apply filters ─────────────────────────────────────────────────────────────

df = df_full.copy()

if search:
    df = df[df["expt_id"].astype(str).str.contains(search, case=False, na=False)]
if sel_genotype:
    df = df[df["genotype"].isin(sel_genotype)]
if sel_clamp:
    df = df[df["clamp_mode"].isin(sel_clamp)]
if sel_sex:
    df = df[df["sex"].isin(sel_sex)]
if sel_cell_type:
    df = df[df["targeted_cell_type"].isin(sel_cell_type)]
if date_range and len(date_range) == 2 and has_dates:
    start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1]) + pd.Timedelta(days=1)
    df = df[(df["start_time"] >= start) & (df["start_time"] < end)]
if only_video and "has_video" in df.columns:
    df = df[df["has_video"]]
if only_trials and "has_trials" in df.columns:
    df = df[df["has_trials"]]


# ── Header ────────────────────────────────────────────────────────────────────

st.markdown("# ⚡ Ephys Experiment Dashboard")

n_total   = len(df_full)
n_shown   = len(df)
n_animals = df["expt_id"].nunique() if "expt_id" in df.columns else 0
dur_sum   = df["duration_seconds"].sum() if "duration_seconds" in df.columns else 0
dur_hrs   = dur_sum / 3600

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Showing",         f"{n_shown} / {n_total}")
c2.metric("Animals",         n_animals)
c3.metric("Total recording", f"{dur_hrs:.1f} h")
c4.metric("Has video",       int(df["has_video"].sum())  if "has_video"  in df.columns else "—")
c5.metric("Has trials",      int(df["has_trials"].sum()) if "has_trials" in df.columns else "—")

st.markdown("---")

if df.empty:
    st.info("No experiments match the current filters.")
    st.stop()


# ── Build display table ───────────────────────────────────────────────────────

# Columns to show in the table (auto + any user columns)
AUTO_COLS = [
    "expt_id", "genotype", "age", "sex", "targeted_cell_type",
    "start_time", "duration_seconds", "clamp_mode", "has_video", "has_trials",
]
user_cols = [c for c in df.columns if c not in AUTO_COLS and c != "metadata_file" and c != "end_time"]
display_cols = [c for c in AUTO_COLS if c in df.columns] + user_cols

df_display = df[display_cols].copy()

# Format duration
if "duration_seconds" in df_display.columns:
    df_display["duration_seconds"] = df_display["duration_seconds"].apply(
        lambda x: f"{x:.1f} s" if pd.notna(x) else ""
    )
    df_display = df_display.rename(columns={"duration_seconds": "duration"})

# Format start_time to readable string
if "start_time" in df_display.columns:
    df_display["start_time"] = df_display["start_time"].dt.strftime("%Y-%m-%d  %H:%M")

col_cfg: dict = {}
if "has_video"  in df_display.columns: col_cfg["has_video"]  = st.column_config.CheckboxColumn("video")
if "has_trials" in df_display.columns: col_cfg["has_trials"] = st.column_config.CheckboxColumn("trials")
if "start_time" in df_display.columns: col_cfg["start_time"] = st.column_config.TextColumn("date / time")
if "expt_id"    in df_display.columns: col_cfg["expt_id"]    = st.column_config.TextColumn("experiment")

event = st.dataframe(
    df_display.reset_index(drop=True),
    column_config=col_cfg,
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    height=min(40 + 35 * len(df_display), 520),
)


# ── Detail panel ─────────────────────────────────────────────────────────────

selected_rows = event.selection.get("rows", []) if hasattr(event, "selection") else []

if not selected_rows:
    st.markdown(
        '<p style="color:#8b949e;font-size:0.85rem;margin-top:8px;">'
        'Click a row to inspect experiment details.</p>',
        unsafe_allow_html=True,
    )
    st.stop()

row_idx = selected_rows[0]
row = df.iloc[row_idx]
meta_file = row.get("metadata_file", "")

st.markdown("---")
st.markdown(f"### {row.get('expt_id', 'Experiment')} &nbsp;—&nbsp; details",
            unsafe_allow_html=True)

left, right = st.columns([1, 1])

# ── Left: metadata ────────────────────────────────────────────────────────────
with left:
    meta = load_json_metadata(DATA_DIR, meta_file) if meta_file else {}
    subject = meta.get("subject", {})
    files_meta = meta.get("files", {})
    camera = meta.get("camera", {})

    # Subject info
    with st.expander("Subject", expanded=True):
        fields = [
            ("Experiment ID",    subject.get("expt_id",           row.get("expt_id", ""))),
            ("Genotype",         subject.get("genotype",          row.get("genotype", ""))),
            ("Age",              subject.get("age",               row.get("age", ""))),
            ("Sex",              subject.get("sex",               row.get("sex", ""))),
            ("Targeted cell",    subject.get("targeted_cell_type",row.get("targeted_cell_type", ""))),
        ]
        rows_html = "".join(
            f'<div class="info-key">{k}</div><div class="info-val">{v or "—"}</div>'
            for k, v in fields
        )
        st.markdown(f'<div class="info-grid">{rows_html}</div>', unsafe_allow_html=True)

    # Recording info
    with st.expander("Recording", expanded=True):
        dur = row.get("duration_seconds", "")
        dur_str = f"{float(dur):.1f} s" if dur != "" and pd.notna(dur) else "—"
        chans = meta.get("channels", [])
        chan_str = ", ".join(c.get("name","") for c in chans) if chans else "—"

        fields = [
            ("Start time",    str(row.get("start_time", ""))[:19] or "—"),
            ("End time",      str(row.get("end_time", ""))[:19]   or "—"),
            ("Duration",      dur_str),
            ("Clamp mode",    row.get("clamp_mode", "") or "—"),
            ("Sample rate",   f"{row.get('sample_rate_hz', '')} Hz" if row.get("sample_rate_hz") else "—"),
            ("Channels",      chan_str),
        ]
        if camera:
            fields += [
                ("Camera fps",  f"{camera.get('frame_rate_hz','—')} Hz"),
                ("Exposure",    f"{camera.get('exposure_ms','—')} ms"),
            ]
        rows_html = "".join(
            f'<div class="info-key">{k}</div><div class="info-val">{v}</div>'
            for k, v in fields
        )
        st.markdown(f'<div class="info-grid">{rows_html}</div>', unsafe_allow_html=True)

    # User columns (notes, custom)
    user_vals = {c: row.get(c, "") for c in user_cols if str(row.get(c, "")).strip()}
    if user_vals:
        with st.expander("Notes / custom fields", expanded=True):
            rows_html = "".join(
                f'<div class="info-key">{k}</div><div class="info-val">{v}</div>'
                for k, v in user_vals.items()
            )
            st.markdown(f'<div class="info-grid">{rows_html}</div>', unsafe_allow_html=True)


# ── Right: files on disk ──────────────────────────────────────────────────────
with right:
    with st.expander("Files on disk", expanded=True):
        file_list = list_experiment_files(DATA_DIR, meta_file) if meta_file else []
        if not file_list:
            st.markdown('<p style="color:#8b949e">No files found.</p>', unsafe_allow_html=True)
        else:
            total_size = sum(s for _, s in file_list)
            ext_groups: dict[str, list] = {}
            for fname, fsize in file_list:
                ext = Path(fname).suffix.lower() or "other"
                ext_groups.setdefault(ext, []).append((fname, fsize))

            # Summary tags
            tags_html = ""
            for ext, items in sorted(ext_groups.items()):
                color = {"h5": "green", "hdf5": "green",
                         "bin": "yellow", "json": "",
                         "avi": "red", "mp4": "red"}.get(ext.lstrip("."), "gray")
                tags_html += _tag(f"{ext}  ×{len(items)}", color)
            tags_html += _tag(_fmt_size(total_size), "gray")
            st.markdown(tags_html, unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            for fname, fsize in file_list:
                ext = Path(fname).suffix
                icon = _file_icon(ext)
                st.markdown(
                    f'<div class="file-row">'
                    f'  <span>{icon}</span>'
                    f'  <span>{fname}</span>'
                    f'  <span class="file-size">{_fmt_size(fsize)}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # Channel detail from JSON
    chans = meta.get("channels", []) if meta else []
    if chans:
        with st.expander("Channels", expanded=False):
            chan_df = pd.DataFrame([
                {
                    "name":     c.get("name", ""),
                    "NI ch":    c.get("ni_channel", ""),
                    "units":    c.get("units", ""),
                    "scale":    c.get("display_scale", ""),
                    "terminal": c.get("terminal_config", ""),
                }
                for c in chans
            ])
            st.dataframe(chan_df, hide_index=True, use_container_width=True)

    # Protocol detail (trials)
    protocol_str = None
    if meta and meta_file:
        trials_h5 = DATA_DIR / meta_file.replace("_metadata.json", "_trials.h5")
        if trials_h5.exists():
            # Try to read protocol JSON from HDF5
            try:
                import h5py
                with h5py.File(trials_h5, "r") as f:
                    if "metadata/protocol" in f:
                        protocol_str = f["metadata/protocol"][()].decode()
            except Exception:
                pass

    if protocol_str:
        with st.expander("Trial protocol", expanded=False):
            try:
                proto = json.loads(protocol_str)
                st.json(proto)
            except Exception:
                st.code(protocol_str)

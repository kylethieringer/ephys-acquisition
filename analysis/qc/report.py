"""
QC report orchestrator.

Loads a recording, runs every check module (integrity, signal, stimulus),
renders a self-contained HTML report (with base64-embedded PNG plots)
and writes a machine-readable JSON sidecar next to the recording.
"""

from __future__ import annotations

import base64
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless — report runs on save thread, no display

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402
from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: E402

from analysis.qc import Check, Status, worst  # noqa: E402
from analysis.qc import integrity, signal, stimulus  # noqa: E402
from analysis.qc.descriptions import (  # noqa: E402
    REPORT_INTRO, STATUS_KEY, describe_check, describe_section,
)
from analysis.qc.load import load_recording  # noqa: E402


TEMPLATE_DIR = Path(__file__).parent / "templates"
STYLE_PATH = Path(__file__).parent.parent / "kt.mplstyle"


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------

def run_qc(h5_path: str | Path, write: bool = True) -> dict[str, Any]:
    """Run the full QC pipeline and (optionally) write report files.

    Args:
        h5_path: Path to the recording HDF5.
        write: If True, writes ``qc_report.html`` and ``qc_report.json``
            next to the recording.  If False, returns the result dict
            without writing.

    Returns:
        A dict with keys ``status``, ``sections``, ``html_path``,
        ``json_path``.
    """
    h5_path = Path(h5_path)
    bundle = load_recording(h5_path)

    sections = {
        "Acquisition integrity": integrity.run_all(bundle),
        "Signal sanity":         signal.run_all(bundle),
        "Commanded vs recorded": stimulus.run_all(bundle),
    }

    plots = _build_plots(bundle)
    overall = worst([c for checks in sections.values() for c in checks])

    result: dict[str, Any] = {
        "h5_path":    str(h5_path),
        "generated":  datetime.now().isoformat(timespec="seconds"),
        "mode":       bundle["recording_mode"],
        "status":     overall.value,
        "sections":   {name: [c.to_dict() for c in cks] for name, cks in sections.items()},
        "html_path":  None,
        "json_path":  None,
    }

    if write:
        html_path = h5_path.with_name(h5_path.stem + "_qc_report.html")
        json_path = h5_path.with_name(h5_path.stem + "_qc_report.json")
        html_path.write_text(_render_html(bundle, sections, overall, plots),
                             encoding="utf-8")
        json_path.write_text(json.dumps(result, indent=2, default=str))
        result["html_path"] = str(html_path)
        result["json_path"] = str(json_path)

    return result


# ----------------------------------------------------------------------------
# HTML rendering
# ----------------------------------------------------------------------------

def _render_html(
    bundle: dict[str, Any],
    sections: dict[str, list[Check]],
    overall: Status,
    plots: dict[str, str],
) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    tmpl = env.get_template("report.html.j2")

    return tmpl.render(
        h5_path=str(bundle["h5_path"]),
        mode=bundle["recording_mode"],
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        overall_status=overall.value,
        report_intro=REPORT_INTRO,
        status_key=STATUS_KEY,
        sections={
            name: {
                "description": describe_section(name),
                "checks": [
                    {
                        "name":        c.name,
                        "status":      c.status.value,
                        "message":     c.message,
                        "metrics":     _format_metrics(c.metrics),
                        "description": describe_check(c.name),
                    }
                    for c in checks
                ],
            }
            for name, checks in sections.items()
        },
        plots=plots,
        sample_rate=bundle["sample_rate"],
        channel_names=bundle["channel_names"],
        start_time=bundle.get("start_time", ""),
    )


def _format_metrics(m: dict[str, Any]) -> str:
    """Pretty-print a metrics dict as an indented JSON string."""
    try:
        return json.dumps(m, indent=2, default=_json_default)
    except Exception:
        return str(m)


def _json_default(x: Any) -> Any:
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (np.integer, np.floating)):
        return x.item()
    if isinstance(x, Path):
        return str(x)
    raise TypeError(f"not JSON-serialisable: {type(x).__name__}")


# ----------------------------------------------------------------------------
# Plots — each returns a base64-encoded PNG for inline HTML
# ----------------------------------------------------------------------------

def _build_plots(bundle: dict[str, Any]) -> dict[str, Any]:
    """Return a dict of plot artifacts for the template.

    Keys:
        overview_html   — interactive plotly HTML snippet (zoomable)
        stimulus_overlay — base64 PNG (trial mode only)
    """
    plots: dict[str, Any] = {}
    try:
        plt.style.use(str(STYLE_PATH))
    except Exception:
        pass

    try:
        plots["overview_html"] = _plot_overview_interactive(bundle)
    except Exception:
        pass
    try:
        if bundle["recording_mode"] == "trial":
            plots["stimulus_overlay"] = _plot_trial_cmd_overlay(bundle)
    except Exception:
        pass
    return plots


MAX_POINTS_PER_CHANNEL = 600_000
"""Upper bound on points per channel in the interactive overview plot.

Short recordings (under ~5 s at 20 kHz) render at full resolution.  Longer
recordings are decimated with :func:`_minmax_decimate`, which preserves
peaks/valleys in each bin so spikes and transients remain visible at any
zoom level.
"""


def _plot_overview_interactive(bundle: dict[str, Any]) -> str:
    """Multi-channel overview rendered as interactive plotly HTML.

    Uses the WebGL renderer (``go.Scattergl``).  Full resolution below
    :data:`MAX_POINTS_PER_CHANNEL` samples per channel; above that, min/max
    binning preserves the signal envelope so spikes stay visible.
    """
    sr = int(bundle["sample_rate"])
    names = list(bundle["channel_names"])
    scales = list(bundle["display_scales"])
    units = list(bundle["units"])

    if bundle["recording_mode"] == "continuous":
        data = bundle["data"]
    else:
        data = np.concatenate([t["data"] for t in bundle["trials"]], axis=1)

    n_samp = data.shape[1]
    n_ch = len(names)
    fig = make_subplots(
        rows=n_ch, cols=1, shared_xaxes=True, vertical_spacing=0.02,
    )
    for i, ch in enumerate(names):
        full = data[i] * float(scales[i])
        idx, y = _minmax_decimate(full, MAX_POINTS_PER_CHANNEL)
        t = (idx.astype(np.float32) / sr)
        fig.add_trace(
            go.Scattergl(
                x=t, y=y.astype(np.float32), mode="lines", name=ch,
                line=dict(width=1),
                hovertemplate=(f"<b>{ch}</b><br>"
                               f"t=%{{x:.3f}} s<br>"
                               f"%{{y:.3f}} {units[i]}<extra></extra>"),
            ),
            row=i + 1, col=1,
        )
        fig.update_yaxes(title_text=f"{ch} ({units[i]})", row=i + 1, col=1)
    fig.update_xaxes(title_text="Time (s)", row=n_ch, col=1)
    fig.update_layout(
        height=max(240, 140 * n_ch),
        margin=dict(l=70, r=20, t=20, b=40),
        showlegend=False,
        hovermode="x unified",
        dragmode="zoom",
        template="simple_white",
    )
    return fig.to_html(
        full_html=False,
        include_plotlyjs="cdn",
        div_id="qc-overview",
        config={"displaylogo": False, "responsive": True},
    )


def _plot_trial_cmd_overlay(bundle: dict[str, Any]) -> str:
    """Overlay expected vs. recorded AmpCmd for each unique stimulus."""
    from acquisition.trial_protocol import protocol_from_dict
    from acquisition.trial_waveforms import build_trial_waveform

    try:
        amp_idx = bundle["channel_names"].index("AmpCmd")
    except ValueError:
        raise RuntimeError("no AmpCmd channel")
    protocol_json = bundle.get("protocol_json") or ""
    if not protocol_json:
        raise RuntimeError("no protocol JSON")
    protocol = protocol_from_dict(json.loads(protocol_json))
    stim_by_name = {s.name: s for s in protocol.stimuli}

    seen: dict[str, dict[str, Any]] = {}
    for t in bundle["trials"]:
        stim_name = t["stimulus_name"]
        if stim_name in seen or stim_name not in stim_by_name:
            continue
        expected = build_trial_waveform(stim_by_name[stim_name], protocol)
        recorded = np.asarray(t["data"][amp_idx], dtype=float)
        n = min(expected.size, recorded.size)
        seen[stim_name] = {"exp": expected[:n], "rec": recorded[:n]}

    if not seen:
        raise RuntimeError("no stimuli to plot")

    sr = int(bundle["sample_rate"])
    fig, axes = plt.subplots(len(seen), 1, figsize=(9, 1.8 * len(seen)),
                             sharex=False)
    if len(seen) == 1:
        axes = [axes]
    for ax, (name, traces) in zip(axes, seen.items()):
        t = np.arange(traces["exp"].size) / sr
        ax.plot(t, traces["exp"], label="commanded", linewidth=1.0)
        ax.plot(t, traces["rec"], label="recorded",  linewidth=0.7, alpha=0.8)
        ax.set_ylabel("ao0 (V)")
        ax.set_title(name)
        ax.legend(loc="upper right", fontsize=9)
    axes[-1].set_xlabel("Time (s)")
    fig.tight_layout()
    return _fig_to_b64(fig)


def _minmax_decimate(
    x: np.ndarray, max_points: int
) -> tuple[np.ndarray, np.ndarray]:
    """Min/max bin-decimate a 1-D signal while preserving peaks and valleys.

    If ``x`` already fits within ``max_points`` it is returned unchanged.
    Otherwise we partition the signal into ``max_points // 2`` bins, and
    emit two points per bin — the min and the max — at the exact sample
    indices where they occurred.  The resulting line plot follows the full
    envelope of the original signal, so spikes are never missed regardless
    of zoom level.

    Args:
        x: 1-D numeric array.
        max_points: Target maximum output length.  Must be >= 4.

    Returns:
        ``(indices, values)`` — integer sample indices (for time axis)
        and the decimated values.  Length ≤ ``max_points``.
    """
    n = int(x.size)
    if n <= max_points:
        return np.arange(n, dtype=np.int64), np.asarray(x)

    n_bins = max(2, max_points // 2)
    bin_size = n // n_bins
    usable = n_bins * bin_size
    reshaped = x[:usable].reshape(n_bins, bin_size)

    min_pos = np.argmin(reshaped, axis=1)
    max_pos = np.argmax(reshaped, axis=1)
    bin_starts = np.arange(n_bins, dtype=np.int64) * bin_size
    min_idx = bin_starts + min_pos
    max_idx = bin_starts + max_pos
    min_val = reshaped[np.arange(n_bins), min_pos]
    max_val = reshaped[np.arange(n_bins), max_pos]

    # Interleave in the order each extremum occurred within its bin so the
    # connecting polyline never "jumps backwards".
    first_min = min_pos <= max_pos
    out_idx = np.empty(n_bins * 2, dtype=np.int64)
    out_val = np.empty(n_bins * 2, dtype=x.dtype if x.dtype.kind == "f" else np.float64)
    out_idx[0::2] = np.where(first_min, min_idx, max_idx)
    out_idx[1::2] = np.where(first_min, max_idx, min_idx)
    out_val[0::2] = np.where(first_min, min_val, max_val)
    out_val[1::2] = np.where(first_min, max_val, min_val)
    return out_idx, out_val


def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")

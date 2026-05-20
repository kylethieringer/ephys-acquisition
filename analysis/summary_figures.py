"""Per-cell summary figures: F-I curves, input resistance, and RMP.

Walks the experiment log via :func:`batch_intrinsics.collect_intrinsics`,
filters out drug-applied recordings, joins on a user-maintained side-car
``D:\\data\\_fi_protocols.csv`` that names which step_protocols in each
recording are the F-I repeats, and aggregates per cell.

A "cell" is the group ``(expt_id, targeted_cell_type, drug_applied)``
unless the side-car overrides with an explicit ``cell_id``.  Within a
cell, recordings are averaged: per h5 first (so one long recording can't
dominate), then across h5 files (so session-to-session drift stays
visible in plotted thin lines).

CLI::

    python -m analysis.summary_figures dvmn
    python -m analysis.summary_figures dvmn DNa01
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from analysis.batch_intrinsics import collect_intrinsics

DEFAULT_FI_PROTOCOLS_PATH = Path(r"D:\data\_fi_protocols.csv")
DEFAULT_FIG_DIR = Path(r"D:\results\figs\summary")
DEFAULT_SUMMARY_DIR = Path(r"D:\results\summary")

# RMP threshold above which the cell is presumed to be unhealthy/depolarised.
RMP_OUTLIER_MV = -20.0

# Tolerance (pA) when matching side-car (fi_min, fi_max) against
# _intrinsics.csv summary columns (which are rounded to 0.1 pA).
AMP_MATCH_TOL_PA = 1.0

# ── Apply custom matplotlib style ────────────────────────────────────────
_STYLE_PATH = Path(__file__).resolve().parent / "kt.mplstyle"
if _STYLE_PATH.exists():
    plt.style.use(_STYLE_PATH)


# =========================================================================
# Helpers
# =========================================================================

_DATE_RE = re.compile(r"(\d{8})_\d{6}")
_TRUTHY = {"1", "true", "yes", "y", "t"}


def _is_drug_truthy(value: object) -> bool:
    s = str(value).strip().lower()
    return s in _TRUTHY


def _extract_date(metadata_file: str) -> str | None:
    """Return YYYYMMDD from the standard filename, or None."""
    m = _DATE_RE.search(metadata_file)
    return m.group(1) if m else None


def _derive_cell_id(row: pd.Series) -> str:
    """Use side-car `cell_id` when provided, else fall back to the
    (expt_id, cell_type, drug_applied) tuple."""
    explicit = str(row.get("cell_id", "") or "").strip()
    if explicit:
        return explicit
    drug = str(row.get("drug_applied", "")).strip().lower() or "no"
    return f"{row['expt_id']}_{row['targeted_cell_type']}_drug-{drug}"


def _build_color_map(cell_ids: list[str]) -> dict[str, tuple]:
    """Assign a stable color to each cell_id using a sequential ``Blues``
    colormap.  The mapping is deterministic across the three figures so
    a cell keeps the same color in the F-I, RMP, and Ri plots.  Samples
    are taken in 0.35..0.95 to avoid near-white shades that vanish on a
    white background.
    """
    sorted_ids = sorted(set(cell_ids))
    n = len(sorted_ids)
    cmap = plt.get_cmap("Blues")
    if n == 1:
        return {sorted_ids[0]: cmap(0.7)}
    fractions = np.linspace(0.35, 0.95, n)
    return {cid: cmap(f) for cid, f in zip(sorted_ids, fractions)}


def _clean_spines(ax: plt.Axes) -> None:
    """Remove top and right spines for a clean publication look."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _parse_side_car(
    fi_protocols_path: Path,
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Read the side-car CSV.

    Returns
    -------
    specs_by_basename
        ``{basename_of_metadata_file: [{"fi_min_pA", "fi_max_pA", "fi_n_steps"}, ...]}``.
        One side-car row contributes one spec; multiple rows for the
        same recording stack as additional F-I blocks for that cell.
    explicit_cell_ids
        ``{basename: cell_id}`` for rows where the user supplied one.
    """
    specs_by_basename: dict[str, list[dict]] = {}
    explicit_cell_ids: dict[str, str] = {}

    if not fi_protocols_path.exists():
        print(f"Side-car {fi_protocols_path} not found; F-I plot will be empty.")
        return specs_by_basename, explicit_cell_ids

    side = pd.read_csv(fi_protocols_path, dtype=str).fillna("")
    for _, srow in side.iterrows():
        mf = str(srow.get("metadata_file", "")).strip()
        if not mf:
            continue
        bn = Path(mf).name

        fi_min = str(srow.get("fi_min_pA", "")).strip()
        fi_max = str(srow.get("fi_max_pA", "")).strip()
        fi_n = str(srow.get("fi_n_steps", "")).strip()
        if fi_min and fi_max and fi_n:
            specs_by_basename.setdefault(bn, []).append({
                "fi_min_pA": fi_min,
                "fi_max_pA": fi_max,
                "fi_n_steps": fi_n,
            })

        cid = str(srow.get("cell_id", "")).strip()
        if cid:
            existing = explicit_cell_ids.get(bn)
            if existing and existing != cid:
                print(
                    f"[warn] conflicting cell_id for {bn}: "
                    f"{existing!r} vs {cid!r}; using first."
                )
            else:
                explicit_cell_ids[bn] = cid

    return specs_by_basename, explicit_cell_ids


# =========================================================================
# Per-recording aggregation
# =========================================================================

def _summarize_one_h5(row: pd.Series, specs: list[dict]) -> dict:
    """Compute RMP, Ri, and (if any side-car specs apply) an F-I curve
    for one h5 file.

    ``specs`` is a list of dicts, each with ``fi_min_pA``, ``fi_max_pA``,
    ``fi_n_steps`` (strings, as read from the CSV).  All matching
    step_protocols across all specs are unioned before computing the
    F-I curve, so a cell with two separate amplitude ranges (e.g.
    -50..50 pA and 50..300 pA) produces one combined curve.
    """
    intrinsics = pd.read_csv(row["intrinsics_csv_path"])

    # RMP / Ri with outlier drop
    good = intrinsics.dropna(subset=["input_resistance_MOhm"])
    good = good[good["rmp_mV"] <= RMP_OUTLIER_MV]
    rmp_mV = float(good["rmp_mV"].mean()) if not good.empty else float("nan")
    ri_MOhm = (
        float(good["input_resistance_MOhm"].mean()) if not good.empty else float("nan")
    )

    fi_curve: pd.DataFrame | None = None
    matched_indices: set[int] = set()
    for spec in specs:
        fi_min = float(spec["fi_min_pA"])
        fi_max = float(spec["fi_max_pA"])
        fi_n = int(float(spec["fi_n_steps"]))
        matches = intrinsics[
            (np.abs(intrinsics["min_amp_pA"] - fi_min) <= AMP_MATCH_TOL_PA)
            & (np.abs(intrinsics["max_amp_pA"] - fi_max) <= AMP_MATCH_TOL_PA)
            & (intrinsics["n_steps"] == fi_n)
        ]
        matched_indices.update(matches["step_protocol_index"].tolist())

    if matched_indices:
        rates = pd.read_csv(row["step_rates_csv_path"])
        sub = rates[rates["step_protocol_index"].isin(matched_indices)].copy()
        if not sub.empty:
            sub["amp_bin"] = sub["amplitude_pA"].round(0).astype(int)
            fi_curve = (
                sub.groupby("amp_bin")["firing_rate_hz"]
                .mean()
                .rename("firing_rate_hz")
                .reset_index()
                .rename(columns={"amp_bin": "amplitude_pA"})
                .sort_values("amplitude_pA")
                .reset_index(drop=True)
            )

    return {
        "metadata_file": row["metadata_file"],
        "rmp_mV": rmp_mV,
        "ri_MOhm": ri_MOhm,
        "fi_curve": fi_curve,
        "n_fi_repeats": len(matched_indices),
    }


def _aggregate_one_cell(cell_id: str, group: pd.DataFrame) -> dict:
    """Combine per-h5 summaries into a single cell-level summary."""
    per_h5 = [
        _summarize_one_h5(r, r.get("_fi_specs", []) or [])
        for _, r in group.iterrows()
    ]

    rmps = [h["rmp_mV"] for h in per_h5 if not np.isnan(h["rmp_mV"])]
    ris = [h["ri_MOhm"] for h in per_h5 if not np.isnan(h["ri_MOhm"])]
    rmp_cell = float(np.mean(rmps)) if rmps else float("nan")
    ri_cell = float(np.mean(ris)) if ris else float("nan")

    fi_curves = [h["fi_curve"] for h in per_h5 if h["fi_curve"] is not None]
    if fi_curves:
        combined = pd.concat(fi_curves, axis=0, ignore_index=True)
        # Mean across h5s — equal weight per h5 (already a within-h5 mean).
        cell_fi = (
            combined.groupby("amplitude_pA")["firing_rate_hz"]
            .mean()
            .reset_index()
            .sort_values("amplitude_pA")
            .reset_index(drop=True)
        )
    else:
        cell_fi = None

    return {
        "cell_id": cell_id,
        "expt_id": group["expt_id"].iloc[0],
        "targeted_cell_type": group["targeted_cell_type"].iloc[0],
        "drug_applied": group["drug_applied"].iloc[0],
        "n_h5_files": len(group),
        "n_fi_repeats_total": sum(h["n_fi_repeats"] for h in per_h5),
        "rmp_mV": rmp_cell,
        "ri_MOhm": ri_cell,
        "fi_curve": cell_fi,
        "per_h5": per_h5,
    }


# =========================================================================
# Plotting
# =========================================================================

def _plot_fi(
    cells: list[dict],
    cell_type: str,
    out_path: Path,
    color_map: dict[str, tuple],
) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(5.5, 5.0))

    for cell in sorted(cells, key=lambda c: c["cell_id"]):
        curve = cell["fi_curve"]
        if curve is None or curve.empty:
            continue
        ax.plot(
            curve["amplitude_pA"],
            curve["firing_rate_hz"],
            color=color_map[cell["cell_id"]],
            lw=2.0,
            marker="o",
            markersize=8,
            zorder=2,
        )

    ax.set_xlabel("input current (pA)")
    ax.set_ylabel("firing rate (hz)")
    _clean_spines(ax)

    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    print(f"Saved {out_path}")
    return fig


def _plot_strip(
    cells: list[dict],
    field: str,
    ylabel: str,
    title: str,
    cell_type: str,
    out_path: Path,
    color_map: dict[str, tuple],
) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(3.0, 5.0))

    sorted_cells = sorted(cells, key=lambda c: c["cell_id"])
    keep = [(c["cell_id"], c[field]) for c in sorted_cells if not np.isnan(c[field])]
    if not keep:
        fig.tight_layout()
        fig.savefig(out_path, dpi=200)
        print(f"Saved {out_path}")
        return fig

    rng = np.random.default_rng(seed=0)
    xs = rng.uniform(-0.15, 0.15, size=len(keep))
    for x, (cid, v) in zip(xs, keep):
        ax.scatter(
            [x], [v],
            s=80, color=color_map[cid],
            edgecolor="white", linewidth=1.0,
            zorder=2,
        )

    vals = [v for _, v in keep]
    mean = float(np.mean(vals))
    sem = float(np.std(vals, ddof=1) / np.sqrt(len(vals))) if len(vals) > 1 else 0.0
    ax.errorbar(
        [0], [mean], yerr=[sem],
        fmt="_", color="black", markersize=30,
        capsize=8, lw=1.5, zorder=3,
    )

    ax.set_xticks([0])
    ax.set_xticklabels([cell_type])
    ax.set_xlim(-0.5, 0.5)
    ax.set_ylabel(ylabel)
    _clean_spines(ax)

    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    print(f"Saved {out_path}")
    return fig


# =========================================================================
# Main entry
# =========================================================================

def summarize_cell_type(
    cell_type: str,
    fi_protocols_path: Path = DEFAULT_FI_PROTOCOLS_PATH,
    fig_dir: Path = DEFAULT_FIG_DIR,
    summary_dir: Path = DEFAULT_SUMMARY_DIR,
    show: bool = True,
) -> pd.DataFrame:
    """Build per-cell F-I, RMP, Ri summaries and figures for one cell type.

    Returns a DataFrame with one row per cell (also written to disk as
    ``{cell_type}_summary.csv``).
    """
    fig_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)

    batch = collect_intrinsics(cell_type)
    if batch.empty:
        print(f"No matched recordings for cell type {cell_type!r}.")
        return pd.DataFrame()

    batch = batch[batch["status"] != "error"].copy()
    n_before = len(batch)
    batch = batch[~batch["drug_applied"].map(_is_drug_truthy)]
    n_dropped = n_before - len(batch)
    if n_dropped:
        print(f"Excluded {n_dropped} drug-applied recording(s).")
    if batch.empty:
        print(f"No drug-free recordings remain for cell type {cell_type!r}.")
        return pd.DataFrame()

    # Side-car: parse once into a dict of basename -> list of specs.
    # Matching uses the basename of `metadata_file` so the side-car may
    # use either the bare filename or the subdir-prefixed form from the
    # experiment log.
    specs_by_basename, explicit_cell_ids = _parse_side_car(fi_protocols_path)

    batch["_basename"] = batch["metadata_file"].map(lambda s: Path(s).name)
    batch["_fi_specs"] = batch["_basename"].map(
        lambda bn: specs_by_basename.get(bn, [])
    )
    batch["cell_id"] = batch.apply(
        lambda row: explicit_cell_ids.get(row["_basename"]) or _derive_cell_id(row),
        axis=1,
    )

    # Warn when an auto-derived cell_id covers >1 recording date.
    for cell_id, group in batch.groupby("cell_id"):
        if any(bn in explicit_cell_ids for bn in group["_basename"]):
            continue
        dates = {d for d in (_extract_date(f) for f in group["metadata_file"]) if d}
        if len(dates) > 1:
            print(
                f"[warn] cell_id={cell_id!r} spans {len(dates)} recording dates "
                f"({sorted(dates)}) across {len(group)} h5 file(s). "
                f"Likely >1 distinct cell -- add an explicit `cell_id` in "
                f"{fi_protocols_path.name} to disambiguate."
            )

    cells: list[dict] = []
    for cell_id, group in batch.groupby("cell_id"):
        cells.append(_aggregate_one_cell(cell_id, group))

    if not cells:
        print(f"No cells aggregated for {cell_type!r}.")
        return pd.DataFrame()

    color_map = _build_color_map([c["cell_id"] for c in cells])

    # Plots
    fi_path = fig_dir / f"{cell_type}_fi.png"
    rmp_path = fig_dir / f"{cell_type}_rmp.png"
    ri_path = fig_dir / f"{cell_type}_ri.png"
    fig_fi = _plot_fi(cells, cell_type, fi_path, color_map)
    fig_rmp = _plot_strip(
        cells, "rmp_mV", "resting Vm (mV)", "",
        cell_type, rmp_path, color_map,
    )
    fig_ri = _plot_strip(
        cells, "ri_MOhm", "input resistance (MΩ)", "",
        cell_type, ri_path, color_map,
    )

    # Manifest
    manifest_rows = []
    for c in cells:
        if c["fi_curve"] is not None and not c["fi_curve"].empty:
            amps = ";".join(f"{a:g}" for a in c["fi_curve"]["amplitude_pA"])
            rates = ";".join(f"{r:.2f}" for r in c["fi_curve"]["firing_rate_hz"])
        else:
            amps = ""
            rates = ""
        manifest_rows.append({
            "cell_id": c["cell_id"],
            "expt_id": c["expt_id"],
            "targeted_cell_type": c["targeted_cell_type"],
            "drug_applied": c["drug_applied"],
            "n_h5_files": c["n_h5_files"],
            "n_fi_repeats_used": c["n_fi_repeats_total"],
            "rmp_mV_mean": round(c["rmp_mV"], 2) if not np.isnan(c["rmp_mV"]) else "",
            "ri_MOhm_mean": round(c["ri_MOhm"], 1) if not np.isnan(c["ri_MOhm"]) else "",
            "fi_amps_pA": amps,
            "fi_rates_hz": rates,
        })
    manifest = pd.DataFrame(manifest_rows)
    manifest_path = summary_dir / f"{cell_type}_summary.csv"
    manifest.to_csv(manifest_path, index=False)
    print(f"Saved {manifest_path}")

    if show:
        plt.show()
    else:
        for f in (fig_fi, fig_rmp, fig_ri):
            plt.close(f)

    return manifest


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build per-cell summary figures (F-I, RMP, Ri) for "
                    "one or more cell types.",
    )
    p.add_argument("cell_types", nargs="+",
                   help="One or more values of `targeted_cell_type`.")
    p.add_argument("--fi-protocols", type=Path, default=DEFAULT_FI_PROTOCOLS_PATH,
                   help=f"Side-car CSV (default: {DEFAULT_FI_PROTOCOLS_PATH}).")
    p.add_argument("--fig-dir", type=Path, default=DEFAULT_FIG_DIR,
                   help=f"Figure output dir (default: {DEFAULT_FIG_DIR}).")
    p.add_argument("--summary-dir", type=Path, default=DEFAULT_SUMMARY_DIR,
                   help=f"Manifest CSV output dir (default: {DEFAULT_SUMMARY_DIR}).")
    p.add_argument("--no-show", action="store_true",
                   help="Don't open interactive figure windows.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    for ct in args.cell_types:
        print(f"\n=== {ct} ===")
        summarize_cell_type(
            ct,
            fi_protocols_path=args.fi_protocols,
            fig_dir=args.fig_dir,
            summary_dir=args.summary_dir,
            show=not args.no_show,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Per-cell summary figures: F-I curves, input resistance, and RMP.

Walks the experiment log via :func:`batch_intrinsics.collect_intrinsics`,
joins on a user-maintained side-car ``D:\\data\\_fi_protocols.csv`` that
names which step_protocols in each recording are the F-I repeats, and
aggregates per cell.

Drug-applied recordings are excluded by default; pass ``--include-drug``
to plot them alongside the drug-free ones.  Output filenames then gain a
``_withdrug`` suffix so both sets of figures can coexist.

A "cell" is the group ``(expt_id, targeted_cell_type, condition)`` --
where *condition* is the baseline or a specific ``drug_name`` -- unless
the side-car overrides with an explicit ``cell_id``.  Splitting on the
drug name matters: several experiments applied both caffeine and
octopamine to one cell, and a bare drug/no-drug key would average them.
``drug_concentration`` is not part of the key because the log records it
in inconsistent units; it is reported in the manifest instead.

The physical cell behind those conditions is the ``pair_key``.  It
drives the color map, so a cell keeps one color across its baseline and
drug points, and the strip plots join that cell's conditions with a
line.  Drug points are hollow and drug F-I curves dashed.

Within a cell, recordings are averaged: per h5 first (so one long
recording can't dominate), then across h5 files (so session-to-session
drift stays visible in plotted thin lines).

CLI::

    python -m analysis.summary_figures dvmn
    python -m analysis.summary_figures dvmn DNa01
    python -m analysis.summary_figures dvmn --include-drug
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

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


def _is_drug_cell(cell: dict) -> bool:
    """True when an aggregated cell came from drug-applied recordings."""
    return bool(cell.get("condition", ""))


def _condition(row: pd.Series) -> str:
    """The pharmacological condition of a recording.

    ``""`` for baseline, else the normalised ``drug_name`` (e.g.
    ``"caffeine"``).  Splitting on the drug *name* matters because some
    experiments applied two different drugs to one cell -- keying only on
    ``drug_applied`` would average those two conditions together.

    ``drug_concentration`` is deliberately not part of the key: the log
    records it in inconsistent units (``.5mM``, ``0.5 mM``, ``100uM``,
    ``1X``), so it cannot be grouped on reliably.  It is carried into the
    manifest instead.
    """
    if not _is_drug_truthy(row.get("drug_applied", "")):
        return ""
    name = str(row.get("drug_name", "") or "").strip().lower()
    return name or "drug"


def _extract_date(metadata_file: str) -> str | None:
    """Return YYYYMMDD from the standard filename, or None."""
    m = _DATE_RE.search(metadata_file)
    return m.group(1) if m else None


def _derive_pair_key(row: pd.Series) -> str:
    """Identity of the physical cell, ignoring which drug was applied.

    Baseline and drug recordings of one cell share a pair_key, which is
    what lets the strip plots join them with a line and give them a
    single color.
    """
    explicit = str(row.get("cell_id", "") or "").strip()
    if explicit:
        return explicit
    return f"{row['expt_id']}_{row['targeted_cell_type']}"


def _derive_cell_id(row: pd.Series) -> str:
    """One cell_id per (physical cell, condition) pair.

    Baseline keeps the historical ``drug-no`` spelling so drug-free runs
    produce byte-identical output; drug recordings are tagged with the
    drug name rather than a bare ``drug-yes``.
    """
    condition = _condition(row)
    return f"{_derive_pair_key(row)}_drug-{condition or 'no'}"


def _ordered_conditions(cells: list[dict]) -> list[str]:
    """Conditions present, baseline first then drug names alphabetically."""
    present = {c["condition"] for c in cells}
    ordered = [""] if "" in present else []
    return ordered + sorted(present - {""})


def _build_color_map(pair_keys: list[str]) -> dict[str, tuple]:
    """Assign a stable color to each *physical cell* using a sequential
    ``Blues`` colormap.  Keying on pair_key rather than cell_id means a
    cell's baseline and drug points share a color, so a connecting line
    agrees with both of its endpoints.  The mapping is deterministic
    across the three figures.  Samples are taken in 0.35..0.95 to avoid
    near-white shades that vanish on a white background.
    """
    sorted_ids = sorted(set(pair_keys))
    n = len(sorted_ids)
    cmap = plt.get_cmap("Blues")
    if n == 1:
        return {sorted_ids[0]: cmap(0.7)}
    fractions = np.linspace(0.35, 0.95, n)
    return {cid: cmap(f) for cid, f in zip(sorted_ids, fractions)}


_DRUG_LINESTYLES = ["--", ":", "-."]


def _condition_styles(conditions: list[str]) -> dict[str, str]:
    """Map each condition to a linestyle: baseline solid, drugs dashed /
    dotted / dash-dot in the order returned by :func:`_ordered_conditions`."""
    styles: dict[str, str] = {}
    drug_i = 0
    for cond in conditions:
        if not cond:
            styles[cond] = "-"
        else:
            styles[cond] = _DRUG_LINESTYLES[drug_i % len(_DRUG_LINESTYLES)]
            drug_i += 1
    return styles


def _condition_label(condition: str, cell_type: str) -> str:
    """Axis / legend text for a condition."""
    return f"+ {condition}" if condition else cell_type


def _drug_legend_handles(
    conditions: list[str], styles: dict[str, str]
) -> list[Line2D]:
    """Neutral-grey proxy handles explaining the filled/hollow and
    linestyle conventions, one entry per condition present."""
    grey = "0.3"
    return [
        Line2D(
            [0], [0], color=grey, lw=2.0, linestyle=styles[cond],
            marker="o", markersize=8,
            markerfacecolor="none" if cond else grey,
            markeredgecolor=grey,
            label=cond if cond else "no drug",
        )
        for cond in conditions
    ]


SPINE_OFFSET_PT = 10    # outward shift, so the x and y axes do not meet
AXIS_PAD_FRAC = 0.08    # data-limit padding, as a fraction of the data range

# Fewest cells at an amplitude to pool a point.  2 rather than 3 because
# amplitude coverage is uneven -- most amplitudes in the dvmn set were
# tested by a single cell, and at 3 the mean curve collapses to 4 points
# ending at 150 pA while individual cells run to 300.  At 2 it keeps the
# full -100..300 pA range and every point still has a SEM.
MEAN_CURVE_MIN_CELLS = 2
MEAN_CURVE_GREY = "#898781"  # thin per-cell lines when identity is not encoded
# Leading slots of a CVD-checked categorical palette, used for condition
# means.  Only the first few are ever needed -- there are never many
# conditions in one figure.
CONDITION_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]


def _clean_spines(
    ax: plt.Axes,
    x_pad: float | None = AXIS_PAD_FRAC,
    y_pad: float | None = AXIS_PAD_FRAC,
) -> None:
    """Remove top and right spines and detach the two that remain.

    The left and bottom spines are pushed outward so they no longer join
    at the corner, and the data limits are padded so markers sitting at
    the extremes are not clipped by the axes frame.  The padding is
    needed because kt.mplstyle sets ``axes.xmargin``/``axes.ymargin`` to
    0, which otherwise places extreme points exactly on the frame.

    Pass ``x_pad=None`` or ``y_pad=None`` to leave that axis alone, for
    callers that set their own limits on it.
    """
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_position(("outward", SPINE_OFFSET_PT))

    if x_pad is not None:
        ax.margins(x=x_pad)
    if y_pad is not None:
        ax.margins(y=y_pad)


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


def _first_nonempty(group: pd.DataFrame, column: str) -> str:
    """First non-blank value of ``column`` in ``group``, else ``""``.

    Used for the descriptive drug columns, which are blank on drug-free
    recordings and may be blank on some h5 files of a drug cell.
    """
    if column not in group.columns:
        return ""
    for value in group[column]:
        s = str(value).strip()
        if s and s.lower() != "nan":
            return s
    return ""


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
        "pair_key": group["_pair_key"].iloc[0],
        "condition": group["_condition"].iloc[0],
        "expt_id": group["expt_id"].iloc[0],
        "targeted_cell_type": group["targeted_cell_type"].iloc[0],
        "drug_applied": group["drug_applied"].iloc[0],
        "drug_name": _first_nonempty(group, "drug_name"),
        "drug_concentration": _first_nonempty(group, "drug_concentration"),
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

def _fi_matrix(cells: list[dict]) -> pd.DataFrame:
    """Amplitude x cell matrix of firing rates, aligned on step amplitude.

    Cells that did not test a given amplitude are NaN there, so the
    pooled mean at each amplitude uses only the cells that measured it.
    """
    series = {
        cell["cell_id"]: pd.Series(
            cell["fi_curve"]["firing_rate_hz"].to_numpy(),
            index=cell["fi_curve"]["amplitude_pA"].round(0).astype(int).to_numpy(),
        )
        for cell in cells
    }
    return pd.DataFrame(series).sort_index()


def _mean_and_sem(matrix: pd.DataFrame, min_cells: int = MEAN_CURVE_MIN_CELLS):
    """Pooled mean and SEM, restricted to adequately-sampled amplitudes.

    An amplitude tested by fewer than *min_cells* cells would otherwise
    put a point on the mean curve that its neighbours do not share, which
    reads as a kink in the F-I relationship but is really a change in
    which cells contribute.  Raising *min_cells* trims the sparse ends of
    the curve and makes the remaining points more comparable to each
    other; lowering it keeps more of the amplitude range.

    The bar is capped at the group size, so a group smaller than
    *min_cells* still plots rather than vanishing.  SEM is left NaN (no
    band) where fewer than 2 cells contribute.
    """
    n = matrix.notna().sum(axis=1)
    pooled = matrix[n >= min(min_cells, matrix.shape[1])]
    if pooled.empty:
        return None, None
    k = pooled.notna().sum(axis=1)
    return pooled.mean(axis=1), (pooled.std(axis=1) / np.sqrt(k)).where(k >= 2)


def _draw_mean_fi(
    ax: plt.Axes,
    drawn: list[dict],
    conditions: list[str],
    styles: dict[str, str],
    cell_type: str,
    mode: str,
    min_cells: int = MEAN_CURVE_MIN_CELLS,
) -> list[Line2D]:
    """Thin per-cell lines beneath a mean +/- SEM.  Returns legend handles.

    ``mode="split"`` draws one mean per condition, with each condition's
    thin lines tinted to match.  ``mode="pooled"`` greys every cell and
    draws a single mean across all of them, drug included.  With only
    baseline present the two are identical.
    """
    groups = (
        [("", drawn)] if mode == "pooled"
        else [(c, [x for x in drawn if x["condition"] == c]) for c in conditions]
    )
    groups = [(cond, g) for cond, g in groups if g]
    single = len(groups) == 1

    handles: list[Line2D] = []
    for i, (cond, group) in enumerate(groups):
        color = CONDITION_COLORS[i % len(CONDITION_COLORS)]
        # Identity is only worth encoding when more than one group is
        # on screen; otherwise the thin lines are spread, not identity.
        thin = MEAN_CURVE_GREY if single else color
        matrix = _fi_matrix(group)

        for cid in matrix.columns:
            s = matrix[cid].dropna()
            ax.plot(s.index, s.values, color=thin, lw=1.0, alpha=0.45, zorder=1)

        mean, sem = _mean_and_sem(matrix, min_cells)
        if mean is None:
            continue
        ax.fill_between(
            mean.index, mean - sem, mean + sem,
            color=color, alpha=0.18, lw=0, zorder=2,
        )
        label = (
            "mean ± SEM" if mode == "pooled"
            else f"{_condition_label(cond, cell_type)} (n={matrix.shape[1]})"
        )
        line, = ax.plot(
            mean.index, mean.values,
            color=color, lw=2.0, linestyle=styles.get(cond, "-"),
            marker="o", markersize=8, zorder=3, label=label,
        )
        handles.append(line)

    # A grey proxy only describes the thin lines when they are actually
    # grey.  With several conditions each group's thin lines carry its
    # own colour, and the per-condition entries already give the counts.
    if single:
        handles.insert(0, Line2D(
            [0], [0], color=MEAN_CURVE_GREY, lw=1.0, alpha=0.45,
            label=f"cells (n={len(drawn)})",
        ))
    return handles


def _plot_fi(
    cells: list[dict],
    cell_type: str,
    out_path: Path,
    color_map: dict[str, tuple],
    mean_curve: str | None = None,
    mean_min_cells: int = MEAN_CURVE_MIN_CELLS,
) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(5.5, 5.0))

    drawn = [c for c in cells
             if c["fi_curve"] is not None and not c["fi_curve"].empty]
    conditions = _ordered_conditions(drawn)
    styles = _condition_styles(conditions)

    if mean_curve is not None:
        handles = _draw_mean_fi(
            ax, drawn, conditions, styles, cell_type, mean_curve,
            min_cells=mean_min_cells,
        )
        ax.legend(handles=handles, frameon=False, loc="upper left")
    else:
        for cell in sorted(drawn, key=lambda c: c["cell_id"]):
            curve = cell["fi_curve"]
            color = color_map[cell["pair_key"]]
            is_drug = _is_drug_cell(cell)
            ax.plot(
                curve["amplitude_pA"],
                curve["firing_rate_hz"],
                color=color,
                lw=2.0,
                linestyle=styles[cell["condition"]],
                marker="o",
                markersize=8,
                markerfacecolor="none" if is_drug else color,
                markeredgecolor=color,
                zorder=2,
            )

        # Only worth a legend when the figure actually mixes conditions.
        if len(conditions) > 1:
            ax.legend(
                handles=_drug_legend_handles(conditions, styles),
                frameon=False, loc="upper left",
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
    sorted_cells = sorted(cells, key=lambda c: c["cell_id"])
    keep = [c for c in sorted_cells if not np.isnan(c[field])]

    # One column per condition -- a pooled mean +/- SEM across conditions
    # would be meaningless, and lumping two different drugs into a single
    # "drug" column would average caffeine with octopamine.  With only
    # baseline present this collapses to the single column the figure has
    # always shown.
    conditions = _ordered_conditions(keep)
    columns = [
        (cond, [c for c in keep if c["condition"] == cond]) for cond in conditions
    ]
    n_cols = max(len(columns), 1)

    fig, ax = plt.subplots(
        figsize=(3.0 if n_cols == 1 else 1.5 * n_cols + 1.2, 5.0)
    )

    if not keep:
        fig.tight_layout()
        fig.savefig(out_path, dpi=200)
        print(f"Saved {out_path}")
        return fig

    # Points of one physical cell, collected so its conditions can be
    # joined by a line: {pair_key: [(column_index, x, y), ...]}.
    tracks: dict[str, list[tuple[int, float, float]]] = {}

    rng = np.random.default_rng(seed=0)
    for center, (cond, group) in enumerate(columns):
        is_drug = bool(cond)
        xs = rng.uniform(-0.15, 0.15, size=len(group)) + center
        for x, cell in zip(xs, group):
            color = color_map[cell["pair_key"]]
            tracks.setdefault(cell["pair_key"], []).append(
                (center, float(x), float(cell[field]))
            )
            ax.scatter(
                [x], [cell[field]],
                s=80,
                facecolor="none" if is_drug else color,
                edgecolor=color if is_drug else "white",
                linewidth=1.5 if is_drug else 1.0,
                zorder=3,
            )

        vals = [c[field] for c in group]
        mean = float(np.mean(vals))
        sem = (
            float(np.std(vals, ddof=1) / np.sqrt(len(vals))) if len(vals) > 1 else 0.0
        )
        ax.errorbar(
            [center], [mean], yerr=[sem],
            fmt="_", color="black", markersize=30,
            capsize=8, lw=1.5, zorder=4,
        )

    # Join each cell's conditions, drawn under the markers.
    for pair_key, points in tracks.items():
        if len(points) < 2:
            continue
        ordered = sorted(points)
        ax.plot(
            [p[1] for p in ordered], [p[2] for p in ordered],
            color=color_map[pair_key], lw=1.0, alpha=0.7, zorder=2,
        )

    ax.set_xticks(range(len(columns)))
    ax.set_xticklabels([_condition_label(cond, cell_type) for cond, _ in columns])
    ax.set_xlim(-0.5, len(columns) - 0.5)
    ax.set_ylabel(ylabel)
    # x limits are the column layout, not data-driven, so only pad y.
    _clean_spines(ax, x_pad=None)

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
    include_drug: bool = False,
    mean_curve: str | None = None,
    mean_min_cells: int = MEAN_CURVE_MIN_CELLS,
) -> pd.DataFrame:
    """Build per-cell F-I, RMP, Ri summaries and figures for one cell type.

    When ``include_drug`` is False (the default) drug-applied recordings
    are dropped.  When True they are kept as their own cells, styled with
    dashed/hollow markers, and every output filename gains a
    ``_withdrug`` suffix so the drug-free figures are not overwritten.

    ``mean_curve`` restyles the F-I figure only: ``"split"`` draws thin
    per-cell lines under one mean +/- SEM per condition, ``"pooled"``
    greys every cell and draws a single mean across all of them.  Both
    get their own filename suffix so they can sit beside the default
    per-cell figure.  The RMP and Ri strips already show points plus a
    mean +/- SEM, so they are unaffected.

    ``mean_min_cells`` is how many cells must have tested an amplitude
    before it gets a point on the mean curve, which controls how far the
    curve extends into the sparsely-sampled ends.  It is capped at the
    group size, so a small group still plots.

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
    is_drug = batch["drug_applied"].map(_is_drug_truthy)
    if include_drug:
        n_drug = int(is_drug.sum())
        if n_drug:
            print(f"Including {n_drug} drug-applied recording(s).")
    else:
        n_dropped = int(is_drug.sum())
        batch = batch[~is_drug].copy()
        if n_dropped:
            print(f"Excluded {n_dropped} drug-applied recording(s).")
    if batch.empty:
        print(f"No recordings remain for cell type {cell_type!r}.")
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
    batch["_condition"] = batch.apply(_condition, axis=1)
    batch["_pair_key"] = batch.apply(
        lambda row: explicit_cell_ids.get(row["_basename"]) or _derive_pair_key(row),
        axis=1,
    )

    def _cell_id(row: pd.Series) -> str:
        """cell_id = physical cell + condition.

        A side-car `cell_id` names the physical cell, not the condition,
        so it still needs the drug suffix -- without it the baseline and
        drug recordings of that cell would be averaged together.  The
        bare explicit spelling is kept for baseline rows so drug-free
        runs are unaffected.
        """
        explicit = explicit_cell_ids.get(row["_basename"])
        cond = row["_condition"]
        if explicit:
            return f"{explicit}_drug-{cond}" if cond else explicit
        return f"{row['_pair_key']}_drug-{cond or 'no'}"

    batch["cell_id"] = batch.apply(_cell_id, axis=1)

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

    color_map = _build_color_map([c["pair_key"] for c in cells])

    # Plots.  The suffix keeps a --include-drug run from clobbering the
    # drug-free figures and manifest.
    suffix = "_withdrug" if include_drug else ""
    # Only the F-I figure changes with --mean-curve, so only it takes the
    # extra suffix; the strips are byte-identical either way.
    fi_suffix = suffix + (f"_mean-{mean_curve}" if mean_curve else "")
    fi_path = fig_dir / f"{cell_type}_fi{fi_suffix}.png"
    rmp_path = fig_dir / f"{cell_type}_rmp{suffix}.png"
    ri_path = fig_dir / f"{cell_type}_ri{suffix}.png"
    fig_fi = _plot_fi(cells, cell_type, fi_path, color_map,
                      mean_curve=mean_curve, mean_min_cells=mean_min_cells)
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
            "pair_key": c["pair_key"],
            "condition": c["condition"] or "baseline",
            "expt_id": c["expt_id"],
            "targeted_cell_type": c["targeted_cell_type"],
            "drug_applied": c["drug_applied"],
            "drug_name": c["drug_name"],
            "drug_concentration": c["drug_concentration"],
            "n_h5_files": c["n_h5_files"],
            "n_fi_repeats_used": c["n_fi_repeats_total"],
            "rmp_mV_mean": round(c["rmp_mV"], 2) if not np.isnan(c["rmp_mV"]) else "",
            "ri_MOhm_mean": round(c["ri_MOhm"], 1) if not np.isnan(c["ri_MOhm"]) else "",
            "fi_amps_pA": amps,
            "fi_rates_hz": rates,
        })
    manifest = pd.DataFrame(manifest_rows)
    manifest_path = summary_dir / f"{cell_type}_summary{suffix}.csv"
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
    p.add_argument("--include-drug", action="store_true",
                   help="Also plot drug-applied recordings, as separate cells "
                        "with dashed lines and hollow markers. Outputs are "
                        "written with a `_withdrug` suffix.")
    p.add_argument("--mean-curve", choices=("split", "pooled"), default=None,
                   help="Restyle the F-I figure as thin per-cell lines under "
                        "a mean +/- SEM: `split` gives one mean per drug "
                        "condition, `pooled` a single mean across all cells. "
                        "Written with a `_mean-{choice}` suffix.")
    p.add_argument("--mean-min-cells", type=int, default=MEAN_CURVE_MIN_CELLS,
                   metavar="N",
                   help="How many cells must have tested an amplitude for it "
                        "to get a point on the mean curve, capped at the "
                        f"group size (default: {MEAN_CURVE_MIN_CELLS}). "
                        "Only meaningful with --mean-curve.")
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
            include_drug=args.include_drug,
            mean_curve=args.mean_curve,
            mean_min_cells=args.mean_min_cells,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

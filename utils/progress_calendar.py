"""
progress_calendar.py — Experiment progress calendar from the experiment log.

Reads ``_experiment_log.csv`` and renders a GitHub-style weekday calendar
(week columns x Mon–Fri rows).  A day is dark gray if any recording was made
and green if at least one recording from that day is marked ``keep``.
Days listed in ``OUT_OF_LAB`` with no recordings have a slash through them.

Writes three files next to the log (or to ``--out-dir``):
``_progress_calendar.html`` (hover tooltips), ``.png`` and ``.pdf``.

Usage
-----
    python utils/progress_calendar.py
    python utils/progress_calendar.py --data-dir D:/data
    python utils/progress_calendar.py --csv D:/data/_experiment_log.csv --out-dir D:/figures
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import sys
from pathlib import Path

import pandas as pd

# expt IDs used for rig tests, not real experiments
TEST_EXPT_IDS = {"expt_xx"}

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]

# Days out of the lab, as inclusive (first, last) ranges.  Shown in their own
# color unless a recording was made that day anyway.
OUT_OF_LAB = [
    (dt.date(2026, 6, 18), dt.date(2026, 7, 1)),
    (dt.date(2026, 7, 31), dt.date(2026, 7, 31)),
    (dt.date(2026, 8, 31), dt.date(2026, 9, 1)),
    (dt.date(2026, 10, 2), dt.date(2026, 10, 2)),
    (dt.date(2026, 10, 23), dt.date(2026, 10, 23)),
]

# (light, dark) theme colors
COLORS = {
    "empty": ("#ebedf0", "#21262d"),
    "away": ("#ebedf0", "#21262d"),  # same as empty; told apart by a slash
    "ran": ("#8c959f", "#6e7681"),
    "kept": ("#2da44e", "#3fb950"),
}
SLASH = ("#b1b8c0", "#484f58")

LEGEND = [("empty", "No recordings"), ("away", "Out of lab"), ("ran", "Recorded"), ("kept", "Kept")]


def _out_of_lab(day: dt.date) -> bool:
    return any(first <= day <= last for first, last in OUT_OF_LAB)


def summarize_days(log: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the experiment log into one row per recording day.

    The date comes from the ``_YYYYMMDD_HHMMSS_metadata.json`` suffix of
    ``metadata_file``.  Returns a DataFrame indexed by date with columns
    ``n_recordings``, ``n_kept``, ``expt_ids``, ``cell_types``, ``genotypes``.
    """
    df = log[~log["expt_id"].isin(TEST_EXPT_IDS)].copy()
    stamp = df["metadata_file"].str.extract(r"_(\d{8})_\d{6}_metadata\.json$")[0]
    df["date"] = pd.to_datetime(stamp, format="%Y%m%d").dt.date
    df = df.dropna(subset=["date"])
    df["kept"] = df["keep"].astype(str).str.strip().str.lower() == "true"

    def _unique(s: pd.Series) -> list[str]:
        return sorted({str(v).strip() for v in s.dropna() if str(v).strip()})

    return df.groupby("date").agg(
        n_recordings=("expt_id", "size"),
        n_kept=("kept", "sum"),
        expt_ids=("expt_id", _unique),
        cell_types=("targeted_cell_type", _unique),
        genotypes=("genotype", _unique),
    )


def _weekday_grid(start: dt.date, end: dt.date) -> list[list[dt.date]]:
    """Week columns (Mon–Fri dates) covering *start* through *end*."""
    monday = start - dt.timedelta(days=start.weekday())
    weeks = []
    while monday <= end:
        weeks.append([monday + dt.timedelta(days=i) for i in range(5)])
        monday += dt.timedelta(weeks=1)
    return weeks


def _month_labels(weeks: list[list[dt.date]]) -> list[tuple[int, str]]:
    """(column index, month abbrev) wherever a column starts a new month."""
    labels, prev = [], None
    for i, week in enumerate(weeks):
        month = week[-1].month  # Friday decides, so a Mar 30 week reads "Apr"
        if month != prev:
            labels.append((i, week[-1].strftime("%b")))
            prev = month
    return labels


def _status(day: dt.date, days: pd.DataFrame) -> str:
    if day not in days.index:
        return "away" if _out_of_lab(day) else "empty"
    return "kept" if days.at[day, "n_kept"] > 0 else "ran"


def _tooltip(day: dt.date, days: pd.DataFrame) -> str:
    """Tooltip HTML for one day; values from the CSV are escaped."""
    head = day.strftime("%a %b %d, %Y").replace(" 0", " ")
    if day not in days.index:
        return f"<b>{head}</b><br>" + ("Out of lab" if _out_of_lab(day) else "No recordings")
    r = days.loc[day]
    rows = [
        f"{r.n_recordings} recording{'s' if r.n_recordings != 1 else ''} · {r.n_kept} kept",
        "Expt: " + ", ".join(r.expt_ids),
    ]
    if r.cell_types:
        rows.append("Cell type: " + ", ".join(r.cell_types))
    if r.genotypes:
        rows.append("Genotype: " + ", ".join(r.genotypes))
    return f"<b>{head}</b><br>" + "<br>".join(html.escape(x) for x in rows)


# ── HTML ──────────────────────────────────────────────────────────────────────

CELL, GAP, LEFT, TOP = 14, 3, 34, 20


def render_html(days: pd.DataFrame, weeks: list[list[dt.date]], today: dt.date) -> str:
    step = CELL + GAP
    width = LEFT + len(weeks) * step
    height = TOP + 5 * step
    parts = []
    for col, label in _month_labels(weeks):
        parts.append(f'<text class="lbl" x="{LEFT + col * step}" y="12">{label}</text>')
    for row, name in enumerate(WEEKDAYS):
        if row % 2 == 0:  # Mon / Wed / Fri, like GitHub
            parts.append(f'<text class="lbl" x="0" y="{TOP + row * step + CELL - 3}">{name}</text>')
    for col, week in enumerate(weeks):
        for row, day in enumerate(week):
            if day > today:
                continue
            cls = _status(day, days)
            if day == today:
                cls += " today"
            tip = html.escape(_tooltip(day, days), quote=True)
            x, y = LEFT + col * step, TOP + row * step
            parts.append(
                f'<rect class="{cls}" x="{x}" y="{y}" '
                f'width="{CELL}" height="{CELL}" rx="3" data-tip="{tip}"/>'
            )
            if cls.startswith("away"):
                i = 3
                parts.append(f'<line class="slash" x1="{x + i}" y1="{y + CELL - i}" '
                             f'x2="{x + CELL - i}" y2="{y + i}"/>')
    svg = (f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
           f'role="img" aria-label="Experiment calendar">{"".join(parts)}</svg>')

    first, last = days.index.min(), days.index.max()
    span = f"{first:%b %d, %Y} – {last:%b %d, %Y}".replace(" 0", " ")
    light = {k: v[0] for k, v in COLORS.items()}
    dark = {k: v[1] for k, v in COLORS.items()}
    legend = "\n  ".join(f'<span><i class="{k}"></i>{text}</span>' for k, text in LEGEND)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Experiment Progress</title>
<style>
:root {{
  --bg: #ffffff; --fg: #1f2328; --muted: #656d76; --tip-bg: #1f2328; --tip-fg: #ffffff;
  --empty: {light['empty']}; --away: {light['away']}; --ran: {light['ran']}; --kept: {light['kept']};
  --slash: {SLASH[0]};
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #0d1117; --fg: #e6edf3; --muted: #8b949e; --tip-bg: #e6edf3; --tip-fg: #0d1117;
    --empty: {dark['empty']}; --away: {dark['away']}; --ran: {dark['ran']}; --kept: {dark['kept']};
    --slash: {SLASH[1]};
  }}
}}
body {{ margin: 0; background: var(--bg); color: var(--fg);
  font: 14px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }}
main {{ max-width: 960px; margin: 48px auto; padding: 0 16px; }}
h1 {{ font-size: 20px; font-weight: 600; margin: 0 0 4px; }}
.sub {{ color: var(--muted); margin: 0 0 24px; }}
.scroll {{ overflow-x: auto; padding-bottom: 4px; }}
svg {{ display: block; }}
.lbl {{ fill: var(--muted); font-size: 11px; }}
rect {{ stroke: rgba(27,31,36,0.06); stroke-width: 1; }}
rect.empty {{ fill: var(--empty); }}
rect.away {{ fill: var(--away); }}
rect.ran {{ fill: var(--ran); }}
rect.kept {{ fill: var(--kept); }}
rect.today {{ stroke: var(--fg); stroke-width: 1.5; }}
rect:hover {{ stroke: var(--fg); stroke-width: 1.5; }}
line.slash {{ stroke: var(--slash); stroke-width: 1.5; stroke-linecap: round; pointer-events: none; }}
.legend {{ display: flex; gap: 16px; align-items: center; margin-top: 14px;
  color: var(--muted); font-size: 12px; }}
.legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
.legend i {{ width: 12px; height: 12px; border-radius: 3px; display: inline-block; }}
.legend i.empty {{ background: var(--empty); }}
.legend i.away {{ background: linear-gradient(to top right, transparent calc(50% - 0.8px),
  var(--slash) calc(50% - 0.8px), var(--slash) calc(50% + 0.8px), transparent calc(50% + 0.8px)) var(--away); }}
.legend i.ran {{ background: var(--ran); }}
.legend i.kept {{ background: var(--kept); }}
#tip {{ position: fixed; pointer-events: none; background: var(--tip-bg); color: var(--tip-fg);
  padding: 6px 9px; border-radius: 6px; font-size: 12px; line-height: 1.5;
  max-width: 280px; opacity: 0; transition: opacity .08s; z-index: 10; }}
</style></head>
<body><main>
<h1>Experiment progress</h1>
<p class="sub">{span} · generated {today:%b %d, %Y}</p>
<div class="scroll">{svg}</div>
<div class="legend">
  {legend}
</div>
</main>
<div id="tip"></div>
<script>
const tip = document.getElementById('tip');
document.querySelectorAll('rect[data-tip]').forEach(r => {{
  r.addEventListener('mouseenter', () => {{ tip.innerHTML = r.dataset.tip; tip.style.opacity = 1; }});
  r.addEventListener('mouseleave', () => {{ tip.style.opacity = 0; }});
  r.addEventListener('mousemove', e => {{
    const w = tip.offsetWidth, x = e.clientX + 12;
    tip.style.left = (x + w > innerWidth - 8 ? e.clientX - w - 12 : x) + 'px';
    tip.style.top = (e.clientY + 14) + 'px';
  }});
}});
</script>
</body></html>
"""


# ── PNG / PDF ─────────────────────────────────────────────────────────────────

def _slash(ax, x: float, y: float, size: float) -> None:
    """Bottom-left to top-right slash across a cell at (x, y)."""
    i = size * 0.2
    ax.plot([x + i, x + size - i], [y + i, y + size - i], color=SLASH[0],
            linewidth=0.9, solid_capstyle="round")


def render_figure(days: pd.DataFrame, weeks: list[list[dt.date]], today: dt.date, stem: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    fill = {k: v[0] for k, v in COLORS.items()}
    muted = "#656d76"
    size, pad = 0.82, 0.08

    fig_w = 1.2 + len(weeks) * 0.22
    fig, ax = plt.subplots(figsize=(fig_w, 2.0))
    for col, week in enumerate(weeks):
        for row, day in enumerate(week):
            if day > today:
                continue
            status = _status(day, days)
            ax.add_patch(FancyBboxPatch(
                (col, -row), size, size, boxstyle=f"round,pad=0,rounding_size={pad * 1.5}",
                facecolor=fill[status], edgecolor="none",
            ))
            if status == "away":
                _slash(ax, col, -row, size)
    for col, label in _month_labels(weeks):
        ax.text(col, 1.25, label, fontsize=8, color=muted, va="bottom")
    for row, name in enumerate(WEEKDAYS):
        if row % 2 == 0:
            ax.text(-0.4, -row + size / 2, name, fontsize=7, color=muted, ha="right", va="center")

    legend_y = -5.0
    x = len(weeks) - sum(1.2 + len(text) * 0.32 for _, text in LEGEND)
    for key, text in LEGEND:
        ax.add_patch(FancyBboxPatch((x, legend_y), 0.6, 0.6,
                                    boxstyle=f"round,pad=0,rounding_size={pad * 1.2}",
                                    facecolor=fill[key], edgecolor="none"))
        if key == "away":
            _slash(ax, x, legend_y, 0.6)
        ax.text(x + 0.85, legend_y + 0.3, text, fontsize=7, color=muted, va="center")
        x += 1.2 + len(text) * 0.32

    first, last = days.index.min(), days.index.max()
    span = f"{first:%b %d, %Y} – {last:%b %d, %Y}".replace(" 0", " ")
    ax.text(-2.2, 2.3, "Experiment progress", fontsize=11, fontweight="semibold", color="#1f2328")
    ax.text(len(weeks), 2.3, span, fontsize=8, color=muted, ha="right")

    ax.set_xlim(-2.4, len(weeks) + 0.2)
    ax.set_ylim(legend_y - 0.4, 3.1)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.savefig(stem.with_suffix(".png"), dpi=200, bbox_inches="tight", facecolor="white")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("D:/data"),
                        help="Directory containing _experiment_log.csv (default: D:/data)")
    parser.add_argument("--csv", type=Path, default=None,
                        help="Path to the experiment log (default: <data-dir>/_experiment_log.csv)")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="Where to write the calendar files (default: the CSV's directory)")
    args = parser.parse_args()

    csv_path = args.csv.resolve() if args.csv else args.data_dir.resolve() / "_experiment_log.csv"
    if not csv_path.exists():
        sys.exit(f"Experiment log not found: {csv_path}\nRun update_experiment_log.py first.")
    try:
        write_calendar(csv_path, args.out_dir.resolve() if args.out_dir else None)
    except ValueError as e:
        sys.exit(str(e))


def write_calendar(csv_path: Path, out_dir: Path | None = None) -> Path:
    """Render the calendar from *csv_path*; return the HTML path.

    Files go to *out_dir* (default: the CSV's directory).  Raises ValueError
    if the log has no dated recordings.
    """
    out_dir = out_dir or csv_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    days = summarize_days(pd.read_csv(csv_path))
    if days.empty:
        raise ValueError("No dated recordings in the experiment log.")
    today = dt.date.today()
    weeks = _weekday_grid(days.index.min(), max(today, days.index.max()))

    stem = out_dir / "_progress_calendar"
    stem.with_suffix(".html").write_text(render_html(days, weeks, today), encoding="utf-8")
    render_figure(days, weeks, today, stem)

    n_kept = int((days["n_kept"] > 0).sum())
    print(f"{len(days)} recording days, {n_kept} with kept recordings")
    for ext in (".html", ".png", ".pdf"):
        print(f"  wrote {stem.with_suffix(ext)}")
    return stem.with_suffix(".html")


if __name__ == "__main__":
    main()

"""
update_experiment_log.py — Build / refresh an experiment database CSV.

Scans a data directory for all *_metadata.json sidecar files produced by the
acquisition system and merges their contents into a CSV log.  User-added
columns (notes, quality scores, etc.) are never overwritten.

Usage
-----
    python update_experiment_log.py
    python update_experiment_log.py --data-dir D:/data
    python update_experiment_log.py --data-dir D:/data --output D:/data/_experiment_log.csv
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Columns that this script owns and will refresh on every run.
# Anything else in the CSV is treated as user data and left alone.
# ---------------------------------------------------------------------------
AUTO_COLUMNS = [
    "expt_id",
    "genotype",
    "age",
    "sex",
    "metadata_file",
    "drug_applied",
    "drug_name",
    "drug_concentration",
    "duration_seconds",
    "clamp_mode",
    "notes",
]


def _parse_metadata(json_path: Path, data_dir: Path) -> dict:
    """Return a dict of auto-column values from one metadata JSON file."""
    with open(json_path, encoding="utf-8") as fh:
        meta = json.load(fh)

    subject = meta.get("subject", {})
    files = meta.get("files", {})

    return {
        "expt_id": subject.get("expt_id", ""),
        "genotype": subject.get("genotype", ""),
        "age": subject.get("age", ""),
        "sex": subject.get("sex", ""),
        "metadata_file": str(json_path.relative_to(data_dir)).replace("\\", "/"),
        "drug_applied": subject.get("drug_applied", False),
        "drug_name": subject.get("drug_name", ""),
        "drug_concentration": subject.get("drug_concentration", ""),
        "duration_seconds": meta.get("duration_seconds", ""),
        "clamp_mode": meta.get("clamp_mode", ""),
        "notes": subject.get("notes", ""),
    }


def _read_csv(csv_path: Path) -> tuple[list[str], list[dict]]:
    """Return (fieldnames, rows) from an existing CSV, or ([], []) if absent."""
    if not csv_path.exists():
        return [], []

    import csv
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return fieldnames, rows


def _write_csv(csv_path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    import csv
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def update(data_dir: Path, output: Path) -> None:
    # ── 1. Discover all metadata JSON files ──────────────────────────────
    json_files = sorted(data_dir.rglob("*_metadata.json"))
    if not json_files:
        print(f"No *_metadata.json files found under {data_dir}")
        print("0 added, 0 updated.")
        return

    # ── 2. Parse each file ───────────────────────────────────────────────
    fresh: dict[str, dict] = {}
    errors = []
    for jf in json_files:
        try:
            row = _parse_metadata(jf, data_dir)
            fresh[row["metadata_file"]] = row
        except Exception as exc:
            errors.append((jf, exc))

    if errors:
        print("Warning: could not parse the following files:", file=sys.stderr)
        for jf, exc in errors:
            print(f"  {jf}: {exc}", file=sys.stderr)

    # ── 3. Load existing CSV ─────────────────────────────────────────────
    existing_fields, existing_rows = _read_csv(output)

    # Build index: metadata_file → row dict
    existing_index: dict[str, dict] = {}
    for row in existing_rows:
        key = row.get("metadata_file", "")
        if key:
            existing_index[key] = row

    # ── 4. Merge ─────────────────────────────────────────────────────────
    n_added = 0
    n_updated = 0

    for key, auto in fresh.items():
        if key in existing_index:
            # Refresh only auto columns; leave user columns intact
            existing_index[key].update(auto)
            n_updated += 1
        else:
            existing_index[key] = auto
            n_added += 1

    # ── 5. Determine final column order ──────────────────────────────────
    # Auto columns first (in defined order), then any user columns that
    # already existed, preserving their relative order.
    user_columns = [c for c in existing_fields if c not in AUTO_COLUMNS]
    final_fields = AUTO_COLUMNS + user_columns

    # Ensure every row has all final fields (fill blanks for new user columns)
    merged_rows = []
    for key in existing_index:
        row = existing_index[key]
        for col in final_fields:
            row.setdefault(col, "")
        merged_rows.append(row)

    # Sort by start_time so the CSV is chronological
    merged_rows.sort(key=lambda r: (r.get("start_time") or "", r.get("metadata_file") or ""))

    # ── 6. Write ─────────────────────────────────────────────────────────
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(output, final_fields, merged_rows)

    print(f"Database updated: {output}")
    print(f"  {n_added} row(s) added, {n_updated} row(s) updated, "
          f"{len(merged_rows)} total.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build / refresh the experiment log CSV from metadata JSON files."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("D:/data"),
        help="Root directory to scan for *_metadata.json files (default: D:/data)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path to the CSV log file (default: <data-dir>/_experiment_log.csv)",
    )
    args = parser.parse_args()

    data_dir: Path = args.data_dir.resolve()
    output: Path = args.output.resolve() if args.output else data_dir / "_experiment_log.csv"

    if not data_dir.exists():
        print(f"Error: data directory does not exist: {data_dir}", file=sys.stderr)
        sys.exit(1)

    update(data_dir, output)


if __name__ == "__main__":
    main()

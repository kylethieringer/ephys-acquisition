"""
CLI for the standalone hardware-alignment check.

Usage:
    python -m analysis.qc_alignment --save-dir D:/data [--no-phase-b]
                                    [--model-cell-MOhm 500]

Run with the Axon Patch-1U model cell in CELL mode (500 MΩ) patched in
place of a pipette.  The script drives the NI DAQ directly and writes an
HTML + JSON report to ``{save-dir}/_alignment_checks/``, plus a row in
the master drift-tracking CSV (``qc_alignment_history.csv``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows console defaults to cp1252 which can't render Unicode Ω, τ, ±.
# Force UTF-8 so Check.message strings printed below don't crash the CLI.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Run standalone hardware-alignment QC (Phase A rig + "
                    "Phase B model cell)."
    )
    p.add_argument(
        "--save-dir", type=Path, required=True,
        help="Data root directory.  Alignment output goes under "
             "'{save-dir}/_alignment_checks/'.",
    )
    p.add_argument(
        "--no-phase-b", action="store_true",
        help="Skip the model-cell phase (Phase A only).",
    )
    p.add_argument(
        "--model-cell-MOhm", type=float, default=500.0,
        help="Model-cell resistance in MOhm (default: 500).",
    )
    p.add_argument(
        "--non-interactive", action="store_true",
        help="Skip the prompts that ask you to flip the amplifier between "
             "current-clamp and voltage-clamp between sections.",
    )
    args = p.parse_args(argv)

    if not args.save_dir.exists():
        print(f"error: --save-dir {args.save_dir} does not exist",
              file=sys.stderr)
        return 2

    from analysis.qc.alignment import run_alignment, write_alignment_report

    sections = run_alignment(
        include_phase_b = not args.no_phase_b,
        model_cell_MOhm = args.model_cell_MOhm,
        interactive     = not args.non_interactive,
    )
    for section_name, checks in sections.items():
        print(f"\n[{section_name}]")
        for c in checks:
            print(f"  {c.status.value.upper():<5}  {c.name:55s}  {c.message}")

    paths = write_alignment_report(sections, args.save_dir)
    print(f"\nOverall status: {paths['status'].upper()}")
    print(f"HTML: {paths['html']}")
    print(f"JSON: {paths['json']}")
    print(f"CSV:  {paths['csv']}")
    return 0 if paths["status"] in ("pass", "warn", "skip") else 1


if __name__ == "__main__":
    raise SystemExit(main())

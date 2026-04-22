"""
CLI for running the QC report on an existing recording.

Usage:
    python -m analysis.qc_report path/to/recording.h5 [--no-write]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from analysis.qc.report import run_qc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Run post-recording QC and write qc_report.html + qc_report.json."
    )
    p.add_argument("h5_path", type=Path, help="Recording HDF5 file.")
    p.add_argument("--no-write", action="store_true",
                   help="Run checks and print summary only; do not write report files.")
    args = p.parse_args(argv)

    if not args.h5_path.exists():
        print(f"error: {args.h5_path} does not exist", file=sys.stderr)
        return 2

    result = run_qc(args.h5_path, write=not args.no_write)
    print(f"Overall status: {result['status'].upper()}")
    for section, checks in result["sections"].items():
        print(f"\n[{section}]")
        for c in checks:
            print(f"  {c['status'].upper():<5}  {c['name']:40s}  {c['message']}")
    if result.get("html_path"):
        print(f"\nHTML report: {result['html_path']}")
        print(f"JSON report: {result['json_path']}")

    return 0 if result["status"] in ("pass", "warn", "skip") else 1


if __name__ == "__main__":
    raise SystemExit(main())

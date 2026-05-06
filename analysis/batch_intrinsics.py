"""Batch driver for intrinsic-properties analysis.

Reads ``D:\\data\\_experiment_log.csv``, filters to recordings of the
requested cell type(s) where the ``keep`` column is truthy, and ensures
each one has a ``{stem}_intrinsics.csv`` in ``D:\\results\\csv``,
computing missing files via :func:`analyze_steps.process_file`.

Use as a building block for downstream summary analysis::

    from analysis.batch_intrinsics import collect_intrinsics

    df = collect_intrinsics("DNa01")
    # df has one row per recording: expt_id, targeted_cell_type,
    # h5_path, intrinsics_csv_path, status (cached | computed | error)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from analysis import analyze_steps

DEFAULT_LOG_PATH = Path(r"D:\data\_experiment_log.csv")
DEFAULT_DATA_ROOT = Path(r"D:\data")
DEFAULT_RESULTS_CSV_DIR = Path(r"D:\results\csv")

_TRUTHY = {"1", "true", "yes", "y", "keep", "t"}


def _is_truthy_keep(value: object) -> bool:
    if value is None:
        return False
    s = str(value).strip().lower()
    if not s or s == "nan":
        return False
    return s in _TRUTHY


def _h5_path_from_metadata_file(metadata_file: str, data_root: Path) -> Path:
    rel = metadata_file.replace("_metadata.json", ".h5")
    return data_root / rel


def collect_intrinsics(
    cell_types: str | list[str],
    log_path: Path = DEFAULT_LOG_PATH,
    data_root: Path = DEFAULT_DATA_ROOT,
    results_csv_dir: Path = DEFAULT_RESULTS_CSV_DIR,
) -> pd.DataFrame:
    """Return one row per kept recording of ``cell_types``, computing the
    intrinsics CSV first if it doesn't yet exist.

    Parameters
    ----------
    cell_types
        A single cell-type name or a list of names. Matched against the
        ``targeted_cell_type`` column in the experiment log.
    log_path, data_root, results_csv_dir
        Paths; the defaults match the standard Tuthill-lab acquisition
        layout.

    Returns
    -------
    pandas.DataFrame
        Columns: ``expt_id``, ``targeted_cell_type``, ``metadata_file``,
        ``h5_path``, ``intrinsics_csv_path``, ``drug_applied``,
        ``drug_name``, ``drug_concentration``, ``status`` (one of
        ``cached``, ``computed``, ``error``), ``error`` (str, only set
        when status is ``error``). Drug columns are passed through
        verbatim from the experiment log so callers can group on them.
    """
    if isinstance(cell_types, str):
        cell_types = [cell_types]
    cell_types_set = {ct for ct in cell_types}

    log = pd.read_csv(log_path, dtype=str).fillna("")

    if "targeted_cell_type" not in log.columns:
        raise KeyError(
            f"'targeted_cell_type' column not found in {log_path}. "
            f"Available columns: {list(log.columns)}"
        )
    if "keep" not in log.columns:
        raise KeyError(
            f"'keep' column not found in {log_path}. "
            f"Available columns: {list(log.columns)}"
        )

    matched = log[log["targeted_cell_type"].isin(cell_types_set)]
    kept = matched[matched["keep"].map(_is_truthy_keep)]
    if "clamp_mode" in kept.columns:
        kept = kept[kept["clamp_mode"].str.strip().str.lower() != "voltage_clamp"]

    rows: list[dict] = []
    for _, row in kept.iterrows():
        metadata_file = row.get("metadata_file", "")
        if not metadata_file:
            continue

        h5_path = _h5_path_from_metadata_file(metadata_file, data_root)
        csv_path = results_csv_dir / f"{h5_path.stem}_intrinsics.csv"

        out = {
            "expt_id": row.get("expt_id", ""),
            "targeted_cell_type": row.get("targeted_cell_type", ""),
            "metadata_file": metadata_file,
            "h5_path": str(h5_path),
            "intrinsics_csv_path": str(csv_path),
            "drug_applied": row.get("drug_applied", ""),
            "drug_name": row.get("drug_name", ""),
            "drug_concentration": row.get("drug_concentration", ""),
            "status": "",
            "error": "",
        }

        if csv_path.exists():
            out["status"] = "cached"
        elif not h5_path.exists():
            out["status"] = "error"
            out["error"] = f"h5 file not found: {h5_path}"
            print(f"[error] {metadata_file}: {out['error']}")
        else:
            print(f"[compute] {metadata_file} -> {csv_path.name}")
            try:
                result = analyze_steps.process_file(h5_path)
                if result is None:
                    out["status"] = "error"
                    out["error"] = "process_file returned None (load or detection failed)"
                elif not csv_path.exists():
                    out["status"] = "error"
                    out["error"] = f"process_file finished but {csv_path} was not written"
                else:
                    out["status"] = "computed"
            except Exception as exc:
                out["status"] = "error"
                out["error"] = f"{type(exc).__name__}: {exc}"
                print(f"[error] {metadata_file}: {out['error']}")

        rows.append(out)

    df = pd.DataFrame(
        rows,
        columns=[
            "expt_id",
            "targeted_cell_type",
            "metadata_file",
            "h5_path",
            "intrinsics_csv_path",
            "drug_applied",
            "drug_name",
            "drug_concentration",
            "status",
            "error",
        ],
    )

    n_cached = int((df["status"] == "cached").sum())
    n_computed = int((df["status"] == "computed").sum())
    n_error = int((df["status"] == "error").sum())
    print(
        f"\ncollect_intrinsics: {len(df)} matched recording(s) "
        f"({n_cached} cached, {n_computed} computed, {n_error} error)"
    )

    return df


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Batch driver: compute intrinsic-properties CSVs for all "
            "recordings of the given cell type(s) where the experiment "
            "log's `keep` column is truthy."
        ),
    )
    parser.add_argument(
        "cell_types",
        nargs="+",
        help="One or more values of `targeted_cell_type` to include.",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help=f"Experiment log CSV (default: {DEFAULT_LOG_PATH})",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=DEFAULT_DATA_ROOT,
        help=f"Root directory for recordings (default: {DEFAULT_DATA_ROOT})",
    )
    parser.add_argument(
        "--results-csv-dir",
        type=Path,
        default=DEFAULT_RESULTS_CSV_DIR,
        help=f"Output directory (default: {DEFAULT_RESULTS_CSV_DIR})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    df = collect_intrinsics(
        args.cell_types,
        log_path=args.log,
        data_root=args.data_root,
        results_csv_dir=args.results_csv_dir,
    )
    if not df.empty:
        print(df.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

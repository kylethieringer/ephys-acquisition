"""
Fire-and-forget QC runner used from the acquisition code path.

Spawns a daemon thread so the GUI thread never blocks on the multi-second
plotly/HTML rendering.  Every exception is caught and printed — a QC
failure must never affect the primary save path.
"""

from __future__ import annotations

import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable


def write_acquisition_log(h5_path: str | Path, events: list[dict]) -> Path | None:
    """Write buffer-fill warning events to ``{stem}_acquisition.log``.

    The log file is parsed by :mod:`analysis.qc.integrity` during report
    rendering.  Writing is a no-op if ``events`` is empty.  Returns the
    log path on success or ``None`` if no events were supplied.
    """
    if not events:
        return None
    h5 = Path(h5_path)
    log = h5.with_name(h5.stem + "_acquisition.log")
    ts = datetime.now().isoformat(timespec="seconds")
    lines: list[str] = [f"# buffer-fill events (drained {ts})"]
    for e in events:
        lines.append(
            f"WARN buffer_fill "
            f"sample_index={e.get('sample_index', -1)} "
            f"avail_samp_per_chan={e.get('avail_samp_per_chan', -1)} "
            f"threshold={e.get('threshold', -1)}"
        )
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log


def schedule_qc(
    h5_path: str | Path,
    on_status: Callable[[str], None] | None = None,
) -> threading.Thread:
    """Run QC on ``h5_path`` in a background daemon thread.

    Args:
        h5_path: The finalised HDF5 recording.
        on_status: Optional one-argument callable invoked with short status
            strings (``"QC: running…"``, ``"QC: pass"``, or an error line).
            Safe to pass a Qt signal's ``.emit`` — Qt auto-connection routes
            the call back to the receiver's thread.

    Returns:
        The daemon thread (already started).
    """
    def _worker() -> None:
        try:
            if on_status is not None:
                on_status("QC: running…")
            # Import inside the thread so the ~1 s module-import cost of
            # matplotlib/plotly is paid off the GUI thread.
            from analysis.qc.report import run_qc
            result = run_qc(h5_path, write=True)
            if on_status is not None:
                on_status(f"QC: {result['status']}")
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            if on_status is not None:
                on_status(f"QC failed: {exc}")

    t = threading.Thread(target=_worker, name="qc-runner", daemon=True)
    t.start()
    return t

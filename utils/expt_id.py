"""
Pure functions for deriving the next experiment ID from existing folder names.

No hardware or Qt dependencies — safe to import and test standalone.

Experiment IDs are a letter prefix followed by a zero-padded number, e.g.
``fre063``.  Each recording creates one folder per ID under the save directory
(see :class:`~acquisition.data_saver.DataSaver`), so the folder names in
``save_dir`` are the record of which IDs have been used.

Developer notes
---------------
:func:`next_expt_id` takes folder names rather than a path so it stays pure.
The caller is responsible for listing the directory and passing the names
**newest-modified first** — that ordering is what picks the series to continue
when the Experiment ID field is empty.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_ID_RE = re.compile(r"^([A-Za-z]+)(\d+)$")
"""Matches a valid experiment ID: letter prefix, then digits, nothing else."""

_PREFIX_RE = re.compile(r"[A-Za-z]+")
"""Extracts the leading letters of a typed prefix hint."""

_DEFAULT_WIDTH = 3
"""Zero-padding used when starting a brand-new series (``kt`` → ``kt001``)."""


def next_expt_id(names: Sequence[str], prefix: str = "") -> str | None:
    """Return the next experiment ID after the ones present in ``names``.

    Args:
        names: Existing folder names, ordered **newest-modified first**.
            Entries that are not a valid experiment ID (``expt_xx``,
            ``_experiment_log.csv``, …) are ignored.
        prefix: Optional prefix hint, typically the current contents of the
            Experiment ID field.  Only its leading letters are used, so both
            ``"fre"`` and ``"fre041"`` select the ``fre`` series.  Matching is
            case-insensitive and the existing folders' casing wins, so a hint
            of ``"FRE"`` still yields ``fre064``.

    Returns:
        The next ID in the selected series, e.g. ``"fre064"``.  The number is
        one past the highest existing number and is zero-padded to the widest
        width already in use, so ``fre099`` yields ``fre100``.  A hinted prefix
        with no existing folders starts a new series at ``001``.  Returns
        ``None`` when no series can be determined — no hint given and no valid
        experiment folders present.
    """
    parsed = [(m.group(1), m.group(2))
              for m in (_ID_RE.match(n) for n in names) if m]

    hint_match = _PREFIX_RE.match(prefix.strip())
    hint = hint_match.group(0) if hint_match else ""

    if hint:
        series = [(p, digits) for p, digits in parsed if p.lower() == hint.lower()]
        if not series:
            return f"{hint}{1:0{_DEFAULT_WIDTH}d}"
    else:
        if not parsed:
            return None
        newest_prefix = parsed[0][0]
        series = [(p, digits) for p, digits in parsed
                  if p.lower() == newest_prefix.lower()]

    # The newest folder in the series decides the casing of the returned ID.
    series_prefix = series[0][0]
    width = max(len(digits) for _, digits in series)
    number = max(int(digits) for _, digits in series) + 1
    return f"{series_prefix}{number:0{width}d}"

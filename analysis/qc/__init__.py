"""
Post-recording QC checks.

Public surface:

    from analysis.qc import Check, Status, run_qc
    result = run_qc("path/to/recording.h5")

Each check returns a :class:`Check` with a status (pass / warn / fail),
a short message, and a dict of numeric metrics that also lands in the
machine-readable JSON report.  No check ever raises — they catch and
downgrade to ``fail`` with the exception message, so the orchestrator
can always produce a report.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Status(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class Check:
    name: str
    status: Status
    message: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


def worst(checks: list[Check]) -> Status:
    order = {Status.SKIP: 0, Status.PASS: 1, Status.WARN: 2, Status.FAIL: 3}
    if not checks:
        return Status.SKIP
    return max((c.status for c in checks), key=lambda s: order[s])


__all__ = ["Check", "Status", "worst"]

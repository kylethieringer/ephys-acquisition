"""
Experiment-day checklist GUI.

A lightweight PySide6 helper that displays an ordered checklist of tasks for
an experiment day. Some tasks have a one-click launcher that starts the
relevant GUI or script (ephys acquisition, analysis GUI, analyze_steps, or
experiment-log refresh).

Tasks are defined in utils/checklist_tasks.json (hand-editable).  Checked
state is persisted per calendar day in utils/.checklist_state.json and
auto-resets when a new day begins.

Usage:
    python utils/experiment_checklist.py
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Paths and action mapping
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = Path(__file__).resolve().parent / "checklist_tasks.json"
STATE_FILE = Path(__file__).resolve().parent / ".checklist_state.json"

# Default location of the experiment log CSV; matches update_experiment_log.py's default.
EXPERIMENT_LOG_PATH = Path("D:/data/_experiment_log.csv")

# Subprocess actions: key → argv list passed to subprocess.Popen.
ACTIONS: dict[str, list[str]] = {
    "ephys_gui":     [sys.executable, str(PROJECT_ROOT / "main.py")],
    "analysis_gui":  [sys.executable, str(PROJECT_ROOT / "analysis" / "analysis_gui.py")],
    "analyze_steps": [sys.executable, str(PROJECT_ROOT / "analysis" / "analyze_steps.py")],
    "update_log":    [sys.executable, str(PROJECT_ROOT / "utils" / "update_experiment_log.py")],
}

# Non-subprocess actions handled directly in _run_action (e.g. open a file).
SPECIAL_ACTIONS: set[str] = {"open_log"}

# ---------------------------------------------------------------------------
# Stylesheet (subset of main.py's dark theme)
# ---------------------------------------------------------------------------

_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #14161a;
    color: #e7ebf0;
    font-size: 10pt;
}

QLabel { color: #e7ebf0; background: transparent; }
QLabel[tier="muted"]    { color: #7f8794; }
QLabel[tier="header"]   { color: #b8bec9; font-weight: 600; font-size: 11pt; }
QLabel[tier="footer"]   { color: #7f8794; font-size: 9pt; letter-spacing: 1px; }

QPushButton {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                      stop:0 #2e343e, stop:1 #262b34);
    border: 1px solid #3a414c;
    border-radius: 3px;
    padding: 4px 12px;
    color: #e7ebf0;
    font-weight: 500;
    min-height: 18px;
}
QPushButton:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                      stop:0 #383e4a, stop:1 #2e343e);
    border-color: #4a525e;
}
QPushButton:pressed {
    background-color: #1f242c;
}

QPushButton[accent="ghost"] {
    background-color: transparent;
    border: 1px solid transparent;
    color: #b8bec9;
}
QPushButton[accent="ghost"]:hover {
    background-color: #22262e;
    border-color: #3a414c;
    color: #e7ebf0;
}

QCheckBox { color: #e7ebf0; spacing: 10px; padding: 4px 2px; }
QCheckBox:checked { color: #7f8794; }
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #4a525e;
    border-radius: 3px;
    background-color: #2a2f38;
}
QCheckBox::indicator:hover { border-color: #a4afc1; }
QCheckBox::indicator:checked {
    background-color: #a4afc1;
    border-color: #7f8794;
    image: none;
}

QFrame[divider="true"] {
    background-color: #2e333d;
    max-height: 1px;
    min-height: 1px;
    border: none;
}

QScrollArea { border: none; background-color: transparent; }
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2e333d;
    border-radius: 5px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #3f4650; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


# ---------------------------------------------------------------------------
# Tasks + state I/O
# ---------------------------------------------------------------------------

def _known_action(key: str) -> bool:
    return key in ACTIONS or key in SPECIAL_ACTIONS


def load_tasks() -> list[dict]:
    """Load the ordered task list from JSON.

    Each task may have:
      - "action": "<key>"  (shorthand → one button labelled "Launch")
      - "actions": [{ "key": "<key>", "label": "<button-text>" }, ...]
        (one button per entry, in order)

    Returns a list of dicts: {id, label, actions: [(key, button_label), ...]}.
    """
    with open(TASKS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw = data.get("tasks", [])
    cleaned: list[dict] = []
    seen_ids: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            print(f"[checklist] skipping non-object task entry: {entry!r}", file=sys.stderr)
            continue
        tid = entry.get("id")
        label = entry.get("label")
        if not tid or not label:
            print(f"[checklist] skipping task missing id/label: {entry!r}", file=sys.stderr)
            continue
        if tid in seen_ids:
            print(f"[checklist] skipping duplicate task id: {tid!r}", file=sys.stderr)
            continue
        seen_ids.add(tid)

        actions: list[tuple[str, str]] = []
        if isinstance(entry.get("actions"), list):
            for a in entry["actions"]:
                if not isinstance(a, dict):
                    continue
                k = a.get("key")
                blabel = a.get("label") or "Launch"
                if not k or not _known_action(k):
                    print(f"[checklist] task {tid!r}: unknown action {k!r}; skipping",
                          file=sys.stderr)
                    continue
                actions.append((k, blabel))
        elif entry.get("action") is not None:
            k = entry["action"]
            if _known_action(k):
                actions.append((k, "Launch"))
            else:
                print(f"[checklist] task {tid!r}: unknown action {k!r}; ignoring",
                      file=sys.stderr)

        cleaned.append({"id": tid, "label": label, "actions": actions})
    return cleaned


def _today_iso() -> str:
    return _dt.date.today().isoformat()


def load_state() -> set[str]:
    """Return the set of checked task ids for today, or empty set."""
    if not STATE_FILE.exists():
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return set()
    if data.get("date") != _today_iso():
        return set()
    checked = data.get("checked", [])
    return {str(x) for x in checked if isinstance(x, (str, int))}


def save_state(checked: set[str]) -> None:
    """Atomically write today's checked-set to disk."""
    payload = {"date": _today_iso(), "checked": sorted(checked)}
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ChecklistWindow(QMainWindow):
    def __init__(self, tasks: list[dict], checked: set[str]) -> None:
        super().__init__()
        self.setWindowTitle("Experiment Checklist")
        self.resize(440, 600)

        self._tasks = tasks
        self._checked: set[str] = set(checked) & {t["id"] for t in tasks}
        self._checkboxes: dict[str, QCheckBox] = {}

        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # -- header ----------------------------------------------------------
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        today = _dt.date.today()
        date_label = QLabel(today.strftime("%A, %Y-%m-%d"))
        date_label.setProperty("tier", "header")
        header_row.addWidget(date_label)
        header_row.addStretch(1)
        reset_btn = QPushButton("Reset day")
        reset_btn.setProperty("accent", "ghost")
        reset_btn.clicked.connect(self._reset_day)
        header_row.addWidget(reset_btn)
        root.addLayout(header_row)

        divider = QFrame()
        divider.setProperty("divider", True)
        root.addWidget(divider)

        # -- task rows (scrollable) ------------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        rows_host = QWidget()
        rows_layout = QVBoxLayout(rows_host)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(2)
        for task in tasks:
            rows_layout.addWidget(self._build_row(task))
        rows_layout.addStretch(1)
        scroll.setWidget(rows_host)
        root.addWidget(scroll, 1)

        # -- footer ----------------------------------------------------------
        divider2 = QFrame()
        divider2.setProperty("divider", True)
        root.addWidget(divider2)

        self._progress = QLabel()
        self._progress.setProperty("tier", "footer")
        self._progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._progress)

        self._refresh_progress()

    # -- row building --------------------------------------------------------

    def _build_row(self, task: dict) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(6)

        cb = QCheckBox(task["label"])
        cb.setChecked(task["id"] in self._checked)
        cb.toggled.connect(lambda checked, tid=task["id"]: self._on_toggle(tid, checked))
        self._checkboxes[task["id"]] = cb
        layout.addWidget(cb, 1)

        for key, btn_label in task.get("actions", []):
            btn = QPushButton(btn_label)
            btn.clicked.connect(lambda _=False, k=key: self._run_action(k))
            layout.addWidget(btn, 0)

        return row

    # -- event handlers ------------------------------------------------------

    def _on_toggle(self, task_id: str, checked: bool) -> None:
        if checked:
            self._checked.add(task_id)
        else:
            self._checked.discard(task_id)
        try:
            save_state(self._checked)
        except OSError as e:
            QMessageBox.warning(self, "Checklist", f"Could not save state:\n{e}")
        self._refresh_progress()

    def _run_action(self, key: str) -> None:
        if key == "open_log":
            self._open_experiment_log()
            return

        cmd = ACTIONS.get(key)
        if cmd is None:
            QMessageBox.warning(self, "Checklist", f"Unknown action: {key!r}")
            return
        try:
            subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))
        except (FileNotFoundError, OSError) as e:
            QMessageBox.warning(
                self,
                "Checklist",
                f"Failed to launch {key!r}:\n{e}",
            )

    def _open_experiment_log(self) -> None:
        path = EXPERIMENT_LOG_PATH
        if not path.exists():
            QMessageBox.warning(
                self,
                "Open experiment log",
                f"Log file not found:\n{path}\n\n"
                "Click 'Refresh' to generate it from metadata files.",
            )
            return
        try:
            # os.startfile is Windows-only; this project runs on Windows.
            os.startfile(str(path))  # type: ignore[attr-defined]
        except OSError as e:
            QMessageBox.warning(
                self,
                "Open experiment log",
                f"Could not open {path}:\n{e}",
            )

    def _reset_day(self) -> None:
        if not self._checked:
            return
        resp = QMessageBox.question(
            self,
            "Reset checklist",
            "Uncheck all items and start over?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        for cb in self._checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self._checked.clear()
        try:
            save_state(self._checked)
        except OSError as e:
            QMessageBox.warning(self, "Checklist", f"Could not save state:\n{e}")
        self._refresh_progress()

    def _refresh_progress(self) -> None:
        self._progress.setText(f"{len(self._checked)} / {len(self._tasks)} complete")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Experiment Checklist")
    app.setOrganizationName("Lab")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(_STYLESHEET)

    try:
        tasks = load_tasks()
    except FileNotFoundError:
        QMessageBox.critical(
            None,
            "Checklist",
            f"Could not find task list:\n{TASKS_FILE}",
        )
        sys.exit(1)
    except json.JSONDecodeError as e:
        QMessageBox.critical(
            None,
            "Checklist",
            f"Could not parse {TASKS_FILE.name}:\n{e}",
        )
        sys.exit(1)

    if not tasks:
        QMessageBox.critical(None, "Checklist", "Task list is empty.")
        sys.exit(1)

    checked = load_state()
    window = ChecklistWindow(tasks, checked)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

"""Shared pytest setup.

Qt allows one application object per process, and widgets abort the process
unless it is a full ``QApplication``.  Creating it here, before any test runs,
means tests that only need ``QCoreApplication`` share it safely.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])

"""RMP baseline-window tests for :mod:`analysis.analyze_steps`.

Regression cover for back-to-back step protocols: when the silent gap
between one protocol and the next is shorter than ``BASELINE_MS``, the
pre-step baseline window must not reach back into the previous
protocol's final (depolarizing) step.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from analysis import analyze_steps as A

SR = 20_000
DISPLAY_SCALES = np.array([10.0, 2.0, 400.0, 1.0, 1.0])

REST_MV = -55.0
HYPERPOL_MV = -75.0
DEPOL_MV = -20.0
HYPERPOL_PA = -100.0
DEPOL_PA = 100.0


def _ms(milliseconds: float) -> int:
    return int(milliseconds / 1000.0 * SR)


def _build_recording(gap_ms: float, n_protocols: int = 3) -> tuple:
    """Synthesise a recording of back-to-back step protocols.

    Each protocol is a 500 ms hyperpolarizing step, a 100 ms pause, then
    a 500 ms depolarizing step.  Protocols are separated by *gap_ms* of
    silence at ``REST_MV``.

    Returns ``(data, step_protocols, step_protocol_meta)`` ready to pass
    to :func:`analyze_steps.compute_all_intrinsics`.
    """
    step_ms, inner_gap_ms, lead_in_ms = 500.0, 100.0, 1000.0
    protocol_ms = step_ms + inner_gap_ms + step_ms
    total_ms = lead_in_ms + n_protocols * protocol_ms + (n_protocols - 1) * gap_ms + 500.0

    n_samples = _ms(total_ms)
    vm_mV = np.full(n_samples, REST_MV)
    cmd_pA = np.zeros(n_samples)

    step_protocols, step_protocol_meta = [], []
    cursor_ms = lead_in_ms
    for idx in range(n_protocols):
        hyper_on, hyper_off = _ms(cursor_ms), _ms(cursor_ms + step_ms)
        depol_on = _ms(cursor_ms + step_ms + inner_gap_ms)
        depol_off = _ms(cursor_ms + protocol_ms)

        vm_mV[hyper_on:hyper_off] = HYPERPOL_MV
        cmd_pA[hyper_on:hyper_off] = HYPERPOL_PA
        vm_mV[depol_on:depol_off] = DEPOL_MV
        cmd_pA[depol_on:depol_off] = DEPOL_PA

        step_protocols.append([
            {"onset": hyper_on, "offset": hyper_off, "amplitude_pA": HYPERPOL_PA},
            {"onset": depol_on, "offset": depol_off, "amplitude_pA": DEPOL_PA},
        ])
        # Waveform detection sets apply_sample == first pulse onset.
        step_protocol_meta.append({
            "apply_sample": hyper_on,
            "stimulus_index": -1,
            "stimulus_name": "unknown",
        })
        cursor_ms += protocol_ms + gap_ms

    data = np.zeros((5, n_samples))
    data[A.VM_CH] = vm_mV / DISPLAY_SCALES[A.VM_CH]
    data[A.CMD_CH] = cmd_pA / DISPLAY_SCALES[A.CMD_CH]
    return data, step_protocols, step_protocol_meta


def _intrinsics(gap_ms: float, n_protocols: int = 3) -> list[dict]:
    data, protos, meta = _build_recording(gap_ms, n_protocols)
    return A.compute_all_intrinsics(
        data, protos, meta, [], SR, DISPLAY_SCALES,
    )


def test_rmp_correct_when_gap_exceeds_baseline_window():
    """A long inter-protocol gap leaves the baseline entirely at rest."""
    results = _intrinsics(gap_ms=2000.0)
    for row in results:
        assert row["rmp_mV"] == pytest.approx(REST_MV, abs=0.5)


def test_rmp_not_contaminated_by_previous_protocol():
    """A 200 ms gap is shorter than BASELINE_MS (500 ms), so the naive
    look-back window overlaps the previous protocol's depolarizing step."""
    results = _intrinsics(gap_ms=200.0)
    for row in results:
        assert row["rmp_mV"] == pytest.approx(REST_MV, abs=0.5), (
            f"protocol {row['step_protocol_index']} reported "
            f"{row['rmp_mV']} mV; baseline window reached into the "
            f"previous protocol's depolarizing step ({DEPOL_MV} mV)"
        )


def test_input_resistance_unaffected_by_short_gap():
    """Ri is derived from RMP, so baseline contamination inflates it."""
    expected = (HYPERPOL_MV - REST_MV) / (HYPERPOL_PA / 1000.0)
    results = _intrinsics(gap_ms=200.0)
    for row in results:
        assert row["input_resistance_MOhm"] == pytest.approx(expected, abs=5.0)


def test_rmp_is_nan_when_clean_baseline_too_short():
    """Too little settled baseline to measure -> NaN, not a guess."""
    results = _intrinsics(gap_ms=40.0)
    # First protocol has the full lead-in, so it is still measurable.
    assert results[0]["rmp_mV"] == pytest.approx(REST_MV, abs=0.5)
    for row in results[1:]:
        assert np.isnan(row["rmp_mV"]), (
            f"protocol {row['step_protocol_index']} reported "
            f"{row['rmp_mV']} mV from a sub-threshold baseline window"
        )

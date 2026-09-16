"""Tests for the live spike-audio monitor DSP and chunk routing.

These tests exercise :mod:`hardware.audio_monitor` without touching real
audio hardware.  ``SpikeAudioFilter`` is pure numpy, and ``AudioMonitor``
takes an injectable output factory so the Qt sink is never constructed.
"""

from __future__ import annotations

import numpy as np
import pytest

from config import AUDIO_DEFAULT_GAIN
from hardware.audio_monitor import AudioMonitor, SpikeAudioFilter, gain_from_volume

FS = 20_000
CUTOFF = 100.0


def make_filter(gain: float = 1.0) -> SpikeAudioFilter:
    return SpikeAudioFilter(sample_rate=FS, cutoff_hz=CUTOFF, gain=gain)


class FakeAudioOut:
    """Stand-in for the Qt sink adapter: records what was written."""

    def __init__(self, bytes_free: int = 1 << 20) -> None:
        self.written: bytes = b""
        self._bytes_free = bytes_free
        self.closed = False

    def bytes_free(self) -> int:
        return self._bytes_free

    def write(self, data: bytes) -> None:
        self.written += bytes(data)

    def close(self) -> None:
        self.closed = True

    @property
    def samples(self) -> np.ndarray:
        return np.frombuffer(self.written, dtype=np.int16)


# ---------------------------------------------------------------------------
# SpikeAudioFilter — DC removal
# ---------------------------------------------------------------------------


def test_constant_resting_potential_is_silent():
    """A steady -60 mV (-6.0 V raw) baseline must produce no sound at all."""
    out = make_filter().process(np.full(200, -6.0))
    assert np.all(out == 0)


def test_first_chunk_has_no_startup_thump():
    """Enabling audio mid-recording must not emit a transient from the DC step.

    The filter seeds its input history from the first sample it ever sees,
    so a baseline that was already present before the monitor was switched
    on reads as silence rather than a step edge.
    """
    out = make_filter().process(np.full(50, -6.0))
    assert np.all(out == 0)


def test_slow_baseline_drift_is_attenuated():
    """A 1 Hz drift is far below the 100 Hz corner and must be suppressed."""
    t = np.arange(FS) / FS
    drift = 5.0 * np.sin(2 * np.pi * 1.0 * t)
    out = make_filter(gain=0.1).process(drift)
    # 5 V of drift would be 16383 counts unfiltered; the corner is 100x up.
    assert np.abs(out).max() < 500


# ---------------------------------------------------------------------------
# SpikeAudioFilter — cross-chunk continuity
# ---------------------------------------------------------------------------


def test_filter_state_persists_across_chunk_boundaries():
    """Splitting a signal into DAQ-sized chunks must not change the output.

    Filter state that resets per chunk would put a discontinuity at every
    200-sample boundary, i.e. an audible 100 Hz buzz on top of the data.
    """
    t = np.arange(400) / FS
    signal = 5.0 * np.sin(2 * np.pi * 1000.0 * t)

    whole = make_filter(gain=0.1).process(signal)

    chunked_filter = make_filter(gain=0.1)
    first = chunked_filter.process(signal[:200])
    second = chunked_filter.process(signal[200:])

    assert np.array_equal(whole, np.concatenate([first, second]))


def test_reset_clears_filter_state():
    """After reset the filter behaves like a freshly constructed one."""
    f = make_filter(gain=0.1)
    spike = np.full(200, -6.0)
    spike[100:120] = 4.0
    f.process(spike)

    f.reset()

    assert np.all(f.process(np.full(200, -6.0)) == 0)


# ---------------------------------------------------------------------------
# SpikeAudioFilter — spikes, gain, clipping
# ---------------------------------------------------------------------------


def test_action_potential_produces_a_loud_transient():
    """A 1 ms spike on a quiet baseline must be the loudest thing in the chunk."""
    x = np.full(2000, -6.0)
    x[1000:1020] = 4.0          # -60 mV -> +40 mV for 1 ms

    out = make_filter(gain=0.05).process(x)

    assert np.abs(out[:900]).max() == 0            # baseline is silent
    assert np.abs(out[1000:1100]).max() > 10_000   # spike is loud


def test_gain_scales_the_output():
    x = np.zeros(100)
    x[50] = 1.0

    quiet = make_filter(gain=0.01).process(x)
    loud = make_filter(gain=0.02).process(x)

    assert loud.max() == pytest.approx(2 * quiet.max(), rel=0.01)


def test_loud_input_clips_instead_of_wrapping():
    """Integer overflow would turn a big spike into a full-scale square wave."""
    x = np.zeros(100)
    x[50] = 100.0
    x[70] = -100.0

    out = make_filter(gain=10.0).process(x)

    assert out.max() == 32767
    assert out.min() == -32767
    assert out.dtype == np.int16


def test_output_is_int16():
    out = make_filter().process(np.zeros(200))
    assert out.dtype == np.int16


# ---------------------------------------------------------------------------
# AudioMonitor — chunk routing
# ---------------------------------------------------------------------------


def test_disabled_monitor_writes_nothing():
    fake = FakeAudioOut()
    mon = AudioMonitor(output_factory=lambda: fake)

    mon.push(np.random.randn(5, 200))

    assert fake.written == b""


def test_enabled_monitor_plays_the_membrane_channel():
    """Only row 0 (ScAmpOut / I_mem) reaches the speaker."""
    fake = FakeAudioOut()
    mon = AudioMonitor(output_factory=lambda: fake, gain=0.1)
    mon.set_enabled(True)

    chunk = np.zeros((5, 200))
    chunk[0] = -6.0      # steady membrane potential -> silence
    chunk[1] = 9.0       # other channels must be ignored
    chunk[3] = 5.0
    mon.push(chunk)

    assert len(fake.samples) == 200
    assert np.all(fake.samples == 0)


def test_monitor_passes_spikes_through_to_the_output():
    fake = FakeAudioOut()
    mon = AudioMonitor(output_factory=lambda: fake, gain=0.05)
    mon.set_enabled(True)

    chunk = np.full((5, 200), -6.0)
    chunk[0, 100:120] = 4.0
    mon.push(chunk)

    assert np.abs(fake.samples).max() > 10_000


def test_chunk_is_dropped_when_the_sink_is_backed_up():
    """Falling behind must glitch, not accumulate latency."""
    fake = FakeAudioOut(bytes_free=10)      # room for 5 samples, need 200
    mon = AudioMonitor(output_factory=lambda: fake, gain=0.05)
    mon.set_enabled(True)

    chunk = np.full((5, 200), -6.0)
    chunk[0, 100:120] = 4.0
    mon.push(chunk)

    assert fake.written == b""


def test_disabling_closes_the_output():
    fake = FakeAudioOut()
    mon = AudioMonitor(output_factory=lambda: fake)
    mon.set_enabled(True)
    mon.push(np.zeros((5, 200)))

    mon.set_enabled(False)

    assert fake.closed


def test_reenabling_starts_from_a_clean_baseline():
    """Toggling off and on must not replay stale filter state as a thump."""
    outs = []

    def factory():
        outs.append(FakeAudioOut())
        return outs[-1]

    mon = AudioMonitor(output_factory=factory, gain=0.1)
    mon.set_enabled(True)
    spike = np.full((5, 200), -6.0)
    spike[0, 100:120] = 4.0
    mon.push(spike)
    mon.set_enabled(False)

    mon.set_enabled(True)
    mon.push(np.full((5, 200), -6.0))

    assert np.all(outs[-1].samples == 0)


def test_missing_audio_device_is_a_silent_noop():
    """A machine with no output device must not break acquisition."""
    mon = AudioMonitor(output_factory=lambda: None)
    mon.set_enabled(True)

    mon.push(np.full((5, 200), -6.0))     # must not raise

    assert not mon.is_active


# ---------------------------------------------------------------------------
# Volume slider curve
# ---------------------------------------------------------------------------


def test_midpoint_volume_is_the_default_gain():
    assert gain_from_volume(50) == pytest.approx(AUDIO_DEFAULT_GAIN)


def test_volume_spans_two_decades_around_the_default():
    assert gain_from_volume(0) == pytest.approx(AUDIO_DEFAULT_GAIN / 10)
    assert gain_from_volume(100) == pytest.approx(AUDIO_DEFAULT_GAIN * 10)


def test_volume_curve_is_monotonic():
    gains = [gain_from_volume(v) for v in range(0, 101, 5)]
    assert all(b > a for a, b in zip(gains, gains[1:]))


def test_volume_is_clamped_to_the_slider_range():
    assert gain_from_volume(-20) == gain_from_volume(0)
    assert gain_from_volume(150) == gain_from_volume(100)


def test_set_gain_takes_effect_on_the_next_chunk():
    fake = FakeAudioOut()
    mon = AudioMonitor(output_factory=lambda: fake, gain=0.001)
    mon.set_enabled(True)

    chunk = np.full((5, 200), -6.0)
    chunk[0, 100:120] = 4.0
    mon.push(chunk)
    quiet_peak = np.abs(fake.samples).max()

    mon.set_gain(0.05)
    mon.push(chunk)
    loud_peak = np.abs(fake.samples[200:]).max()

    assert loud_peak > quiet_peak

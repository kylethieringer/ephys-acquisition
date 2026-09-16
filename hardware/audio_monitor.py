"""
AudioMonitor — plays the membrane channel through the speaker in real time.

This is the software equivalent of the audio monitor built into a patch
amplifier: the AI signal itself is streamed to the sound card, so spikes
are heard as sharp pops, synaptic noise as hiss, and line pickup as a
60 Hz buzz.  Nothing is detected or synthesised — you hear the recording.

Usage::

    monitor = AudioMonitor()
    monitor.set_enabled(True)
    monitor.push(chunk)          # (N_AI_CHANNELS, CHUNK_SIZE) in Volts
    monitor.set_enabled(False)

``push`` is called from the GUI thread on every AI chunk (~100 Hz), the
same callback that feeds the ring buffer.

Signal path
-----------
Row :data:`~config.AUDIO_CHANNEL_INDEX` of each chunk is high-passed at
:data:`~config.AUDIO_HIGHPASS_HZ` to strip the DC resting potential,
scaled by the current gain, clipped, converted to 16-bit, and written to
a :class:`~PySide6.QtMultimedia.QAudioSink` running at
:data:`~config.SAMPLE_RATE`.  The DAQ rate is fed to the sound card
directly, so no resampling happens.

Filter state persists across chunks.  Filtering each 200-sample chunk
independently would place a discontinuity at every boundary and add an
audible 100 Hz buzz on top of the data.

Latency
-------
The sink buffer is deliberately shallow (:data:`~config.AUDIO_BUFFER_MS`).
When it is too full to take a whole chunk, that chunk is dropped instead
of queued: for a monitor, staying in step with the cell matters more than
gapless audio.

Availability
------------
If no output device exists, or it cannot accept the DAQ sample rate, the
monitor degrades to a silent no-op — :attr:`is_active` stays ``False`` and
acquisition is unaffected.  Nothing in this module imports QtMultimedia
until audio is actually switched on.
"""

from __future__ import annotations

import math
from typing import Callable, Protocol

import numpy as np
from numpy.typing import NDArray
from scipy.signal import lfilter

from config import (
    AUDIO_BUFFER_MS,
    AUDIO_CHANNEL_INDEX,
    AUDIO_DEFAULT_GAIN,
    AUDIO_HIGHPASS_HZ,
    SAMPLE_RATE,
)

_INT16_PEAK = 32767
_BYTES_PER_SAMPLE = 2


def gain_from_volume(volume_percent: float) -> float:
    """Map a 0–100 volume slider position to a playback gain.

    The curve is logarithmic, as hearing is: the midpoint is
    :data:`~config.AUDIO_DEFAULT_GAIN` and each end is a decade away, so
    the slider spans ±20 dB.  Values outside 0–100 are clamped.

    Args:
        volume_percent: Slider position, 0–100.

    Returns:
        Gain to hand to :meth:`AudioMonitor.set_gain`.
    """
    v = min(100.0, max(0.0, float(volume_percent)))
    return AUDIO_DEFAULT_GAIN * 10.0 ** ((v - 50.0) / 50.0)


class AudioOut(Protocol):
    """Minimal sink interface :class:`AudioMonitor` writes to.

    Implemented by :class:`_QtAudioOut` in production and by a fake in the
    tests, so the DSP path can be exercised without a sound card.
    """

    def bytes_free(self) -> int:
        """Bytes the sink can accept right now without blocking."""

    def write(self, data: bytes) -> None:
        """Queue PCM bytes for playback."""

    def close(self) -> None:
        """Stop playback and release the device."""


class SpikeAudioFilter:
    """Stateful one-pole high-pass followed by int16 conversion.

    The difference equation is ``y[n] = a·(y[n-1] + x[n] - x[n-1])`` with
    ``a = exp(-2π·f_c/f_s)``, evaluated by :func:`scipy.signal.lfilter` so
    a chunk costs one vectorised call rather than a Python loop.

    On the first chunk after construction or :meth:`reset`, the input
    history is seeded from the first sample rather than from zero.  A cell
    sitting at -60 mV therefore reads as silence when the monitor is
    switched on, instead of a step edge thumping through the speaker.

    Attributes:
        gain: Volts-to-full-scale multiplier applied after filtering.
    """

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        cutoff_hz:   float = AUDIO_HIGHPASS_HZ,
        gain:        float = AUDIO_DEFAULT_GAIN,
    ) -> None:
        """Initialise the filter.

        Args:
            sample_rate: Sample rate of the incoming data in Hz.
            cutoff_hz: High-pass corner frequency in Hz.
            gain: Multiplier applied to the filtered Volts before clipping.
        """
        alpha = math.exp(-2.0 * math.pi * cutoff_hz / sample_rate)
        self._b = np.array([alpha, -alpha], dtype=np.float64)
        self._a = np.array([1.0, -alpha], dtype=np.float64)
        self._alpha = alpha
        self.gain = gain
        self._zi: NDArray[np.float64] | None = None

    def reset(self) -> None:
        """Discard filter history so the next chunk seeds a fresh baseline."""
        self._zi = None

    def process(self, samples: NDArray[np.float64]) -> NDArray[np.int16]:
        """Filter one chunk and return it as signed 16-bit PCM.

        Args:
            samples: 1-D array of raw AI samples in Volts.

        Returns:
            1-D ``int16`` array of the same length, clipped to ±32767.
        """
        if samples.size == 0:
            return np.zeros(0, dtype=np.int16)

        if self._zi is None:
            # Seed the delay line so a constant baseline maps to silence:
            # with y[0] = 0 required, z_init = -alpha * x[0].
            self._zi = np.array([-self._alpha * float(samples[0])])

        filtered, self._zi = lfilter(self._b, self._a, samples, zi=self._zi)

        scaled = np.clip(filtered * self.gain, -1.0, 1.0)
        return np.rint(scaled * _INT16_PEAK).astype(np.int16)


class AudioMonitor:
    """Routes AI chunks to the speaker, on demand.

    The output device is opened when the monitor is enabled and released
    when it is disabled, so an idle rig holds no audio handle.

    Attributes:
        _filter (SpikeAudioFilter): DSP applied to every chunk.
        _out (AudioOut | None): Open sink, or ``None`` when off/unavailable.
    """

    def __init__(
        self,
        output_factory: Callable[[], AudioOut | None] | None = None,
        gain:           float = AUDIO_DEFAULT_GAIN,
        channel_index:  int   = AUDIO_CHANNEL_INDEX,
    ) -> None:
        """Initialise a disabled monitor.

        Args:
            output_factory: Zero-argument callable returning an
                :class:`AudioOut`, or ``None`` if audio is unavailable.
                Defaults to opening a real Qt sink.  Injected by the tests.
            gain: Initial volts-to-full-scale multiplier.
            channel_index: AI row to play.
        """
        self._output_factory = output_factory or _default_output_factory
        self._channel_index  = channel_index
        self._filter         = SpikeAudioFilter(gain=gain)
        self._out: AudioOut | None = None
        self._enabled = False

    # ------------------------------------------------------------------
    # Public API (GUI thread)
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """``True`` when enabled *and* an output device was successfully opened."""
        return self._enabled and self._out is not None

    def set_enabled(self, enabled: bool) -> None:
        """Switch audio monitoring on or off.

        Enabling opens the output device and clears filter history, so the
        current resting potential is taken as the new silent baseline.
        Disabling releases the device.  Failure to open is not an error:
        the monitor simply stays inactive.

        Args:
            enabled: Desired state.
        """
        if enabled == self._enabled:
            return
        self._enabled = enabled

        if enabled:
            self._filter.reset()
            try:
                self._out = self._output_factory()
            except Exception:
                self._out = None
        else:
            self._close_output()

    def set_gain(self, gain: float) -> None:
        """Set the playback gain, effective from the next chunk.

        Args:
            gain: Volts-to-full-scale multiplier; see
                :data:`~config.AUDIO_DEFAULT_GAIN`.
        """
        self._filter.gain = gain

    def push(self, chunk: NDArray[np.float64]) -> None:
        """Play one AI chunk.  No-op unless the monitor is active.

        Args:
            chunk: ``(N_AI_CHANNELS, CHUNK_SIZE)`` float64 array in Volts.

        Note:
            Never raises: a sink that fails mid-recording is closed and the
            monitor goes quiet rather than interrupting acquisition.
        """
        if not self.is_active:
            return

        pcm = self._filter.process(chunk[self._channel_index])
        data = pcm.tobytes()

        try:
            if self._out.bytes_free() < len(data):
                return          # sink backed up — drop rather than lag
            self._out.write(data)
        except Exception:
            self._close_output()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _close_output(self) -> None:
        """Release the sink, ignoring teardown errors."""
        if self._out is not None:
            try:
                self._out.close()
            except Exception:
                pass
            self._out = None


class _QtAudioOut:
    """:class:`AudioOut` backed by :class:`~PySide6.QtMultimedia.QAudioSink`."""

    def __init__(self, sample_rate: int, buffer_ms: int) -> None:
        """Open the default output device at ``sample_rate``, mono int16.

        Args:
            sample_rate: Playback rate in Hz — the DAQ rate, unresampled.
            buffer_ms: Sink buffer depth in ms.

        Raises:
            RuntimeError: If there is no output device, or it cannot accept
                the requested format.
        """
        from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

        device = QMediaDevices.defaultAudioOutput()
        if device is None or device.isNull():
            raise RuntimeError("no audio output device")

        fmt = QAudioFormat()
        fmt.setSampleRate(sample_rate)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.Int16)
        if not device.isFormatSupported(fmt):
            raise RuntimeError(
                f"output device does not support {sample_rate} Hz mono int16"
            )

        self._sink = QAudioSink(device, fmt)
        self._sink.setBufferSize(
            int(sample_rate * buffer_ms / 1000) * _BYTES_PER_SAMPLE
        )
        self._io = self._sink.start()
        if self._io is None:
            raise RuntimeError("could not start audio sink")

    def bytes_free(self) -> int:
        return int(self._sink.bytesFree())

    def write(self, data: bytes) -> None:
        self._io.write(data)

    def close(self) -> None:
        self._sink.stop()


def _default_output_factory() -> AudioOut | None:
    """Open a real Qt audio sink, or return ``None`` if audio is unavailable."""
    try:
        return _QtAudioOut(SAMPLE_RATE, AUDIO_BUFFER_MS)
    except Exception:
        return None

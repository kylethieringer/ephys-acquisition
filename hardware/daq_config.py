"""
nidaqmx task builders for the NI PCIe-6323 DAQ card.

This module contains pure constructor functions — no state is kept here.
Each function creates and configures a nidaqmx Task object but does **not**
start it; the caller is responsible for starting and closing tasks.

Hardware clock relationships
-----------------------------
The three task types have the following clock topology:

- **AI task** (``build_ai_task``): the clock *master*.  Its 20 kHz sample
  clock is routed internally to the AO task via
  ``/{DEVICE_NAME}/ai/SampleClock``.  Start AI last; stop AI last.

- **AO task** (``build_ao_task``): a *slave* of the AI sample clock.
  AO output is phase-locked to AI acquisition, so command current and
  recorded data stay time-aligned.  Start AO before AI (AO waits for the
  first AI clock edge); stop AO before AI.

- **Counter task** (``build_ttl_counter_task``): **independent** of the
  AI/AO clock.  Uses the device's own 100 MHz timebase.  Starting or
  stopping the counter never disrupts AI/AO timing.  Rebuilt when TTL
  parameters change without affecting AI or AO.

Shutdown order (correct): CTR → AO → AI.

Developer notes
---------------
``HAS_NIDAQMX`` is ``True`` only when nidaqmx is importable (i.e. on the
acquisition PC with the NI driver installed).  All type annotations for
nidaqmx objects use string forward references so this module is importable
in environments without the library (e.g. documentation builds, unit tests).
"""

from __future__ import annotations

try:
    import nidaqmx
    from nidaqmx.constants import (
        AcquisitionType,
        Level,
        RegenerationMode,
        TerminalConfiguration,
    )
    from nidaqmx.stream_readers import AnalogMultiChannelReader
    from nidaqmx.stream_writers import AnalogMultiChannelWriter
    HAS_NIDAQMX = True
except ImportError:
    HAS_NIDAQMX = False

import numpy as np
from config import (
    AI_CHANNELS,
    AO_COMMAND_CH,
    CTR_CHANNEL,
    CTR_OUT_TERMINAL,
    CHUNK_SIZE,
    DEVICE_NAME,
    SAMPLE_RATE,
)

_TERM_MAP: dict[str, object] = {
    "differential": TerminalConfiguration.DIFF if HAS_NIDAQMX else None,
    "rse":          TerminalConfiguration.RSE  if HAS_NIDAQMX else None,
}
"""Mapping from config terminal-config strings to nidaqmx ``TerminalConfiguration`` enums."""


def build_ai_task() -> "nidaqmx.Task":
    """Build and return a configured continuous analog input task.

    Creates one channel per entry in :data:`~config.AI_CHANNELS` with the
    configured terminal configuration (differential or RSE) and a ±10 V
    input range.  The task is configured for continuous acquisition at
    :data:`~config.SAMPLE_RATE` with an onboard buffer of
    ``10 × CHUNK_SIZE`` samples.

    The AI task acts as the **master clock** for the whole system.
    Start it *last* (after the AO task) so that the AO clock slave is
    already waiting when the first AI clock edge fires.

    Returns:
        A configured but not-yet-started ``nidaqmx.Task``.
        Channel order matches :data:`~config.AI_CHANNELS`, so
        ``data[i]`` corresponds to ``AI_CHANNELS[i]``.

    Raises:
        RuntimeError: Propagated from nidaqmx if the device is not found
            or the channel configuration is invalid.
    """
    task = nidaqmx.Task("ai_task")
    for name, ch, term_cfg, scale, units in AI_CHANNELS:
        task.ai_channels.add_ai_voltage_chan(
            f"{DEVICE_NAME}/{ch}",
            terminal_config=_TERM_MAP[term_cfg],
            min_val=-10.0,
            max_val=10.0,
        )
    task.timing.cfg_samp_clk_timing(
        rate=SAMPLE_RATE,
        sample_mode=AcquisitionType.CONTINUOUS,
        samps_per_chan=CHUNK_SIZE * 10,   # onboard buffer = 10× chunk
    )
    return task


def build_ao_task(n_waveform_samples: int) -> "nidaqmx.Task":
    """Build and return a configured continuous analog output task for ao0.

    The AO task drives the amplifier command channel (ao0 = command current
    in CC mode, command voltage in VC mode).  It is clocked from the AI
    sample clock so the command waveform is phase-locked to acquired data.

    ``ALLOW_REGENERATION`` mode causes the NI board to loop the waveform
    continuously from its onboard FIFO without CPU intervention, which
    keeps the command signal glitch-free during Python GIL pauses.

    When the stimulus waveform changes, the task must be rebuilt (see
    :meth:`~hardware.daq_worker.DAQWorker._rebuild_ao`).

    Args:
        n_waveform_samples: Length of the waveform that will be written to
            the task after construction.  Used to size the onboard FIFO
            buffer (minimum: ``max(n_waveform_samples, CHUNK_SIZE × 4)``).

    Returns:
        A configured but not-yet-started ``nidaqmx.Task``.

    Note:
        Set ``task.out_stream.auto_start = False`` before writing the
        waveform so the task waits for an explicit ``start()`` call (which
        waits for the AI clock edge).
    """
    task = nidaqmx.Task("ao_task")
    task.ao_channels.add_ao_voltage_chan(
        f"{DEVICE_NAME}/{AO_COMMAND_CH}",
        min_val=-10.0,
        max_val=10.0,
    )
    task.timing.cfg_samp_clk_timing(
        rate=SAMPLE_RATE,
        source=f"/{DEVICE_NAME}/ai/SampleClock",
        sample_mode=AcquisitionType.CONTINUOUS,
        samps_per_chan=max(n_waveform_samples, CHUNK_SIZE * 4),
    )
    task.out_stream.regen_mode = RegenerationMode.ALLOW_REGENERATION
    return task


def build_ttl_counter_task(
    frame_rate_hz: float,
    exposure_ms: float,
) -> "nidaqmx.Task":
    """Build and return a configured continuous counter output task for camera TTL.

    Generates a square wave on CTR0 (physical output: PFI12) at
    ``frame_rate_hz`` with a duty cycle matching ``exposure_ms``.

    The counter uses the device's own 100 MHz timebase, so it runs
    **independently** of the AI/AO sample clock.  Starting, stopping, or
    rebuilding the AO task never disrupts the TTL timing.

    Args:
        frame_rate_hz: Camera frame rate in Hz.  Determines the square-wave
            period (``1000 / frame_rate_hz`` ms).
        exposure_ms: Camera exposure duration in ms.  Used to compute the
            duty cycle (``exposure_ms / period_ms``), clamped to [0.01, 0.99].

    Returns:
        A configured but not-yet-started ``nidaqmx.Task``.
        Call ``task.start()`` to begin generating TTL pulses.
    """
    period_ms  = 1000.0 / frame_rate_hz
    duty_cycle = min(max(exposure_ms / period_ms, 0.01), 0.99)

    task = nidaqmx.Task("ttl_ctr_task")
    chan = task.co_channels.add_co_pulse_chan_freq(
        f"{DEVICE_NAME}/{CTR_CHANNEL}",
        freq=frame_rate_hz,
        duty_cycle=duty_cycle,
        idle_state=Level.LOW,
        initial_delay=0.0,
    )
    chan.co_pulse_term = f"/{DEVICE_NAME}/{CTR_OUT_TERMINAL}"
    task.timing.cfg_implicit_timing(sample_mode=AcquisitionType.CONTINUOUS)
    return task


def make_reader(task: "nidaqmx.Task") -> "AnalogMultiChannelReader":
    """Create a buffered multi-channel reader for an AI task.

    Args:
        task: A configured and started AI ``nidaqmx.Task``.

    Returns:
        An ``AnalogMultiChannelReader`` bound to ``task.in_stream``.
        Use ``reader.read_many_sample(buffer, n_samples, timeout)`` in the
        acquisition loop.
    """
    return AnalogMultiChannelReader(task.in_stream)


def make_writer(task: "nidaqmx.Task") -> "AnalogMultiChannelWriter":
    """Create a buffered multi-channel writer for an AO task.

    Args:
        task: A configured AO ``nidaqmx.Task`` (need not be started yet).

    Returns:
        An ``AnalogMultiChannelWriter`` bound to ``task.out_stream``.
        Use ``writer.write_many_sample(waveform)`` to load the waveform
        before starting the task.
    """
    return AnalogMultiChannelWriter(task.out_stream)

"""
nidaqmx Task builders for AI and AO.

AI task:  5 channels (ai0-ai4), differential except ai3 (RSE)
AO task:  2 channels (ao0=command current, ao1=TTL), clocked from AI sample clock
          so they are phase-locked.
"""

try:
    import nidaqmx
    from nidaqmx.constants import (
        AcquisitionType,
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
    AO_TTL_CH,
    CHUNK_SIZE,
    DEVICE_NAME,
    SAMPLE_RATE,
)

_TERM_MAP = {
    "differential": TerminalConfiguration.DIFF if HAS_NIDAQMX else None,
    "rse":          TerminalConfiguration.RSE  if HAS_NIDAQMX else None,
}


def build_ai_task() -> "nidaqmx.Task":
    """
    Build and return a configured (but not started) continuous AI task.

    Channel order matches AI_CHANNELS config, so data[i] == AI_CHANNELS[i].
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
    """
    Build and return a configured (but not started) continuous AO task.

    The AO sample clock is sourced from the AI sample clock so both
    tasks are phase-locked.  ALLOW_REGENERATION means the board loops
    the written waveform without CPU intervention.

    n_waveform_samples: total samples of the waveform that will be written.
    """
    task = nidaqmx.Task("ao_task")
    task.ao_channels.add_ao_voltage_chan(
        f"{DEVICE_NAME}/{AO_COMMAND_CH}",
        min_val=-10.0,
        max_val=10.0,
    )
    task.ao_channels.add_ao_voltage_chan(
        f"{DEVICE_NAME}/{AO_TTL_CH}",
        min_val=0.0,
        max_val=5.0,
    )
    task.timing.cfg_samp_clk_timing(
        rate=SAMPLE_RATE,
        source=f"/{DEVICE_NAME}/ai/SampleClock",
        sample_mode=AcquisitionType.CONTINUOUS,
        samps_per_chan=max(n_waveform_samples, CHUNK_SIZE * 4),
    )
    task.out_stream.regen_mode = RegenerationMode.ALLOW_REGENERATION
    return task


def make_reader(task: "nidaqmx.Task") -> "AnalogMultiChannelReader":
    return AnalogMultiChannelReader(task.in_stream)


def make_writer(task: "nidaqmx.Task") -> "AnalogMultiChannelWriter":
    return AnalogMultiChannelWriter(task.out_stream)

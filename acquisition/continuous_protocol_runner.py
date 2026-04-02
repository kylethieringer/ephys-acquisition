"""
ContinuousProtocolRunner — drives a TrialProtocol as a flat timeline of
stimulus events within a single unbroken continuous recording.

Unlike trial-based mode (which saves per-trial HDF5 groups), this runner
keeps the recording continuous and logs the sample-accurate start and stop
of each stimulus block into the HDF5 ``/stimulus_events/`` dataset.  The
resulting file can be sliced into pseudo-trials in post-processing.

Timeline structure
------------------
From the protocol the runner builds a list of ``(sample_offset, action,
waveform, stimulus_name, stimulus_index)`` events, where ``sample_offset``
is relative to the recording start sample.  Events fire when
``n_saved >= recording_start + event.sample_offset``.

Each stimulus block produces two events:

- ``"apply"`` — send the AO waveform to the DAQ worker.
- ``"clear"`` — revert ao0 to 0 V.

The inter-trial interval (ITI) is a silent gap between blocks.

Usage
-----
Instantiate with a protocol and clamp mode, call :meth:`start` once the
HDF5 recording is open, call :meth:`advance` in every ``_on_ai_chunk``
call, check :meth:`is_done` to know when to stop recording.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from config import SAMPLE_RATE
from acquisition.trial_protocol import TrialProtocol, build_trial_order
from acquisition.trial_waveforms import build_trial_waveform


@dataclass
class _Event:
    """One action in the protocol timeline."""
    sample_offset: int
    action:        str                           # "apply" or "clear"
    waveform:      NDArray[np.float64] | None
    stim_name:     str
    stim_idx:      int


class ContinuousProtocolRunner:
    """Drives a :class:`~acquisition.trial_protocol.TrialProtocol` as a
    flat event timeline within a continuous recording.

    Attributes:
        _events (list[_Event]): Sorted list of timeline events.
        _next_event_idx (int): Index of the next unfired event.
        _recording_start (int): ``n_saved`` value when :meth:`start` was called.
        _done (bool): ``True`` after all events have fired.
    """

    def __init__(self, protocol: TrialProtocol) -> None:
        """Build the event timeline from the protocol.

        Args:
            protocol: The :class:`~acquisition.trial_protocol.TrialProtocol`
                to run.  The trial order is randomised once here.
        """
        self._events:          list[_Event] = []
        self._next_event_idx:  int          = 0
        self._recording_start: int          = 0
        self._done:            bool         = False

        self._build_timeline(protocol)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, recording_start_sample: int) -> None:
        """Anchor the timeline to the current recording position.

        Args:
            recording_start_sample: The ``n_saved`` value at the moment the
                recording started (i.e. when this runner should begin).
                Typically 0 if the recording was just opened, or the current
                ``n_saved`` if the runner is started mid-recording.
        """
        self._recording_start = recording_start_sample
        self._next_event_idx  = 0
        self._done            = len(self._events) == 0

    def advance(self, n_saved: int) -> list[_Event]:
        """Check for and return any events that have fired at ``n_saved``.

        Should be called on every AI chunk arrival.  Returns fired events
        in chronological order so the caller can apply/clear waveforms and
        log them to HDF5.

        Args:
            n_saved: Current ``n_saved`` value from the saver.

        Returns:
            List of :class:`_Event` objects whose ``sample_offset`` has
            been reached.  Empty if no events fired yet.
        """
        if self._done:
            return []

        fired: list[_Event] = []
        while self._next_event_idx < len(self._events):
            ev = self._events[self._next_event_idx]
            if n_saved >= self._recording_start + ev.sample_offset:
                fired.append(ev)
                self._next_event_idx += 1
            else:
                break

        if self._next_event_idx >= len(self._events):
            self._done = True

        return fired

    def is_done(self) -> bool:
        """``True`` once all scheduled events have fired."""
        return self._done

    @property
    def total_samples(self) -> int:
        """Total number of samples spanned by the protocol timeline."""
        if not self._events:
            return 0
        return self._events[-1].sample_offset

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_timeline(self, protocol: TrialProtocol) -> None:
        """Construct the flat event list from the protocol."""
        trial_order = build_trial_order(protocol)
        cursor      = _ms_to_samples(protocol.iti_ms)  # start with one ITI

        for trial_pos, stim_idx in enumerate(trial_order):
            stim_def = protocol.stimuli[stim_idx]

            if stim_def.type != "baseline":
                waveform = build_trial_waveform(stim_def, protocol)
            else:
                waveform = None

            # Silent pre-baseline window before apply
            apply_offset = cursor + _ms_to_samples(protocol.pre_ms)
            # Clear fires after stim + post window ends
            stim_duration = len(waveform) if waveform is not None else 0
            clear_offset  = apply_offset + stim_duration + _ms_to_samples(protocol.post_ms)

            self._events.append(_Event(
                sample_offset = apply_offset,
                action        = "apply",
                waveform      = waveform,
                stim_name     = stim_def.name,
                stim_idx      = stim_idx,
            ))
            self._events.append(_Event(
                sample_offset = clear_offset,
                action        = "clear",
                waveform      = None,
                stim_name     = stim_def.name,
                stim_idx      = stim_idx,
            ))

            cursor = clear_offset + _ms_to_samples(protocol.iti_ms)


def _ms_to_samples(ms: float) -> int:
    return max(0, int(ms / 1000.0 * SAMPLE_RATE))

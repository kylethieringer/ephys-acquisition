"""
Thread-safe ring buffer for AI data.

push() and read_contiguous() are both called from the GUI thread
(Qt AutoConnection routes cross-thread signals to the receiver's thread),
so no locking is required as long as DirectConnection is never used.
"""

import numpy as np
from config import N_AI_CHANNELS, DISPLAY_SAMPLES


class RingBuffer:
    """
    Circular numpy buffer for multi-channel analog data.

    Shape: (n_channels, capacity_samples)
    Oldest data is overwritten as new chunks arrive.
    """

    def __init__(self, n_channels: int = N_AI_CHANNELS, capacity: int = DISPLAY_SAMPLES):
        self.n_channels = n_channels
        self.capacity   = capacity
        self._data      = np.zeros((n_channels, capacity), dtype=np.float64)
        self._ptr       = 0   # next write position

    def push(self, chunk: np.ndarray) -> None:
        """
        Append chunk to the ring.  chunk shape: (n_channels, n_samples).
        Wraps around if n_samples > remaining space.
        """
        n = chunk.shape[1]
        end = self._ptr + n

        if end <= self.capacity:
            self._data[:, self._ptr:end] = chunk
        else:
            first  = self.capacity - self._ptr
            second = n - first
            self._data[:, self._ptr:]   = chunk[:, :first]
            self._data[:, :second]      = chunk[:, first:]

        self._ptr = end % self.capacity

    def read_contiguous(self, n_samples: int | None = None) -> np.ndarray:
        """
        Return the last n_samples in chronological order.
        Returns a copy; safe to use from the GUI thread while the buffer is live.
        """
        if n_samples is None:
            n_samples = self.capacity

        n_samples = min(n_samples, self.capacity)

        if self._ptr >= n_samples:
            return self._data[:, self._ptr - n_samples : self._ptr].copy()
        else:
            # Data straddles the wrap point
            tail_len = n_samples - self._ptr
            result   = np.empty((self.n_channels, n_samples), dtype=np.float64)
            result[:, :tail_len] = self._data[:, self.capacity - tail_len :]
            result[:, tail_len:] = self._data[:, : self._ptr]
            return result

    def reset(self) -> None:
        self._data[:] = 0.0
        self._ptr     = 0

"""
HDF5 data saver for continuous AI acquisition.

File layout:
    /metadata/
        sample_rate      (scalar)
        start_time       (string ISO-8601)
        channel_names    (string array)
        display_scales   (float array)
        units            (string array)
    /data/
        analog_input     (float64 dataset, shape=(n_channels, n_samples), extendable)
"""

import datetime
import numpy as np
from pathlib import Path

try:
    import h5py
    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False

from config import AI_CHANNELS, SAMPLE_RATE, N_AI_CHANNELS


class HDF5Saver:
    """
    Appends AI chunks to a growing HDF5 dataset.
    All methods are called from the GUI thread (via Qt signals).
    """

    CHUNK_COLS = SAMPLE_RATE  # HDF5 internal chunk = 1 second of data

    def __init__(self) -> None:
        self._file    = None
        self._dataset = None
        self._n_saved = 0
        self._path:   Path | None = None
        self._folder: Path | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open(self, save_dir: str | Path, prefix: str = "ephys") -> Path:
        """
        Open a new HDF5 file.  Returns the full path that was created.
        Raises RuntimeError if h5py is not installed.
        """
        if not HAS_H5PY:
            raise RuntimeError("h5py is not installed. Run: pip install h5py")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"{prefix}_{timestamp}"
        self._folder = Path(save_dir) / stem
        self._folder.mkdir(parents=True, exist_ok=True)
        self._path = self._folder / f"{stem}.h5"

        self._file = h5py.File(self._path, "w")
        self._write_metadata()
        self._create_dataset()
        self._n_saved = 0
        return self._path

    def append(self, chunk: np.ndarray) -> None:
        """
        Append a chunk of shape (n_channels, n_samples) to the dataset.
        No-op if file is not open.
        """
        if self._dataset is None:
            return
        n = chunk.shape[1]
        new_size = self._n_saved + n
        self._dataset.resize(new_size, axis=1)
        self._dataset[:, self._n_saved : new_size] = chunk
        self._n_saved = new_size

    def close(self) -> None:
        """Flush and close the HDF5 file."""
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception:
                pass
            finally:
                self._file    = None
                self._dataset = None

    @property
    def folder(self) -> Path | None:
        return self._folder

    @property
    def is_open(self) -> bool:
        return self._file is not None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def n_saved(self) -> int:
        return self._n_saved

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _write_metadata(self) -> None:
        grp = self._file.create_group("metadata")
        grp.attrs["sample_rate"]  = SAMPLE_RATE
        grp.attrs["start_time"]   = datetime.datetime.now().isoformat()
        dt_str = h5py.string_dtype()
        names  = [ch[0] for ch in AI_CHANNELS]
        scales = [ch[3] for ch in AI_CHANNELS]
        units  = [ch[4] for ch in AI_CHANNELS]
        grp.create_dataset("channel_names",  data=np.array(names,  dtype=object), dtype=dt_str)
        grp.create_dataset("display_scales", data=np.array(scales, dtype=np.float64))
        grp.create_dataset("units",          data=np.array(units,  dtype=object), dtype=dt_str)

    def _create_dataset(self) -> None:
        data_grp = self._file.create_group("data")
        self._dataset = data_grp.create_dataset(
            "analog_input",
            shape=(N_AI_CHANNELS, 0),
            maxshape=(N_AI_CHANNELS, None),
            dtype=np.float64,
            chunks=(N_AI_CHANNELS, self.CHUNK_COLS),
            compression="lzf",
        )

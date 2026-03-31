"""
HDF5 data saver for continuous AI acquisition.

Writes a single growing dataset that is extended by one
:data:`~config.CHUNK_SIZE`-sample chunk on every call to :meth:`HDF5Saver.append`.

HDF5 file layout
-----------------
::

    /metadata/
        sample_rate      (scalar attribute, int)
        start_time       (scalar attribute, ISO-8601 string)
        channel_names    (string dataset, shape (N_AI_CHANNELS,))
        display_scales   (float64 dataset, shape (N_AI_CHANNELS,))
        units            (string dataset, shape (N_AI_CHANNELS,))
    /subject/            (attributes: expt_id, genotype, age, sex, targeted_cell_type)
    /data/
        analog_input     (float64 dataset, shape (N_AI_CHANNELS, N_samples),
                          extendable on axis 1, chunked 1-second blocks, LZF compression)

File naming
-----------
``{save_dir}/{expt_id}/{expt_id}_{genotype}_{YYYYMMDD}.h5``

Developer notes
---------------
All public methods are called from the GUI thread via Qt signals.
LZF compression is used (rather than GZIP) because it has negligible
CPU overhead and is natively supported by h5py without additional libraries.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

try:
    import h5py
    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False

from config import AI_CHANNELS, SAMPLE_RATE, N_AI_CHANNELS


class HDF5Saver:
    """Append-only HDF5 writer for continuous analog input data.

    Opens one file per recording session and extends the dataset as chunks
    arrive.  Unlike :class:`~acquisition.trial_saver.TrialHDF5Saver`, no
    pre-allocation is performed — the dataset grows dynamically.

    All methods must be called from the GUI thread.

    Attributes:
        CHUNK_COLS (int): HDF5 internal chunk size in columns (samples).
            Set to :data:`~config.SAMPLE_RATE` (1 second of data per chunk),
            which balances read performance and compression ratio.
    """

    CHUNK_COLS: int = SAMPLE_RATE  # HDF5 internal chunk = 1 second of data

    def __init__(self) -> None:
        self._file:    "h5py.File | None"    = None
        self._dataset: "h5py.Dataset | None" = None
        self._n_saved: int                   = 0
        self._path:    Path | None           = None
        self._folder:  Path | None           = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open(
        self,
        save_dir: str | Path,
        subject_metadata: dict | None = None,
    ) -> Path:
        """Create a new HDF5 file and write the metadata header.

        The experiment folder is created automatically if it does not exist.
        The file is overwritten if a file with the same name already exists
        (same experiment ID + genotype + date).

        Args:
            save_dir: Root directory under which the experiment subfolder
                will be created.
            subject_metadata: Optional dict with subject information.
                Recognised keys: ``"expt_id"``, ``"genotype"``, ``"age"``,
                ``"sex"``, ``"targeted_cell_type"``.  Any missing keys fall
                back to empty strings or ``"ephys"`` for ``expt_id``.

        Returns:
            Full path to the created HDF5 file.

        Raises:
            RuntimeError: If h5py is not installed.
        """
        if not HAS_H5PY:
            raise RuntimeError("h5py is not installed. Run: pip install h5py")

        if subject_metadata is None:
            subject_metadata = {}

        expt_id  = subject_metadata.get("expt_id", "ephys") or "ephys"
        genotype = subject_metadata.get("genotype", "") or "unknown"
        # Sanitise for filesystem
        safe_genotype = genotype.replace(" ", "_").replace("/", "-")
        datestamp = datetime.datetime.now().strftime("%Y%m%d")
        stem = f"{expt_id}_{safe_genotype}_{datestamp}"

        self._folder = Path(save_dir) / expt_id
        self._folder.mkdir(parents=True, exist_ok=True)
        self._path = self._folder / f"{stem}.h5"

        self._file = h5py.File(self._path, "w")
        self._write_metadata(subject_metadata)
        self._create_dataset()
        self._n_saved = 0
        return self._path

    def append(self, chunk: NDArray[np.float64]) -> None:
        """Append a chunk of AI data to the growing HDF5 dataset.

        Extends the dataset along the sample axis (axis 1) by
        ``chunk.shape[1]`` columns.

        Args:
            chunk: 2-D float64 array of shape ``(N_AI_CHANNELS, n_samples)``
                in raw Volts.  No-op if the file is not open.
        """
        if self._dataset is None:
            return
        n = chunk.shape[1]
        new_size = self._n_saved + n
        self._dataset.resize(new_size, axis=1)
        self._dataset[:, self._n_saved : new_size] = chunk
        self._n_saved = new_size

    def close(self) -> None:
        """Flush and close the HDF5 file.

        Safe to call even if the file is already closed — silently no-ops.
        Exceptions from h5py are suppressed.
        """
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
        """Directory containing the HDF5 file, or ``None`` if not open."""
        return self._folder

    @property
    def is_open(self) -> bool:
        """``True`` if the HDF5 file is currently open."""
        return self._file is not None

    @property
    def path(self) -> Path | None:
        """Full path to the HDF5 file, or ``None`` if not yet opened."""
        return self._path

    @property
    def n_saved(self) -> int:
        """Total number of samples written since the last :meth:`open` call."""
        return self._n_saved

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _write_metadata(self, subject_metadata: dict | None = None) -> None:
        """Write the ``/metadata`` and ``/subject`` groups to the open file.

        Args:
            subject_metadata: Subject information dict.  Keys written as
                string attributes under ``/subject/``.
        """
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

        if subject_metadata:
            subj_grp = self._file.create_group("subject")
            for key, value in subject_metadata.items():
                subj_grp.attrs[key] = str(value) if value else ""

    def _create_dataset(self) -> None:
        """Create the extendable ``/data/analog_input`` dataset.

        Initial shape is ``(N_AI_CHANNELS, 0)``; the sample axis is
        unlimited so :meth:`append` can resize it as data arrives.
        LZF compression is used for low-overhead lossless compression.
        """
        data_grp = self._file.create_group("data")
        self._dataset = data_grp.create_dataset(
            "analog_input",
            shape=(N_AI_CHANNELS, 0),
            maxshape=(N_AI_CHANNELS, None),
            dtype=np.float64,
            chunks=(N_AI_CHANNELS, self.CHUNK_COLS),
            compression="lzf",
        )

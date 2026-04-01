"""
Continuous-mode data saver.

During recording, raw AI data is appended to a flat binary (``.bin``) file
for minimal write overhead.  When recording stops, a background
:class:`BinToHDF5Worker` thread converts the binary file to the final HDF5
format.  The binary file is always preserved as a raw-data backup.

Binary file format
------------------
Consecutive float64 chunks written in C order.  Each chunk has shape
``(N_channels, CHUNK_SIZE)`` and is written as raw bytes.  The full
recording is recoverable as::

    raw  = np.fromfile(path, dtype=np.float64)
    data = raw.reshape(N_channels, -1)

HDF5 file layout
-----------------
::

    /metadata/
        sample_rate      (scalar attribute, int)
        start_time       (scalar attribute, ISO-8601 string)
        channel_names    (string dataset, shape (N_channels,))
        display_scales   (float64 dataset, shape (N_channels,))
        units            (string dataset, shape (N_channels,))
    /subject/            (attributes: expt_id, genotype, age, sex, targeted_cell_type)
    /data/
        analog_input     (float64, shape (N_channels, N_samples),
                          chunked 1-second blocks, LZF compressed)
    /stimulus_events/    (optional — written when events are logged)
        sample_index     (int64 dataset)
        event_type       (string dataset)
        stimulus_name    (string dataset)
        stimulus_index   (int32 dataset)

File naming
-----------
``{save_dir}/{expt_id}/{expt_id}_{genotype}_{YYYYMMDD}.h5``
``{save_dir}/{expt_id}/{expt_id}_{genotype}_{YYYYMMDD}.bin``

Developer notes
---------------
All public methods are called from the GUI thread.
:meth:`ContinuousSaver.close` returns a :class:`BinToHDF5Worker`.
The caller must connect signals and call ``worker.start()``.
The ``.bin`` file is always kept as a raw-data backup.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QThread, Signal

try:
    import h5py
    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False

from config import AI_CHANNELS, SAMPLE_RATE


# ---------------------------------------------------------------------------
# Background conversion worker
# ---------------------------------------------------------------------------

class BinToHDF5Worker(QThread):
    """Background thread that converts a raw binary recording to HDF5.

    Reads the ``.bin`` file in :data:`~config.SAMPLE_RATE`-wide column
    chunks to keep memory usage bounded.  The binary file is always
    preserved as a raw-data backup.

    Signals:
        conversion_done(str): Emitted with the HDF5 file path string on
            successful conversion.
        conversion_failed(str): Emitted with an error message string if
            conversion raises an exception.
    """

    conversion_done   = Signal(str)   # HDF5 file path
    conversion_failed = Signal(str)   # error message

    def __init__(
        self,
        bin_path: Path,
        h5_path:  Path,
        header:   dict,
        stimulus_events: list | None = None,
        parent=None,
    ) -> None:
        """
        Args:
            bin_path: Path to the source binary file.
            h5_path: Path where the HDF5 file will be written.
            header: Metadata dict produced by :meth:`ContinuousSaver.open`.
            stimulus_events: Optional list of stimulus event dicts (from
                :meth:`ContinuousSaver.log_event`) to embed under
                ``/stimulus_events/``.
        """
        super().__init__(parent)
        self._bin_path        = bin_path
        self._h5_path         = h5_path
        self._header          = header
        self._stimulus_events = stimulus_events or []

    def run(self) -> None:
        """Convert the binary file to HDF5."""
        try:
            self._convert()
            self.conversion_done.emit(str(self._h5_path))
        except Exception as exc:
            self.conversion_failed.emit(str(exc))

    def _convert(self) -> None:
        import h5py as _h5py

        header   = self._header
        n_ch     = header["n_channels"]
        bin_size = self._bin_path.stat().st_size
        n_samp   = bin_size // (n_ch * 8)

        chunk_cols = min(SAMPLE_RATE, max(1, n_samp))

        with open(self._bin_path, "rb") as bf, _h5py.File(self._h5_path, "w") as hf:
            meta = hf.create_group("metadata")
            meta.attrs["sample_rate"] = header["sample_rate"]
            meta.attrs["start_time"]  = header["start_time"]

            dt_str = _h5py.string_dtype()
            meta.create_dataset(
                "channel_names",
                data=np.array(header["channel_names"], dtype=object),
                dtype=dt_str,
            )
            meta.create_dataset(
                "display_scales",
                data=np.array(header["display_scales"], dtype=np.float64),
            )
            meta.create_dataset(
                "units",
                data=np.array(header["units"], dtype=object),
                dtype=dt_str,
            )

            subj = hf.create_group("subject")
            for k, v in header.get("subject", {}).items():
                subj.attrs[k] = str(v) if v else ""

            data_grp = hf.create_group("data")
            ds = data_grp.create_dataset(
                "analog_input",
                shape=(n_ch, n_samp),
                dtype=np.float64,
                chunks=(n_ch, chunk_cols),
                compression="lzf",
            )

            offset          = 0
            bytes_per_chunk = n_ch * SAMPLE_RATE * 8
            while True:
                raw = bf.read(bytes_per_chunk)
                if not raw:
                    break
                arr  = np.frombuffer(raw, dtype=np.float64)
                cols = len(arr) // n_ch
                if cols == 0:
                    break
                chunk_data = arr[: cols * n_ch].reshape(n_ch, cols)
                ds[:, offset : offset + cols] = chunk_data
                offset += cols

            if self._stimulus_events:
                ev = hf.create_group("stimulus_events")
                ev.create_dataset(
                    "sample_index",
                    data=np.array(
                        [e["sample_index"] for e in self._stimulus_events],
                        dtype=np.int64,
                    ),
                )
                ev.create_dataset(
                    "event_type",
                    data=np.array(
                        [e["event_type"] for e in self._stimulus_events],
                        dtype=object,
                    ),
                    dtype=dt_str,
                )
                ev.create_dataset(
                    "stimulus_name",
                    data=np.array(
                        [e["stimulus_name"] for e in self._stimulus_events],
                        dtype=object,
                    ),
                    dtype=dt_str,
                )
                ev.create_dataset(
                    "stimulus_index",
                    data=np.array(
                        [e["stimulus_index"] for e in self._stimulus_events],
                        dtype=np.int32,
                    ),
                )


# ---------------------------------------------------------------------------
# Saver
# ---------------------------------------------------------------------------

class ContinuousSaver:
    """Binary-backed recorder for continuous AI data, converted to HDF5 on close.

    During recording, raw float64 data is appended to a ``.bin`` file for
    minimal I/O overhead.  :meth:`close` returns a :class:`BinToHDF5Worker`
    that the caller starts after connecting its signals.  The ``.bin`` file
    is always preserved as a raw-data backup.

    Attributes:
        CHUNK_COLS (int): HDF5 internal chunk width in samples (1 second).
    """

    CHUNK_COLS: int = SAMPLE_RATE

    def __init__(self) -> None:
        self._bin_file:  object        = None
        self._bin_path:  Path | None   = None
        self._h5_path:   Path | None   = None
        self._folder:    Path | None   = None
        self._header:    dict | None   = None
        self._n_saved:   int           = 0
        self._stimulus_events: list    = []
        self._conversion_worker: "BinToHDF5Worker | None" = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open(
        self,
        save_dir: str | Path,
        subject_metadata: dict | None = None,
        channel_defs=None,
    ) -> Path:
        """Open a new binary recording file and store HDF5 metadata.

        Args:
            save_dir: Root directory for saving.  An experiment subfolder
                is created automatically.
            subject_metadata: Optional dict with subject information.
                Recognised keys: ``"expt_id"``, ``"genotype"``, ``"age"``,
                ``"sex"``, ``"targeted_cell_type"``.
            channel_defs: List of :data:`~config.ChannelDef` tuples
                describing the active AI channels.  Defaults to
                :data:`~config.AI_CHANNELS` (current-clamp mode).

        Returns:
            Intended path of the HDF5 file (not yet written; created when
            the :class:`BinToHDF5Worker` completes).

        Raises:
            RuntimeError: If h5py is not installed.
        """
        if not HAS_H5PY:
            raise RuntimeError("h5py is not installed. Run: pip install h5py")

        if subject_metadata is None:
            subject_metadata = {}
        if channel_defs is None:
            channel_defs = AI_CHANNELS

        expt_id       = subject_metadata.get("expt_id", "ephys") or "ephys"
        genotype      = subject_metadata.get("genotype", "") or "unknown"
        safe_genotype = genotype.replace(" ", "_").replace("/", "-")
        datestamp     = datetime.datetime.now().strftime("%Y%m%d")
        stem          = f"{expt_id}_{safe_genotype}_{datestamp}"

        self._folder   = Path(save_dir) / expt_id
        self._folder.mkdir(parents=True, exist_ok=True)
        self._h5_path  = self._folder / f"{stem}.h5"
        self._bin_path = self._folder / f"{stem}.bin"

        self._header = {
            "sample_rate":    SAMPLE_RATE,
            "start_time":     datetime.datetime.now().isoformat(),
            "n_channels":     len(channel_defs),
            "channel_names":  [ch[0] for ch in channel_defs],
            "display_scales": [float(ch[3]) for ch in channel_defs],
            "units":          [ch[4] for ch in channel_defs],
            "subject": {
                k: (str(v) if v else "") for k, v in subject_metadata.items()
            },
        }

        self._bin_file          = open(self._bin_path, "wb")
        self._n_saved           = 0
        self._stimulus_events   = []
        self._conversion_worker = None
        return self._h5_path

    def append(self, chunk: NDArray[np.float64]) -> None:
        """Append a data chunk to the binary file.

        Args:
            chunk: ``(N_channels, n_samples)`` float64 array in Volts.
                No-op if the file is not open.
        """
        if self._bin_file is None:
            return
        n = chunk.shape[1]
        self._bin_file.write(chunk.astype(np.float64).tobytes())
        self._n_saved += n

    def log_event(
        self,
        sample_idx: int,
        event_type: str,
        stim_name: str,
        stim_idx: int,
    ) -> None:
        """Record a stimulus event to be embedded in the HDF5 file.

        Events are collected in memory and written to ``/stimulus_events/``
        during HDF5 conversion.

        Args:
            sample_idx: Absolute sample index within the recording when
                the event occurred.
            event_type: ``"apply"`` when a waveform is applied,
                ``"clear"`` when it is removed.
            stim_name: Human-readable stimulus name.
            stim_idx: 0-based index into the protocol stimuli list.
        """
        self._stimulus_events.append(
            {
                "sample_index":   sample_idx,
                "event_type":     event_type,
                "stimulus_name":  stim_name,
                "stimulus_index": stim_idx,
            }
        )

    def close(self) -> "BinToHDF5Worker | None":
        """Close the binary file and return a ready-to-start conversion worker.

        The caller is responsible for connecting the worker's
        ``conversion_done`` and ``conversion_failed`` signals, then calling
        ``worker.start()``.

        Returns:
            A :class:`BinToHDF5Worker` ready to start, or ``None`` if no
            binary file is present (e.g. :meth:`open` was never called).
        """
        if self._bin_file is not None:
            try:
                self._bin_file.flush()
                self._bin_file.close()
            except Exception:
                pass
            finally:
                self._bin_file = None

        if (
            self._bin_path is not None
            and self._bin_path.exists()
            and self._h5_path is not None
            and self._header is not None
        ):
            worker = BinToHDF5Worker(
                self._bin_path,
                self._h5_path,
                self._header,
                stimulus_events=list(self._stimulus_events),
            )
            self._conversion_worker = worker
            self._stimulus_events   = []
            return worker

        return None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def folder(self) -> Path | None:
        """Directory containing the recording files, or ``None``."""
        return self._folder

    @property
    def is_open(self) -> bool:
        """``True`` if the binary file is currently open for writing."""
        return self._bin_file is not None

    @property
    def path(self) -> Path | None:
        """Intended HDF5 path (not yet written until conversion completes)."""
        return self._h5_path

    @property
    def n_saved(self) -> int:
        """Total samples written since the last :meth:`open` call."""
        return self._n_saved

    @property
    def conversion_worker(self) -> "BinToHDF5Worker | None":
        """The worker created by the most recent :meth:`close` call."""
        return self._conversion_worker

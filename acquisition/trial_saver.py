"""
Trial-based data saver.

During a protocol run, each trial's AI data is appended to a flat binary
(``.bin``) file as it arrives.  An in-memory trial index table tracks the
byte offset and sample count of every trial.  When the protocol ends,
:meth:`TrialSaver.close` converts the binary file to the final per-trial
HDF5 layout.  The binary file is always preserved as a raw-data backup.

Binary file format
------------------
Consecutive float64 trial blocks written in C order::

    trial_0_block  (N_channels × n_trial_samples_0, float64)
    trial_1_block  (N_channels × n_trial_samples_1, float64)
    ...

Each block is recoverable as::

    raw   = np.frombuffer(raw_bytes, dtype=np.float64)
    data  = raw.reshape(N_channels, n_samples)

HDF5 file layout (unchanged schema)
-------------------------------------
::

    /metadata/
        sample_rate            (scalar attribute, int)
        start_time             (scalar attribute, ISO-8601 string)
        clamp_mode             (scalar attribute, str)
        n_trials               (scalar attribute, int)
        channel_names          (string dataset)
        display_scales         (float64 dataset)
        units                  (string dataset)
        protocol               (string dataset — full JSON)
        trial_order            (int32 dataset)
        trial_stimulus_names   (string dataset)
        trial_stimulus_types   (string dataset)
    /subject/                  (attributes)
    /trial_001/
        analog_input           (float64, shape (N_channels, N_samples))
        attrs: stimulus_name, stimulus_index, trial_index, onset_time, video_file

File naming
-----------
``{save_dir}/{expt_id}/{expt_id}_{genotype}_{YYYYMMDD_HHMMSS}_trials.h5``
``{save_dir}/{expt_id}/{expt_id}_{genotype}_{YYYYMMDD_HHMMSS}_trials.bin``

Developer notes
---------------
All public methods must be called from the GUI thread.
:meth:`TrialSaver.close` converts ``.bin`` → HDF5 synchronously on the
GUI thread (trial files are small, conversion is fast).  The ``.bin``
file is always kept as a raw-data backup.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

try:
    import h5py
    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False

from config import ChannelDef, N_AI_CHANNELS, SAMPLE_RATE
from acquisition.trial_protocol import TrialProtocol, protocol_to_dict


class TrialSaver:
    """Binary-backed writer for per-trial data, converted to HDF5 on close.

    Trials are appended to a ``.bin`` file during the run.  An in-memory
    index table records each trial's byte offset and sample count.
    :meth:`close` converts everything to the standard HDF5 layout and keeps
    the binary file as a backup.

    Attributes:
        _bin_file: Open binary file handle during a run, ``None`` otherwise.
        _path (Path | None): Intended HDF5 file path.
        _folder (Path | None): Directory containing the files.
        _trial_index (list[dict]): In-memory per-trial metadata table.
    """

    def __init__(self) -> None:
        self._bin_file:   object        = None
        self._bin_path:   Path | None   = None
        self._path:       Path | None   = None
        self._folder:     Path | None   = None
        self._header:     dict | None   = None
        self._trial_index: list[dict]   = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open(
        self,
        save_dir: str | Path,
        protocol: TrialProtocol,
        trial_order: list[int],
        subject_metadata: dict,
        channel_defs: list[ChannelDef],
    ) -> Path:
        """Open a new binary file and store HDF5 metadata for later conversion.

        Args:
            save_dir: Root directory for saving.
            protocol: The :class:`~acquisition.trial_protocol.TrialProtocol`
                being run.
            trial_order: Shuffled list of stimulus indices.
            subject_metadata: Dict with subject information.
            channel_defs: List of :data:`~config.ChannelDef` tuples for
                the active channels (CC or VC).

        Returns:
            Intended path of the HDF5 file (not yet written).

        Raises:
            RuntimeError: If h5py is not installed.
        """
        if not HAS_H5PY:
            raise RuntimeError("h5py is not installed. Run: pip install h5py")

        expt_id       = subject_metadata.get("expt_id", "ephys") or "ephys"
        genotype      = subject_metadata.get("genotype", "") or "unknown"
        safe_genotype = genotype.replace(" ", "_").replace("/", "-")
        datestamp     = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        stem          = f"{expt_id}_{safe_genotype}_{datestamp}_trials"

        self._folder   = Path(save_dir) / expt_id
        self._folder.mkdir(parents=True, exist_ok=True)
        self._path     = self._folder / f"{stem}.h5"
        self._bin_path = self._folder / f"{stem}.bin"

        self._header = {
            "sample_rate":    SAMPLE_RATE,
            "start_time":     datetime.datetime.now().isoformat(),
            "n_channels":     len(channel_defs),
            "channel_names":  [ch[0] for ch in channel_defs],
            "display_scales": [float(ch[3]) for ch in channel_defs],
            "units":          [ch[4] for ch in channel_defs],
            "clamp_mode":     protocol.clamp_mode,
            "n_trials":       len(trial_order),
            "protocol_json":  json.dumps(protocol_to_dict(protocol), indent=2),
            "trial_order":    list(trial_order),
            "trial_stimulus_names": [protocol.stimuli[i].name for i in trial_order],
            "trial_stimulus_types": [protocol.stimuli[i].type for i in trial_order],
            "subject": {
                k: (str(v) if v else "") for k, v in subject_metadata.items()
            },
        }

        self._bin_file    = open(self._bin_path, "wb")
        self._trial_index = []
        return self._path

    def write_trial(
        self,
        trial_index:    int,
        stimulus_index: int,
        stimulus_name:  str,
        onset_time:     str,
        data:           NDArray[np.float64],
        video_filename: str = "",
    ) -> None:
        """Append one trial's data to the binary file and record its metadata.

        Args:
            trial_index: 0-based index of this trial in the run.
            stimulus_index: 0-based index into ``protocol.stimuli``.
            stimulus_name: Human-readable stimulus label.
            onset_time: ISO-8601 timestamp string for the trial onset.
            data: ``(N_channels, n_samples)`` float64 array in Volts.
            video_filename: Basename of the ``.avi`` file for this trial.
                Empty string if no video was saved.
        """
        if self._bin_file is None:
            return

        n_samples    = data.shape[1]
        byte_offset  = self._bin_file.tell()
        self._bin_file.write(data.astype(np.float64).tobytes())
        self._bin_file.flush()

        self._trial_index.append(
            {
                "trial_index":    trial_index,
                "stimulus_index": stimulus_index,
                "stimulus_name":  stimulus_name,
                "onset_time":     onset_time,
                "n_samples":      n_samples,
                "byte_offset":    byte_offset,
                "video_filename": video_filename,
            }
        )

    def close(self) -> Path | None:
        """Convert the binary file to HDF5 and return the HDF5 path.

        Converts synchronously on the calling thread (GUI thread) — trial
        files are small enough that this is acceptable.  The ``.bin`` file
        is always preserved as a backup.

        Returns:
            Full path to the HDF5 file, or ``None`` if the file was
            never opened.
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
            self._bin_path is None
            or not self._bin_path.exists()
            or self._header is None
            or self._path is None
        ):
            return self._path

        try:
            self._convert_to_hdf5()
        except Exception:
            pass  # keep .bin; HDF5 may be incomplete but bin is intact

        return self._path

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path | None:
        """Intended HDF5 file path, or ``None`` if not yet opened."""
        return self._path

    @property
    def folder(self) -> Path | None:
        """Directory containing the recording files, or ``None``."""
        return self._folder

    @property
    def is_open(self) -> bool:
        """``True`` if the binary file is currently open for writing."""
        return self._bin_file is not None

    # ------------------------------------------------------------------
    # Private conversion helper
    # ------------------------------------------------------------------

    def _convert_to_hdf5(self) -> None:
        """Read the binary file and write the per-trial HDF5 layout."""
        import h5py as _h5py

        header = self._header
        n_ch   = header["n_channels"]

        with open(self._bin_path, "rb") as bf, _h5py.File(self._path, "w") as hf:
            dt_str = _h5py.string_dtype()

            # /metadata
            meta = hf.create_group("metadata")
            meta.attrs["sample_rate"] = header["sample_rate"]
            meta.attrs["start_time"]  = header["start_time"]
            meta.attrs["clamp_mode"]  = header["clamp_mode"]
            meta.attrs["n_trials"]    = header["n_trials"]

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
            meta.create_dataset("protocol", data=header["protocol_json"], dtype=dt_str)
            meta.create_dataset(
                "trial_order",
                data=np.array(header["trial_order"], dtype=np.int32),
            )
            meta.create_dataset(
                "trial_stimulus_names",
                data=np.array(header["trial_stimulus_names"], dtype=object),
                dtype=dt_str,
            )
            meta.create_dataset(
                "trial_stimulus_types",
                data=np.array(header["trial_stimulus_types"], dtype=object),
                dtype=dt_str,
            )

            # /subject
            subj = hf.create_group("subject")
            for k, v in header.get("subject", {}).items():
                subj.attrs[k] = str(v) if v else ""

            # /trial_NNN groups
            for entry in self._trial_index:
                group_name = f"trial_{entry['trial_index'] + 1:03d}"
                n_samp     = entry["n_samples"]

                bf.seek(entry["byte_offset"])
                raw  = bf.read(n_ch * n_samp * 8)
                data = np.frombuffer(raw, dtype=np.float64).reshape(n_ch, n_samp)

                grp = hf.create_group(group_name)
                grp.attrs["stimulus_name"]  = entry["stimulus_name"]
                grp.attrs["stimulus_index"] = entry["stimulus_index"]
                grp.attrs["trial_index"]    = entry["trial_index"]
                grp.attrs["onset_time"]     = entry["onset_time"]
                grp.attrs["video_file"]     = entry["video_filename"]
                grp.create_dataset("analog_input", data=data, dtype=np.float64)

"""
HDF5 writer for trial-based acquisition.

Each trial is saved as its own group in a single HDF5 file:

    /metadata/
        protocol        (JSON string — complete protocol definition)
        trial_order     (int32 array — stimulus index for each trial)
        sample_rate     (attr)
        start_time      (attr ISO-8601)
        channel_names   (string array)
        display_scales  (float64 array)
        units           (string array)
    /subject/           (attrs: expt_id, genotype, age, sex, targeted_cell_type)
    /trial_001/
        analog_input    (float64, shape 5 × N_trial_samples, pre-allocated)
        attrs:
            stimulus_name   (str)
            stimulus_index  (int)
            trial_index     (int)
            onset_time      (ISO-8601 str)
    /trial_002/ ...

All public methods are called from the GUI thread.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import numpy as np

try:
    import h5py
    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False

from config import N_AI_CHANNELS, SAMPLE_RATE
from acquisition.trial_protocol import TrialProtocol, protocol_to_dict


class TrialHDF5Saver:
    """Writes one HDF5 file with per-trial groups for a protocol run."""

    def __init__(self) -> None:
        self._file:   "h5py.File | None"  = None
        self._path:   Path | None          = None
        self._folder: Path | None          = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open(
        self,
        save_dir: str | Path,
        protocol: TrialProtocol,
        trial_order: list[int],
        subject_metadata: dict,
        channel_defs: list,          # AI_CHANNELS or AI_CHANNELS_VC list
    ) -> Path:
        """
        Create the HDF5 file and write global metadata.
        Returns the path of the created file.
        """
        if not HAS_H5PY:
            raise RuntimeError("h5py is not installed. Run: pip install h5py")

        expt_id  = subject_metadata.get("expt_id", "ephys") or "ephys"
        genotype = subject_metadata.get("genotype", "") or "unknown"
        safe_genotype = genotype.replace(" ", "_").replace("/", "-")
        datestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"{expt_id}_{safe_genotype}_{datestamp}_trials"

        self._folder = Path(save_dir) / expt_id
        self._folder.mkdir(parents=True, exist_ok=True)
        self._path = self._folder / f"{stem}.h5"

        self._file = h5py.File(self._path, "w")
        self._write_metadata(protocol, trial_order, subject_metadata, channel_defs)
        return self._path

    def begin_trial(
        self,
        trial_index: int,
        stimulus_index: int,
        stimulus_name: str,
        onset_time: str,
        n_samples: int,
    ) -> None:
        """
        Pre-create the group and pre-allocated dataset for one trial.
        Must be called before write_trial().
        """
        if self._file is None:
            return
        group_name = f"trial_{trial_index + 1:03d}"
        grp = self._file.create_group(group_name)
        grp.attrs["stimulus_name"]  = stimulus_name
        grp.attrs["stimulus_index"] = stimulus_index
        grp.attrs["trial_index"]    = trial_index
        grp.attrs["onset_time"]     = onset_time
        grp.create_dataset(
            "analog_input",
            shape=(N_AI_CHANNELS, n_samples),
            dtype=np.float64,
        )

    def write_trial(self, trial_index: int, data: np.ndarray) -> None:
        """
        Write the data buffer for a completed trial.
        data must have shape (N_AI_CHANNELS, n_samples).
        """
        if self._file is None:
            return
        group_name = f"trial_{trial_index + 1:03d}"
        if group_name not in self._file:
            return
        n = data.shape[1]
        ds = self._file[group_name]["analog_input"]
        # Guard against buffer overruns (should never happen)
        cols = min(n, ds.shape[1])
        ds[:, :cols] = data[:, :cols]
        self._file.flush()

    def close(self) -> Path | None:
        """Flush and close the file. Returns the path."""
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception:
                pass
            finally:
                self._file = None
        return self._path

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def folder(self) -> Path | None:
        return self._folder

    @property
    def is_open(self) -> bool:
        return self._file is not None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _write_metadata(
        self,
        protocol: TrialProtocol,
        trial_order: list[int],
        subject_metadata: dict,
        channel_defs: list,
    ) -> None:
        grp = self._file.create_group("metadata")
        grp.attrs["sample_rate"] = SAMPLE_RATE
        grp.attrs["start_time"]  = datetime.datetime.now().isoformat()

        dt_str = h5py.string_dtype()
        names  = [ch[0] for ch in channel_defs]
        scales = [ch[3] for ch in channel_defs]
        units  = [ch[4] for ch in channel_defs]
        grp.create_dataset("channel_names",  data=np.array(names,  dtype=object), dtype=dt_str)
        grp.create_dataset("display_scales", data=np.array(scales, dtype=np.float64))
        grp.create_dataset("units",          data=np.array(units,  dtype=object), dtype=dt_str)

        # Full protocol as a JSON string — self-describing
        protocol_json = json.dumps(protocol_to_dict(protocol), indent=2)
        grp.create_dataset("protocol", data=protocol_json, dtype=dt_str)

        # Trial order array (which stimulus was played on each trial)
        grp.create_dataset(
            "trial_order",
            data=np.array(trial_order, dtype=np.int32),
        )

        # Subject metadata
        subj_grp = self._file.create_group("subject")
        for key, value in subject_metadata.items():
            subj_grp.attrs[key] = str(value) if value else ""

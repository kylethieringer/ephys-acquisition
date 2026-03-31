"""
HDF5 writer for trial-based acquisition.

Writes one HDF5 file per protocol run.  Each trial occupies its own group
with a **pre-allocated** fixed-size dataset (unlike the dynamic extension
used by :class:`~acquisition.data_saver.HDF5Saver` for continuous mode).

Pre-allocation guarantees that the disk layout is contiguous for each trial,
which makes per-trial reads fast, and avoids metadata overhead from repeated
dataset resizes.

HDF5 file layout
-----------------
::

    /metadata/
        sample_rate      (scalar attribute, int)
        start_time       (scalar attribute, ISO-8601 string)
        channel_names    (string dataset, shape (N_AI_CHANNELS,))
        display_scales   (float64 dataset, shape (N_AI_CHANNELS,))
        units            (string dataset, shape (N_AI_CHANNELS,))
        protocol         (string dataset — full JSON protocol definition)
        trial_order      (int32 dataset — stimulus index for each trial)
    /subject/            (attributes: expt_id, genotype, age, sex, targeted_cell_type)
    /trial_001/
        analog_input     (float64 dataset, shape (N_AI_CHANNELS, N_trial_samples),
                          pre-allocated — no compression)
        attrs:
            stimulus_name   (str)
            stimulus_index  (int)
            trial_index     (int)
            onset_time      (ISO-8601 str)
    /trial_002/ ...

File naming
-----------
``{save_dir}/{expt_id}/{expt_id}_{genotype}_{YYYYMMDD_HHMMSS}_trials.h5``

Developer notes
---------------
All public methods must be called from the GUI thread.
:meth:`begin_trial` pre-creates the group and dataset;
:meth:`write_trial` fills the dataset from the accumulation buffer.
The two-step pattern avoids a race condition where data arrives before the
group exists.
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

from config import N_AI_CHANNELS, SAMPLE_RATE
from acquisition.trial_protocol import TrialProtocol, protocol_to_dict
from config import ChannelDef


class TrialHDF5Saver:
    """Write per-trial HDF5 groups for one protocol run.

    Attributes:
        _file (h5py.File | None): Open file handle, or ``None`` when closed.
        _path (Path | None): Full path to the HDF5 file.
        _folder (Path | None): Directory containing the file.
    """

    def __init__(self) -> None:
        self._file:   "h5py.File | None" = None
        self._path:   Path | None        = None
        self._folder: Path | None        = None

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
        """Create the HDF5 file and write global metadata.

        The experiment folder is created automatically if needed.

        Args:
            save_dir: Root directory for saving.
            protocol: The :class:`~acquisition.trial_protocol.TrialProtocol`
                being run.  Serialised to JSON and stored under
                ``/metadata/protocol``.
            trial_order: Shuffled list of stimulus indices as returned by
                :func:`~acquisition.trial_protocol.build_trial_order`.
                Stored as an int32 array under ``/metadata/trial_order``.
            subject_metadata: Dict with subject information.  Expected keys:
                ``"expt_id"``, ``"genotype"``, ``"age"``, ``"sex"``,
                ``"targeted_cell_type"``.
            channel_defs: List of :data:`~config.ChannelDef` tuples
                describing the active AI channels (use
                :data:`~config.AI_CHANNELS` for CC mode or
                :data:`~config.AI_CHANNELS_VC` for VC mode).

        Returns:
            Full path to the created HDF5 file.

        Raises:
            RuntimeError: If h5py is not installed.
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
        """Pre-create the group and dataset for one upcoming trial.

        Must be called before :meth:`write_trial`.  Creates the group
        ``/trial_{trial_index+1:03d}`` with metadata attributes and a
        pre-allocated ``analog_input`` dataset of shape
        ``(N_AI_CHANNELS, n_samples)``.

        Args:
            trial_index: 0-based index of this trial in the run.
            stimulus_index: 0-based index into ``protocol.stimuli`` for
                this trial.
            stimulus_name: Human-readable stimulus label (from
                :attr:`~acquisition.trial_protocol.StimulusDefinition.name`).
            onset_time: ISO-8601 timestamp string for the trial onset.
            n_samples: Expected number of AI samples for this trial
                (pre + stim + post).  The dataset is allocated to exactly
                this size; :meth:`write_trial` will not exceed it.
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

    def write_trial(self, trial_index: int, data: NDArray[np.float64]) -> None:
        """Fill the pre-allocated dataset for a completed trial.

        Writes the data buffer collected by the state machine into the
        dataset created by :meth:`begin_trial`.  If ``data`` is longer
        than the pre-allocated dataset (should not happen under normal
        operation), extra columns are silently discarded.

        Args:
            trial_index: 0-based trial index, matching the value passed to
                the preceding :meth:`begin_trial` call.
            data: 2-D float64 array of shape ``(N_AI_CHANNELS, n_samples)``
                in raw Volts.

        Note:
            Calls ``file.flush()`` after writing so data is durable even
            if the process is killed before :meth:`close` is called.
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
        """Flush and close the HDF5 file.

        Returns:
            Full path to the closed file, or ``None`` if the file was
            never opened.
        """
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
        """Full path to the HDF5 file, or ``None`` if not yet opened."""
        return self._path

    @property
    def folder(self) -> Path | None:
        """Directory containing the HDF5 file, or ``None`` if not yet opened."""
        return self._folder

    @property
    def is_open(self) -> bool:
        """``True`` if the HDF5 file is currently open."""
        return self._file is not None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _write_metadata(
        self,
        protocol: TrialProtocol,
        trial_order: list[int],
        subject_metadata: dict,
        channel_defs: list[ChannelDef],
    ) -> None:
        """Write ``/metadata`` and ``/subject`` groups to the open file.

        Args:
            protocol: Protocol to serialise into ``/metadata/protocol``.
            trial_order: Stimulus index sequence to store in
                ``/metadata/trial_order``.
            subject_metadata: Subject information to write as attrs under
                ``/subject/``.
            channel_defs: Channel definitions used to populate
                ``channel_names``, ``display_scales``, and ``units``.
        """
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

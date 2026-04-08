"""Acquisition package — shared utilities for data savers."""

from __future__ import annotations

import numpy as np


def write_common_hdf5_metadata(hf, header: dict):
    """Write ``/metadata/`` channel info and ``/subject/`` group to an open HDF5 file.

    Creates:
        ``/metadata/`` with attrs ``sample_rate``, ``start_time`` and datasets
        ``channel_names``, ``display_scales``, ``units``.

        ``/subject/`` with key-value attrs from ``header["subject"]``.

    The caller is responsible for adding any mode-specific metadata
    (e.g. ``clamp_mode``, ``protocol`` for trial mode).

    Args:
        hf: An open ``h5py.File`` in write mode.
        header: Metadata dict containing ``"sample_rate"``, ``"start_time"``,
            ``"channel_names"``, ``"display_scales"``, ``"units"``, and
            ``"subject"`` (a dict of key-value pairs).

    Returns:
        The ``/metadata/`` :class:`h5py.Group` so the caller can add
        extra attrs or datasets.
    """
    import h5py as _h5py

    dt_str = _h5py.string_dtype()

    meta = hf.create_group("metadata")
    meta.attrs["sample_rate"] = header["sample_rate"]
    meta.attrs["start_time"]  = header["start_time"]

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

    return meta

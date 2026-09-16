# Data

What a recording writes, how those files are laid out inside, and how to read
them back.

::::{grid} 1 2 2 3
:gutter: 3

:::{grid-item-card} Files on disk
:link: file-layout
:link-type: doc

The family of files each recording produces and where they live.
:::

:::{grid-item-card} HDF5 layout
:link: hdf5-format
:link-type: doc

The two layouts, one per acquisition mode, and the fields they share.
:::

:::{grid-item-card} Loading data in Python
:link: loading-data
:link-type: doc

{py:mod}`utils.data_loader` — the shortest path to arrays in memory.
:::

::::

```{toctree}
:hidden:

file-layout
hdf5-format
loading-data
```

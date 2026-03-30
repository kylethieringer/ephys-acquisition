"""
Utilities for loading and visualizing HDF5 ephys data files.
"""

from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt

try:
    import h5py
    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False


def load_hdf5(filepath: str | Path) -> dict:
    """
    Load HDF5 ephys data file.
    
    Returns:
        dict with keys:
            - "data": (n_channels, n_samples) array of raw voltage
            - "sample_rate": sampling rate in Hz
            - "channel_names": list of channel names
            - "display_scales": list of scales to convert V to display units
            - "units": list of unit strings
            - "metadata": raw metadata dict
    """
    if not HAS_H5PY:
        raise RuntimeError("h5py is not installed. Run: pip install h5py")
    
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")
    
    result = {}
    
    with h5py.File(filepath, 'r') as f:
        # Read metadata
        meta = f['metadata']
        result['sample_rate'] = meta.attrs['sample_rate']
        result['channel_names'] = [name for name in meta['channel_names'][:]]
        result['display_scales'] = meta['display_scales'][:]
        result['units'] = [unit for unit in meta['units'][:]]
        result['start_time'] = meta.attrs['start_time']
        
        # Read data
        result['data'] = f['data']['analog_input'][:]
    
    result['metadata'] = {
        'sample_rate': result['sample_rate'],
        'start_time': result['start_time'],
        'n_samples': result['data'].shape[1],
        'n_channels': result['data'].shape[0],
    }
    
    return result


def plot_data(
    filepath: str | Path,
    time_range: tuple[float, float] | None = None,
    figsize: tuple[int, int] = (14, 10),
    show: bool = True,
) -> tuple[plt.Figure, plt.Axes]:
    """
    Load HDF5 data and plot with correct channel names, scales, and units.
    
    Parameters:
        filepath: Path to HDF5 file
        time_range: (start_sec, end_sec) tuple to plot subset of data.
                   If None, plots all data.
        figsize: Figure size (width, height)
        show: If True, display the plot
    
    Returns:
        (fig, axes) tuple
    """
    data_dict = load_hdf5(filepath)
    
    raw_data = data_dict['data']  # (n_channels, n_samples)
    sr = data_dict['sample_rate']
    names = data_dict['channel_names']
    scales = data_dict['display_scales']
    units = data_dict['units']
    
    # Apply display scales to convert raw voltage to display units
    scaled_data = raw_data * scales[:, np.newaxis]  # (n_channels, n_samples)
    
    # Handle time range
    n_samples_total = scaled_data.shape[1]
    start_idx = 0
    end_idx = n_samples_total
    
    if time_range is not None:
        start_sec, end_sec = time_range
        start_idx = max(0, int(start_sec * sr))
        end_idx = min(n_samples_total, int(end_sec * sr))
    
    scaled_data = scaled_data[:, start_idx:end_idx]
    
    # Create time axis (in seconds)
    time = np.arange(scaled_data.shape[1]) / sr + start_idx / sr
    
    # Create subplots (one per channel)
    n_ch = len(names)
    fig, axes = plt.subplots(n_ch, 1, figsize=figsize, sharex=True)
    
    # Handle single channel case (axes is not an array)
    if n_ch == 1:
        axes = [axes]
    
    # Plot each channel
    for i, (ax, name, unit) in enumerate(zip(axes, names, units)):
        ax.plot(time, scaled_data[i], linewidth=0.5)
        ax.set_ylabel(f"{name}\n({unit})", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.set_facecolor('#f8f8f8')
    
    # Set common x-label
    axes[-1].set_xlabel("Time (s)", fontsize=11)
    
    # Add title with file info
    filepath = Path(filepath)
    title = f"{filepath.name} | {n_ch} channels @ {sr/1000:.0f} kHz"
    fig.suptitle(title, fontsize=12, fontweight='bold')
    
    plt.tight_layout()
    
    if show:
        plt.show()
    
    return fig, axes


def plot_data_overlay(
    filepath: str | Path,
    time_range: tuple[float, float] | None = None,
    figsize: tuple[int, int] = (12, 6),
    show: bool = True,
    colors: list[str] | None = None,
) -> tuple[plt.Figure, plt.Axes]:
    """
    Load HDF5 data and plot all channels overlaid on single axes.
    
    Parameters:
        filepath: Path to HDF5 file
        time_range: (start_sec, end_sec) tuple to plot subset of data
        figsize: Figure size (width, height)
        show: If True, display the plot
        colors: Optional list of colors for each channel
    
    Returns:
        (fig, ax) tuple
    """
    data_dict = load_hdf5(filepath)
    
    raw_data = data_dict['data']  # (n_channels, n_samples)
    sr = data_dict['sample_rate']
    names = data_dict['channel_names']
    scales = data_dict['display_scales']
    units = data_dict['units']
    
    # Apply display scales
    scaled_data = raw_data * scales[:, np.newaxis]
    
    # Handle time range
    n_samples_total = scaled_data.shape[1]
    start_idx = 0
    end_idx = n_samples_total
    
    if time_range is not None:
        start_sec, end_sec = time_range
        start_idx = max(0, int(start_sec * sr))
        end_idx = min(n_samples_total, int(end_sec * sr))
    
    scaled_data = scaled_data[:, start_idx:end_idx]
    
    # Create time axis (in seconds)
    time = np.arange(scaled_data.shape[1]) / sr + start_idx / sr
    
    # Create figure
    fig, ax = plt.subplots(figsize=figsize)
    
    # Plot each channel with offset for visibility
    if colors is None:
        colors = plt.cm.tab10(np.linspace(0, 1, len(names)))
    
    for i, (name, unit, color) in enumerate(zip(names, units, colors)):
        ax.plot(time, scaled_data[i], label=f"{name} ({unit})", 
                linewidth=1, color=color, alpha=0.8)
    
    ax.set_xlabel("Time (s)", fontsize=11)
    ax.set_ylabel("Amplitude", fontsize=11)
    ax.legend(loc='upper right', fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_facecolor('#f8f8f8')
    
    # Add title
    filepath = Path(filepath)
    title = f"{filepath.name} | {len(names)} channels @ {sr/1000:.0f} kHz"
    fig.suptitle(title, fontsize=12, fontweight='bold')
    
    plt.tight_layout()
    
    if show:
        plt.show()
    
    return fig, ax


if __name__ == "__main__":
    # Example usage:
    # 1. Plot all channels in separate subplots:
    #    plot_data("path/to/ephys_*.h5")
    #
    # 2. Plot subset of data:
    #    plot_data("path/to/ephys_*.h5", time_range=(0, 10))
    #
    # 3. Plot all channels overlaid:
    #    plot_data_overlay("path/to/ephys_*.h5")
    #
    # 4. Load data programmatically:
    #    data = load_hdf5("path/to/ephys_*.h5")
    #    print(data['channel_names'])
    #    print(data['data'].shape)
    pass

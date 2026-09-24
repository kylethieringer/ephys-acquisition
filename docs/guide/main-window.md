# The main window

The window has three fixed regions — a chrome bar across the top, an icon
sidebar on the left, and a recording bar along the bottom. Only the centre
changes as you switch sidebar tabs.

```text
┌──────────────────────────────────────────────────────────────────────┐
│  TopChromeBar: [Session]  [Continuous|Trial]  [CC|VC]  [Status]      │
├──────┬───────────────────────────────────────────────────────────────┤
│  S   │                                                               │
│  i   │                    Page content (stacked)                     │
│  d   │                                                               │
│  e   ├───────────────────────────────────────────────────────────────┤
│  b   │  Recording: [Start] [Stop] | [● Record] [Stop Rec] | [Audio]  │
│  a   │                                                               │
│  r   │                                                               │
└──────┴───────────────────────────────────────────────────────────────┘
```

The chrome bar and recording bar are always visible, on every page.

## Top chrome bar

Session label
: The current experiment ID. Confirms at a glance which session you are about
  to write into.

Mode pill
: Toggles `Continuous` / `Trial`. See {doc}`continuous-mode` and
  {doc}`trial-mode`.

Clamp pill
: Toggles `CC` / `VC`. **Visible in continuous mode only** — in trial mode the
  clamp is fixed by the protocol.

Status badge
: Live acquisition state.

## Recording bar

Four buttons, in the order you use them:

| Button | Effect |
|---|---|
| **Start** | DAQ sampling begins, camera arms. Traces go live; nothing is saved. |
| **● Record** | TTL fires, camera captures, HDF5 and video open. |
| **Stop Recording** | TTL stops; files close after the guard delay. Camera stays armed. |
| **Stop** | Camera closes, DAQ shuts down. |

There is also a **Quick note** field for jotting an observation into the
session metadata without leaving the page.

### Audio monitor

The **🔊 Audio** checkbox at the right of the bar plays the membrane channel
through the speaker, the same way a patch amplifier's audio monitor does. It
plays the recorded signal itself. Nothing is detected or synthesised. Spikes
sound like pops, synaptic noise like hiss, and line pickup like a 60 Hz buzz.

- It is **off by default**, so starting acquisition never makes unexpected
  noise. It is independent of recording and works in both acquisition modes.
- The slider beside it sets the volume. The scale is logarithmic, spanning
  ±20 dB around {py:data}`config.AUDIO_DEFAULT_GAIN`. Raise it to hear
  subthreshold activity.
- The signal is high-passed at {py:data}`config.AUDIO_HIGHPASS_HZ` (100 Hz)
  first. This strips the resting potential and slow drift, which would
  otherwise use up the whole output range.
- Playback stays within about {py:data}`config.AUDIO_BUFFER_MS` (50 ms) of the
  trace. Under heavy GUI load a chunk is dropped, so you hear a brief glitch
  rather than audio that falls further and further behind.

If the machine has no audio output device, the checkbox does nothing and
acquisition is unaffected. See {doc}`../reference/configuration` for the
constants.

## Sidebar pages

::::{tab-set}

:::{tab-item} Acquire
The working page during an experiment.

- **Left** — live rolling traces for every analog input channel, showing
  the last {py:data}`config.DISPLAY_SECONDS` seconds (5 s by default)
- **Right**
  - Camera preview
  - Subject card — experiment ID, genotype, age, sex. The **↻** button beside
    the Experiment ID field fills in the next unused ID (see below).
  - Protocol widget — dropdown of `.json` protocols from `D:/protocols`, with
    **Run Protocol** / **Stop Protocol**
  - Stimulus panel — ad-hoc step stimulus, continuous mode only. Labels switch
    between pA and mV with the clamp mode.

Both dividers can be dragged. The vertical one trades trace width for sidebar
width. The horizontal one trades camera height for card space. Their positions
are saved on exit and restored at the next launch.

**Next experiment ID.** Clicking **↻** scans the save directory for experiment
folders (letters followed by digits, such as `fre074`). It fills in one past
the highest number in a series, keeping the existing zero-padding, so `fre074`
gives `fre075`. The series comes from the letters already in the field: with
`fre074` or just `fre` there, the click continues `fre`. The field usually still
holds the last ID, so a click normally continues that series. Only when the field
is empty does it use the series of the most recently modified folder. To start or
switch to another series, type its letter prefix into the field first (`kt`,
say), then click. A prefix with no folders yet starts at `kt001`. If no ID can be worked out, the field is
left unchanged.
:::

:::{tab-item} Protocol
Where you build and manage protocols.

- **Left** — saved-protocol list with a filter box, plus Refresh and New
- **Right** — the protocol builder editor, inline

See {doc}`protocols`.
:::

:::{tab-item} Channels
Per-channel display control, one row each:

colour swatch · port · signal name and units · Y-min / Y-max · auto-range
toggle · save checkbox

```{note}
The save checkbox controls which channels are written to disk. Unchecking a
channel here means it is not in the recording at all — not merely hidden.
```
:::

:::{tab-item} Setup
Rig configuration, as a 2×2 card grid:

- **DAQ Device** — device name, sample rate, chunk size, counter, AO command channel
- **Channel Mapping** — AI/AO/CTR port → signal → scale → units
- **Data Save Location** — save directory picker
- **Camera** — TTL frame rate and exposure

These mirror {py:mod}`config`. Changes made here apply to the running session;
{py:mod}`config` remains the persistent source of truth.
:::

::::

## How the pieces connect

{py:class}`ui.main_window.MainWindow` assembles the panels and owns the
acquisition controllers. Both controllers —
{py:class}`acquisition.continuous_mode.ContinuousAcquisition` and
{py:class}`acquisition.trial_mode.TrialAcquisition` — are always instantiated,
but only the active one is started. They share a single ring buffer.

{py:class}`ui.control_panel.ControlPanel` is deliberately split into
independently-placeable sub-widgets (`mode_pill`, `clamp_pill`, `subject_card`,
`recording_settings`, `protocol_widget`, `recording_bar`) so the window can
scatter them across the layout. The panel emits signals and holds no references
to acquisition objects; `MainWindow` does all the wiring.

The per-channel checkboxes and Y-range controls live only on the Channels page.
Switching clamp mode mutates those widgets in place, so page changes never lose
their state.

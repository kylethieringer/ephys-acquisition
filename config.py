"""
Hardware constants and channel definitions for ephys acquisition.
All other modules import from here.
"""

# NI DAQ device name (check NI MAX for your device name)
DEVICE_NAME = "Dev1"

# Acquisition parameters
SAMPLE_RATE = 20000       # Hz
CHUNK_SIZE = 200          # samples per read (~10 ms at 20 kHz)
DISPLAY_SECONDS = 5       # rolling window shown in trace panel
DISPLAY_SAMPLES = SAMPLE_RATE * DISPLAY_SECONDS  # 100000

# Analog input channels
# Each entry: (display_name, ni_channel, terminal_config, display_scale, units)
# display_scale: multiply raw voltage by this to get display units
#   AI0: 0.1 V/mV  → display = V / 0.1 = V * 10   (mV)
#   AI1: 0.5 V/nA  → display = V / 0.5 = V * 2    (nA)
#   AI2: 400 pA/V  → display = V * 400              (pA)
#   AI3: raw        → display = V * 1               (V)
#   AI4: raw        → display = V * 1               (V)
AI_CHANNELS = [
    ("ScAmpOut",    "ai0", "differential", 10.0,  "mV"),
    ("RawAmpOut",   "ai1", "differential",  2.0,  "nA"),
    ("AmpCmd",      "ai2", "differential", 400.0, "pA"),
    ("Camera",      "ai3", "rse",           1.0,  "V"),
    ("TTLLoopback", "ai4", "differential",  1.0,  "V"),
]

N_AI_CHANNELS = len(AI_CHANNELS)

# Analog output channels
AO_COMMAND_CH    = "ao0"    # current injection

# Counter output for camera TTL (CTR0 default output terminal is PFI12)
CTR_CHANNEL      = "ctr0"
CTR_OUT_TERMINAL = "PFI12"

# AO scale for current injection: 400 pA per Volt
# To output X pA: send X / 400.0 Volts
AO_PA_PER_VOLT = 400.0

# AO scale for voltage clamp: mV per Volt (amplifier command sensitivity)
# Axopatch 200B VC mode: 20 mV/V.  To output X mV: send X / 20.0 Volts
AO_MV_PER_VOLT = 20.0

# Voltage-clamp channel definitions.
# In VC mode AI0 carries membrane current and AI1 carries pipette potential;
# the scaling is different from current-clamp mode.
#   AI0 VC: amplifier scale 10 V/nA  → display_scale = 0.1  nA/V
#   AI1 VC: amplifier scale  1 V/V   → display_scale = 1000 mV/V
AI_CHANNELS_VC = [
    ("I_mem",       "ai0", "differential", 0.1,    "nA"),
    ("V_pip",       "ai1", "differential", 1000.0, "mV"),
    ("AmpCmd",      "ai2", "differential", 400.0,  "pA"),
    ("Camera",      "ai3", "rse",          1.0,    "V"),
    ("TTLLoopback", "ai4", "differential", 1.0,    "V"),
]

# TTL voltage levels
TTL_HIGH_V = 5.0
TTL_LOW_V  = 0.0

# Default camera settings
DEFAULT_FRAME_RATE_HZ = 100.0   # Hz (evenly divides 20000: period = 200 samples)
DEFAULT_EXPOSURE_MS   = 5.0   # ms

# Delay (ms) before starting / after stopping camera triggering so the DAQ
# captures a clean baseline before triggers begin and records any trailing
# exposure signals after triggers end.
CAMERA_GUARD_DELAY_MS = 2000

# Default Y-axis ranges in display units for each AI channel
AI_Y_DEFAULTS = [
    (-100.0,  100.0),   # ScAmpOut  (mV)
    ( -10.0,   10.0),   # RawAmpOut (nA)
    (-500.0,  500.0),   # AmpCmd    (pA)
    (  -0.5,    5.5),   # Camera    (V)
    (  -0.5,    5.5),   # TTLLoopback (V)
]

# Trace colors (one per channel)
TRACE_COLORS = [
    "#00BFFF",   # ScAmpOut    — sky blue
    "#FF6B6B",   # RawAmpOut   — coral
    "#98FF98",   # AmpCmd      — mint green
    "#FFD700",   # Camera      — gold
    "#DA70D6",   # TTLLoopback — orchid
]

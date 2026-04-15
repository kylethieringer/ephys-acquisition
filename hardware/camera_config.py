"""
Basler camera configuration via pypylon.

Trigger mode:
    Line1 — TTL input (rising edge triggers frame capture)
    Line3 — ExposureActive output (high during exposure, for loopback monitoring)

Raises ImportError if pypylon is not installed.
Raises RuntimeError if no camera is found.
"""

try:
    from pypylon import pylon
    HAS_PYPYLON = True
except ImportError:
    HAS_PYPYLON = False


def check_camera_available() -> None:
    """
    Verify that a Basler camera can be found and opened.

    Call this before starting acquisition workers to give an early, clear
    error rather than silently running without frame capture.

    Raises:
        RuntimeError: if pypylon is not installed, no camera is detected,
                      or the camera cannot be opened (e.g. already in use
                      by another program).
    """
    if not HAS_PYPYLON:
        raise RuntimeError(
            "pypylon is not installed. Run: pip install pypylon"
        )

    tlf = pylon.TlFactory.GetInstance()
    devices = tlf.EnumerateDevices()
    if len(devices) == 0:
        raise RuntimeError(
            "No Basler camera detected. Check that the camera is plugged in "
            "and powered on."
        )

    # Open and immediately close to confirm the camera is not locked by
    # another process (e.g. Pylon Viewer or a previous crashed session).
    cam = pylon.InstantCamera(tlf.CreateFirstDevice())
    try:
        cam.Open()
    except Exception as exc:
        raise RuntimeError(
            f"Camera detected but cannot be opened — it may already be in use "
            f"by another program. Close any other software that has the camera "
            f"open and try again. ({exc})"
        ) from exc
    finally:
        try:
            cam.Close()
        except Exception:
            pass


def open_camera(exposure_ms: float = 10.0):
    """
    Open the first available Basler camera, configure hardware trigger and
    exposure, and return the (open but not grabbing) InstantCamera object.

    Args:
        exposure_ms: target exposure time in milliseconds

    Returns:
        pylon.InstantCamera (open, trigger configured, not yet grabbing)

    Raises:
        RuntimeError: if pypylon not installed or no camera found
    """
    if not HAS_PYPYLON:
        raise RuntimeError(
            "pypylon is not installed. Run: pip install pypylon"
        )

    tlf = pylon.TlFactory.GetInstance()
    devices = tlf.EnumerateDevices()
    if len(devices) == 0:
        raise RuntimeError("No Basler camera found. Check USB/GigE connection.")

    cam = pylon.InstantCamera(tlf.CreateFirstDevice())
    cam.Open()

    try:
        configure_trigger(cam)
        set_exposure(cam, exposure_ms)
        configure_line3_output(cam)
    except Exception as exc:
        cam.Close()
        raise RuntimeError(f"Camera configuration failed: {exc}") from exc

    return cam


def configure_trigger(cam) -> None:
    """Set Line1 rising-edge hardware trigger."""
    cam.TriggerSelector.Value    = "FrameStart"
    cam.TriggerMode.Value        = "On"
    cam.TriggerSource.Value      = "Line1"
    cam.TriggerActivation.Value  = "RisingEdge"
    cam.ExposureMode.Value       = "Timed"


def set_exposure(cam, exposure_ms: float) -> None:
    """Update exposure time (microseconds internally)."""
    exposure_us = exposure_ms * 1000.0
    # Clamp to camera limits
    min_us = cam.ExposureTime.Min
    max_us = cam.ExposureTime.Max
    cam.ExposureTime.Value = max(min_us, min(max_us, exposure_us))


def configure_line3_output(cam) -> None:
    """
    Configure Line3 as an ExposureActive output so the AI3 channel can
    record when the sensor is actively exposing.

    Not all Basler cameras expose Line3 as a user-configurable output.
    Failures here are non-fatal — we catch and ignore them.
    """
    try:
        cam.LineSelector.Value = "Line3"
        if cam.LineMode.Value == "Output":
            cam.LineSource.Value = "ExposureActive"
    except Exception:
        pass   # camera does not support Line3 output configuration — that's OK

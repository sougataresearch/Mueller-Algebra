"""IDS Peak camera communication: open/configure, software-triggered
acquisition, TIFF save+verify with retries, and ROI/quality helpers used
for the automatic bright/dark reference check in measure.py.

Deliberately thin like motor_communication.py: this file only knows how
to talk to the camera and judge one frame's quality. Which reference shots
to take, when, and what to do about a bad one all live in measure.py.
"""

from __future__ import annotations

import time
from pathlib import Path


class CameraError(RuntimeError):
    """Raised when the camera cannot safely open/acquire/save/verify a
    frame. ``attempts`` records how many tries were actually made."""

    def __init__(self, message: str, attempts: int = 1) -> None:
        super().__init__(message)
        self.attempts = attempts


class IDSCamera:
    """Owns one IDS Peak camera device for the run's lifetime. dry_run
    simulates a 640x480 synthetic frame (a slowly-shifting gradient, so
    repeated dry-run captures aren't bit-identical) with no IDS Peak SDK
    installed."""

    def __init__(
        self,
        dry_run: bool,
        exposure_us: float,
        frame_rate_fps: float,
        gain: float = 1.0,
        pixel_format: str = "Mono8",
        timeout_ms: int = 5_000,
        retries: int = 2,
        mean_too_dark: float = 1.0,
        mean_too_bright: float = 250.0,
    ) -> None:
        self.dry_run = dry_run
        self.exposure_us = exposure_us
        self.frame_rate_fps = frame_rate_fps
        self.gain = gain
        self.pixel_format = pixel_format
        self.timeout_ms = timeout_ms
        self.retries = retries
        self.mean_too_dark = mean_too_dark
        self.mean_too_bright = mean_too_bright

        self.ids_peak = None
        self.ids_peak_ipl = None
        self.ids_peak_ipl_extension = None
        self.device = None
        self.node_map = None
        self.data_stream = None
        self.buffers = []
        self.started = False
        self.model = ""
        self.serial_number = ""
        self.applied: dict = {}
        self._simulation_index = 0

    def _set_node(self, name: str, value) -> None:
        node = self.node_map.FindNode(name)
        if isinstance(value, str):
            node.SetCurrentEntry(value)
        else:
            node.SetValue(value)

    def _read_node(self, name: str):
        return self.node_map.FindNode(name).Value()

    def open(self) -> None:
        if self.dry_run:
            self.model = "SIMULATED IDS CAMERA"
            self.serial_number = "SIM-CAMERA"
            self.applied = {
                "exposure_us": self.exposure_us,
                "frame_rate_fps": self.frame_rate_fps,
                "gain": self.gain,
                "width": 640,
                "height": 480,
            }
            print("[dry-run] Simulated IDS camera, 640x480.")
            return

        try:
            from ids_peak import ids_peak, ids_peak_ipl_extension
            from ids_peak_ipl import ids_peak_ipl
        except Exception as exc:
            raise CameraError(f"Could not load the IDS Peak Python packages. Original error: {exc!r}") from exc

        self.ids_peak = ids_peak
        self.ids_peak_ipl = ids_peak_ipl
        self.ids_peak_ipl_extension = ids_peak_ipl_extension
        ids_peak.Library.Initialize()
        manager = ids_peak.DeviceManager.Instance()
        manager.Update()
        devices = manager.Devices()
        no_device = bool(devices.empty()) if hasattr(devices, "empty") else len(devices) == 0
        if no_device:
            ids_peak.Library.Close()
            raise CameraError("No IDS Peak camera found; close IDS Cockpit and retry.")

        self.device = devices[0].OpenDevice(ids_peak.DeviceAccessType_Control)
        self.node_map = self.device.RemoteDevice().NodeMaps()[0]
        self.model = str(self.node_map.FindNode("DeviceModelName").Value())
        self.serial_number = str(self.node_map.FindNode("DeviceSerialNumber").Value())
        streams = self.device.DataStreams()
        if not streams:
            raise CameraError("IDS camera has no available data stream.")
        self.data_stream = streams[0].OpenDataStream()

        try:
            self._set_node("UserSetSelector", "Default")
            command = self.node_map.FindNode("UserSetLoad")
            command.Execute()
            command.WaitUntilDone()
        except Exception as exc:
            print(f"Camera Default UserSet warning: {exc}")

        self._set_node("PixelFormat", self.pixel_format)
        self._set_node("ExposureTime", float(self.exposure_us))
        try:
            self._set_node("Gain", float(self.gain))
        except Exception as exc:
            print(f"Camera gain warning: {exc}")
        try:
            self._set_node("AcquisitionFrameRateEnable", True)
        except Exception:
            pass
        self._set_node("AcquisitionFrameRate", float(self.frame_rate_fps))

        try:
            applied_gain = float(self._read_node("Gain"))
        except Exception:
            applied_gain = None
        self.applied = {
            "exposure_us": float(self._read_node("ExposureTime")),
            "frame_rate_fps": float(self._read_node("AcquisitionFrameRate")),
            "gain": applied_gain,
            "width": int(self._read_node("Width")),
            "height": int(self._read_node("Height")),
        }
        self._set_node("TriggerSelector", "ExposureStart")
        self._set_node("TriggerSource", "Software")
        self._set_node("TriggerMode", "On")

        payload_size = int(self.node_map.FindNode("PayloadSize").Value())
        count = max(int(self.data_stream.NumBuffersAnnouncedMinRequired()), 3)
        for _ in range(count):
            buffer = self.data_stream.AllocAndAnnounceBuffer(payload_size)
            self.data_stream.QueueBuffer(buffer)
            self.buffers.append(buffer)
        self.node_map.FindNode("TLParamsLocked").SetValue(1)
        self.data_stream.StartAcquisition(ids_peak.AcquisitionStartMode_Default)
        start = self.node_map.FindNode("AcquisitionStart")
        start.Execute()
        start.WaitUntilDone()
        self.started = True
        print(f"Camera connected: {self.model} (S/N {self.serial_number})")
        print(
            f"Applied exposure={self.applied['exposure_us'] / 1000:.3f} ms, "
            f"frame rate={self.applied['frame_rate_fps']:.3f} fps, "
            f"size={self.applied['width']}x{self.applied['height']}"
        )

    def acquire_array(self):
        import numpy as np

        if self.dry_run:
            height, width = 480, 640
            y, x = np.indices((height, width), dtype=np.uint16)
            image = ((x + y + self._simulation_index * 7) % 220 + 15).astype(np.uint8)
            self._simulation_index += 1
            return image

        trigger = self.node_map.FindNode("TriggerSoftware")
        trigger.Execute()
        trigger.WaitUntilDone()
        buffer = self.data_stream.WaitForFinishedBuffer(int(self.timeout_ms))
        try:
            image = self.ids_peak_ipl_extension.BufferToImage(buffer)
            mono8 = image.ConvertTo(self.ids_peak_ipl.PixelFormatName_Mono8)
            return mono8.get_numpy_2D().copy()
        finally:
            self.data_stream.QueueBuffer(buffer)

    def capture_tiff(self, path: Path) -> dict[str, float | int]:
        """Acquire, save, and verify one frame, retrying on failure. Returns
        stats (min/max/mean/saturated_pixels) and prints a warning if the
        mean falls outside [mean_too_dark, mean_too_bright] -- a cheap
        per-frame sanity check independent of the bright/dark ROI
        verification measure.py runs once per sample."""

        from PIL import Image

        last_error = None
        for attempt in range(1, int(self.retries) + 2):
            try:
                image = self.acquire_array()
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(image).save(path, format="TIFF", compression="raw")
                with Image.open(path) as check:
                    check.load()
                    if check.size != (image.shape[1], image.shape[0]):
                        raise RuntimeError("TIFF size mismatch after write.")
                stats = {
                    "minimum": int(image.min()),
                    "maximum": int(image.max()),
                    "mean": float(image.mean()),
                    "saturated_pixels": int((image == 255).sum()),
                }
                print(f"  Saved {path.name}: min={stats['minimum']}, max={stats['maximum']}, mean={stats['mean']:.3f}")
                if stats["mean"] < self.mean_too_dark:
                    print(f"  WARNING: {path.name} mean {stats['mean']:.3f} is below mean_too_dark ({self.mean_too_dark}) -- frame may be black.")
                elif stats["mean"] > self.mean_too_bright:
                    print(f"  WARNING: {path.name} mean {stats['mean']:.3f} is above mean_too_bright ({self.mean_too_bright}) -- frame may be saturated.")
                return stats
            except Exception as exc:
                last_error = exc
                print(f"  Capture attempt {attempt}/{int(self.retries) + 1} failed: {exc}")
                if attempt <= int(self.retries):
                    time.sleep(1.0)
        raise CameraError(f"Capture failed: {last_error}") from last_error

    def close(self) -> None:
        if self.dry_run:
            return
        was_open = self.device is not None
        try:
            if self.started:
                try:
                    stop = self.node_map.FindNode("AcquisitionStop")
                    stop.Execute()
                    stop.WaitUntilDone()
                except Exception:
                    pass
                try:
                    self.data_stream.StopAcquisition(self.ids_peak.AcquisitionStopMode_Default)
                except Exception:
                    pass
                try:
                    self.node_map.FindNode("TLParamsLocked").SetValue(0)
                except Exception:
                    pass
                self.started = False
            if self.data_stream is not None:
                try:
                    self.data_stream.Flush(self.ids_peak.DataStreamFlushMode_DiscardAll)
                    for buffer in self.buffers:
                        self.data_stream.RevokeBuffer(buffer)
                except Exception:
                    pass
        finally:
            if self.ids_peak is not None:
                try:
                    self.ids_peak.Library.Close()
                except Exception:
                    pass
            self.buffers = []
            self.data_stream = None
            self.node_map = None
            self.device = None
            if was_open:
                print("Camera disconnected.")


def select_roi(image, window_size: int, stride: int, min_mean: float) -> tuple[int, int, int, int]:
    """Find the flattest sufficiently-bright square region in ``image``.

    Slides a window_size x window_size window across the frame with step
    ``stride``, scoring each candidate by standard deviation (lower =
    flatter). Candidates whose mean is below ``min_mean`` or that contain
    any saturated (255) pixel are rejected outright -- the winner is the
    flattest region among what remains, not the brightest or most central,
    so a genuine flat-illuminated plateau is preferred over the peak of an
    uneven beam profile. Returns (x, y, width, height).
    """

    height, width = image.shape
    best: tuple[int, int, int, int] | None = None
    best_std: float | None = None
    for y in range(0, height - window_size + 1, stride):
        for x in range(0, width - window_size + 1, stride):
            region = image[y : y + window_size, x : x + window_size]
            mean = float(region.mean())
            if mean < min_mean:
                continue
            if int((region == 255).sum()) > 0:
                continue
            std = float(region.std())
            if best_std is None or std < best_std:
                best_std = std
                best = (x, y, window_size, window_size)
    if best is None:
        raise CameraError(
            "No region met the ROI brightness/saturation criteria; "
            "check illumination or lower roi_min_mean."
        )
    return best


def roi_mean(image, roi: tuple[int, int, int, int]) -> float:
    """Mean pixel value within roi = (x, y, width, height)."""

    x, y, width, height = roi
    return float(image[y : y + height, x : x + width].mean())

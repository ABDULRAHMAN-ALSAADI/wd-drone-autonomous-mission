#!/usr/bin/env python3
"""Camera input helpers for SITL and Raspberry Pi Camera Module 3.

Every backend returns the same timestamped :class:`CameraFrame`. Raspberry Pi
missions prefer direct Picamera2 arrays so camera frames are not encoded to
MJPEG and decoded again before OpenCV sees them. The proven rpicam MJPEG source
remains available as an explicit fallback.
"""
from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

import cv2
import numpy as np


SUPPORTED_CAMERA_SOURCES = {
    "udp_h264",
    "gstreamer_pipeline",
    "device",
    "picamera2",
    "rpicam_mjpeg",
}


@dataclass(frozen=True)
class CameraFrame:
    """One complete camera frame and the timing data used for freshness gates."""

    frame_id: int
    sensor_timestamp_ns: Optional[int]
    received_monotonic_s: float
    image_bgr: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"

    @property
    def width(self) -> int:
        return int(self.image_bgr.shape[1])

    @property
    def height(self) -> int:
        return int(self.image_bgr.shape[0])

    def frame_age_s(self, now: Optional[float] = None) -> float:
        timestamp = time.monotonic() if now is None else float(now)
        return max(0.0, timestamp - self.received_monotonic_s)

    def sensor_age_s(self, now: Optional[float] = None) -> Optional[float]:
        """Return sensor-to-now age when the sensor clock matches monotonic time."""
        if not self.has_sensor_timestamp:
            return None
        timestamp = time.monotonic() if now is None else float(now)
        age = timestamp - (float(self.sensor_timestamp_ns) / 1_000_000_000.0)
        # Picamera2 SensorTimestamp normally uses the Linux monotonic clock.
        # Treat an implausible offset as a different clock domain.
        if age < -0.05 or age > 60.0:
            return None
        return max(0.0, age)

    def capture_age_s(self, now: Optional[float] = None) -> float:
        """Best available frame age, preferring the sensor timestamp."""
        sensor_age = self.sensor_age_s(now)
        return self.frame_age_s(now) if sensor_age is None else sensor_age

    @property
    def has_sensor_timestamp(self) -> bool:
        return self.sensor_timestamp_ns is not None and self.sensor_timestamp_ns > 0


class CameraLike(Protocol):
    def read_frame(self) -> Optional[CameraFrame]:
        ...

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        ...

    def release(self) -> None:
        ...

    def isOpened(self) -> bool:
        ...


class OpenCvCamera:
    """Add frame IDs and receive timestamps to an OpenCV ``VideoCapture``."""

    def __init__(self, capture: cv2.VideoCapture, source: str) -> None:
        self.capture = capture
        self.source = source
        self._frame_id = 0

    def read_frame(self) -> Optional[CameraFrame]:
        ok, image = self.capture.read()
        if not ok or image is None:
            return None
        self._frame_id += 1
        return CameraFrame(
            frame_id=self._frame_id,
            sensor_timestamp_ns=None,
            received_monotonic_s=time.monotonic(),
            image_bgr=image,
            metadata={},
            source=self.source,
        )

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        frame = self.read_frame()
        return (frame is not None, None if frame is None else frame.image_bgr)

    def release(self) -> None:
        self.capture.release()

    def isOpened(self) -> bool:
        return bool(self.capture.isOpened())


def udp_h264_pipeline(port: int) -> str:
    return (
        f'udpsrc address=0.0.0.0 port={port} '
        'caps="application/x-rtp,media=video,encoding-name=H264,payload=96" ! '
        'rtpjitterbuffer latency=70 drop-on-latency=true ! '
        'rtph264depay ! h264parse ! avdec_h264 ! '
        'videoconvert ! video/x-raw,format=BGR ! '
        'appsink drop=true max-buffers=1 sync=false'
    )


def build_rpicam_mjpeg_command(camera_config: dict[str, Any]) -> list[str]:
    width = int(camera_config.get("width", 1280))
    height = int(camera_config.get("height", 720))
    framerate = float(camera_config.get("framerate", 30))
    quality = int(camera_config.get("quality", 85))
    timeout_ms = int(camera_config.get("timeout_ms", 0))
    command = str(camera_config.get("command", "rpicam-vid"))

    args = [
        command,
        "--camera", str(int(camera_config.get("camera_index", 0))),
        "--timeout", str(timeout_ms),
        "--nopreview",
        "--width", str(width),
        "--height", str(height),
        "--framerate", f"{framerate:g}",
        "--codec", "mjpeg",
        "--quality", str(quality),
        "--flush",
        "--verbose", str(int(camera_config.get("verbose", 0))),
    ]

    autofocus_mode = camera_config.get("autofocus_mode")
    if autofocus_mode:
        args.extend(["--autofocus-mode", str(autofocus_mode)])

    lens_position = camera_config.get("lens_position")
    if lens_position is not None:
        args.extend(["--lens-position", str(lens_position)])

    denoise = camera_config.get("denoise")
    if denoise:
        args.extend(["--denoise", str(denoise)])

    if bool(camera_config.get("hflip", False)):
        args.append("--hflip")
    if bool(camera_config.get("vflip", False)):
        args.append("--vflip")
    if int(camera_config.get("rotation", 0)) != 0:
        args.extend(["--rotation", str(int(camera_config["rotation"]))])

    extra_args = camera_config.get("extra_args", [])
    if extra_args:
        if not isinstance(extra_args, list) or not all(isinstance(item, str) for item in extra_args):
            raise ValueError("camera.extra_args must be a list of strings")
        args.extend(extra_args)

    args.extend(["-o", "-"])
    return args


class RpicamMjpegCamera:
    """Read OpenCV frames from ``rpicam-vid --codec mjpeg -o -``."""

    SOI = b"\xff\xd8"
    EOI = b"\xff\xd9"

    def __init__(self, camera_config: dict[str, Any]) -> None:
        self.camera_config = camera_config
        self.read_timeout_s = float(camera_config.get("read_timeout_s", 2.0))
        self.chunk_size = int(camera_config.get("read_chunk_bytes", 65536))
        self.buffer = bytearray()
        self._frame_id = 0
        self.cmd = build_rpicam_mjpeg_command(camera_config)
        if shutil.which(self.cmd[0]) is None:
            raise RuntimeError(f"{self.cmd[0]} was not found. Install Raspberry Pi camera tools first.")
        print("[CAMERA] Starting Pi Camera Module 3 MJPEG stream")
        print("[CAMERA] " + " ".join(self.cmd[:-2] + ["-o", "<stdout>"]))
        self.process = subprocess.Popen(
            self.cmd,
            stdout=subprocess.PIPE,
            stderr=None,
            start_new_session=True,
        )
        if self.process.stdout is None:
            raise RuntimeError("Could not open rpicam-vid stdout")

    def isOpened(self) -> bool:
        return self.process.poll() is None and self.process.stdout is not None

    def _pop_latest_jpeg(self) -> Optional[bytes]:
        latest = None
        while True:
            start = self.buffer.find(self.SOI)
            if start < 0:
                if len(self.buffer) > self.chunk_size:
                    self.buffer.clear()
                return latest
            if start > 0:
                del self.buffer[:start]
            end = self.buffer.find(self.EOI, 2)
            if end < 0:
                return latest
            frame_end = end + len(self.EOI)
            latest = bytes(self.buffer[:frame_end])
            del self.buffer[:frame_end]

    def read_frame(self) -> Optional[CameraFrame]:
        deadline = time.monotonic() + self.read_timeout_s
        fd = self.process.stdout.fileno()
        while time.monotonic() < deadline:
            jpeg = self._pop_latest_jpeg()
            if jpeg is not None:
                frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    self._frame_id += 1
                    return CameraFrame(
                        frame_id=self._frame_id,
                        sensor_timestamp_ns=None,
                        received_monotonic_s=time.monotonic(),
                        image_bgr=frame,
                        metadata={"transport": "mjpeg", "jpeg_bytes": len(jpeg)},
                        source="rpicam_mjpeg",
                    )
                continue
            if self.process.poll() is not None:
                return None
            remaining = max(0.0, min(0.1, deadline - time.monotonic()))
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(fd, self.chunk_size)
            if not chunk:
                return None
            self.buffer.extend(chunk)
        return None

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        frame = self.read_frame()
        return (frame is not None, None if frame is None else frame.image_bgr)

    def release(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=2.0)
        except Exception:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except Exception:
                pass
            self.process.wait(timeout=2.0)


class Picamera2Camera:
    """Capture uncompressed Camera Module 3 arrays through Picamera2."""

    def __init__(self, camera_config: dict[str, Any]) -> None:
        try:
            from libcamera import controls
            from picamera2 import Picamera2
        except ImportError as exc:
            raise RuntimeError(
                "Picamera2 is unavailable. Install the Raspberry Pi OS "
                "python3-picamera2 package or configure rpicam_mjpeg fallback."
            ) from exc

        self.camera_config = camera_config
        self._frame_id = 0
        self._closed = False
        self._controls_module = controls
        camera_index = int(camera_config.get("camera_index", 0))
        width = int(camera_config.get("width", 1280))
        height = int(camera_config.get("height", 720))
        framerate = float(camera_config.get("framerate", 30.0))
        pixel_format = str(camera_config.get("pixel_format", "RGB888"))
        self.array_color_order = str(
            camera_config.get("array_color_order", "BGR")
        ).upper()
        if self.array_color_order not in {"BGR", "RGB"}:
            raise ValueError("camera.array_color_order must be BGR or RGB")

        self.camera = Picamera2(camera_index)
        controls_config = self._camera_controls(camera_config, framerate)
        video_config = self.camera.create_video_configuration(
            main={"size": (width, height), "format": pixel_format},
            buffer_count=max(2, int(camera_config.get("buffer_count", 4))),
            controls=controls_config,
        )
        self.camera.configure(video_config)
        self.camera.start()
        warmup_s = max(0.0, float(camera_config.get("warmup_s", 1.0)))
        if warmup_s:
            time.sleep(warmup_s)
        print(
            f"[CAMERA] Picamera2 direct arrays camera={camera_index} "
            f"{width}x{height}@{framerate:g} format={pixel_format}"
        )

    def _camera_controls(self, camera_config: dict[str, Any], framerate: float) -> dict[str, Any]:
        values: dict[str, Any] = {}
        frame_duration_us = max(1, round(1_000_000.0 / framerate))
        values["FrameDurationLimits"] = (frame_duration_us, frame_duration_us)

        autofocus_mode = str(camera_config.get("autofocus_mode", "manual")).lower()
        autofocus_modes = {
            "manual": self._controls_module.AfModeEnum.Manual,
            "auto": self._controls_module.AfModeEnum.Auto,
            "continuous": self._controls_module.AfModeEnum.Continuous,
        }
        if autofocus_mode not in autofocus_modes:
            raise ValueError("camera.autofocus_mode must be manual, auto, or continuous")
        values["AfMode"] = autofocus_modes[autofocus_mode]
        lens_position = camera_config.get("lens_position")
        if autofocus_mode == "manual" and lens_position is not None:
            values["LensPosition"] = float(lens_position)

        exposure_time_us = camera_config.get("exposure_time_us")
        analogue_gain = camera_config.get("analogue_gain")
        if exposure_time_us is not None:
            values["AeEnable"] = False
            values["ExposureTime"] = int(exposure_time_us)
        if analogue_gain is not None:
            values["AeEnable"] = False
            values["AnalogueGain"] = float(analogue_gain)

        colour_gains = camera_config.get("colour_gains")
        if colour_gains is not None:
            if not isinstance(colour_gains, list) or len(colour_gains) != 2:
                raise ValueError("camera.colour_gains must be [red_gain, blue_gain] or null")
            values["AwbEnable"] = False
            values["ColourGains"] = tuple(float(value) for value in colour_gains)

        if camera_config.get("sharpness") is not None:
            values["Sharpness"] = float(camera_config["sharpness"])
        if camera_config.get("brightness") is not None:
            values["Brightness"] = float(camera_config["brightness"])
        if camera_config.get("contrast") is not None:
            values["Contrast"] = float(camera_config["contrast"])
        denoise = camera_config.get("denoise")
        if denoise is not None:
            draft_controls = getattr(self._controls_module, "draft", None)
            enum = getattr(draft_controls, "NoiseReductionModeEnum", None)
            if enum is not None:
                denoise_modes = {
                    "off": enum.Off,
                    "minimal": enum.Minimal,
                    "fast": enum.Fast,
                    "high_quality": enum.HighQuality,
                }
                denoise_name = str(denoise).lower()
                if denoise_name not in denoise_modes:
                    raise ValueError(
                        "camera.denoise must be off, minimal, fast, or high_quality"
                    )
                values["NoiseReductionMode"] = denoise_modes[denoise_name]
        return values

    def isOpened(self) -> bool:
        return not self._closed

    def read_frame(self) -> Optional[CameraFrame]:
        if self._closed:
            return None
        request = self.camera.capture_request()
        try:
            image = request.make_array("main")
            metadata = dict(request.get_metadata())
        finally:
            request.release()
        if image is None:
            return None
        if self.array_color_order == "RGB":
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        self._frame_id += 1
        sensor_timestamp = metadata.get("SensorTimestamp")
        return CameraFrame(
            frame_id=self._frame_id,
            sensor_timestamp_ns=None if sensor_timestamp is None else int(sensor_timestamp),
            received_monotonic_s=time.monotonic(),
            image_bgr=image,
            metadata=metadata,
            source="picamera2",
        )

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        frame = self.read_frame()
        return (frame is not None, None if frame is None else frame.image_bgr)

    def release(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.camera.stop()
        finally:
            self.camera.close()


def open_camera(camera_config: dict[str, Any]) -> CameraLike:
    source = camera_config.get("source", "udp_h264")
    if source == "udp_h264":
        description = f"UDP H264 port {int(camera_config['udp_port'])}"
        cap = cv2.VideoCapture(udp_h264_pipeline(int(camera_config["udp_port"])), cv2.CAP_GSTREAMER)
    elif source == "gstreamer_pipeline":
        description = "custom GStreamer pipeline"
        cap = cv2.VideoCapture(str(camera_config["pipeline"]), cv2.CAP_GSTREAMER)
    elif source == "device":
        description = f"camera device {int(camera_config.get('device_index', 0))}"
        cap = cv2.VideoCapture(int(camera_config.get("device_index", 0)))
    elif source == "picamera2":
        try:
            return Picamera2Camera(camera_config)
        except RuntimeError:
            fallback_source = camera_config.get("fallback_source")
            if fallback_source != "rpicam_mjpeg":
                raise
            print("[CAMERA WARNING] Picamera2 unavailable; using configured rpicam MJPEG fallback")
            fallback_config = dict(camera_config)
            fallback_config["source"] = "rpicam_mjpeg"
            return RpicamMjpegCamera(fallback_config)
    elif source == "rpicam_mjpeg":
        return RpicamMjpegCamera(camera_config)
    else:
        raise RuntimeError(f"Unsupported camera source: {source}")
    if not cap.isOpened():
        raise RuntimeError(f"Camera source did not open: {description}")
    return OpenCvCamera(cap, source)

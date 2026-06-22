#!/usr/bin/env python3
"""Camera input helpers for SITL and Raspberry Pi Camera Module 3.

The mission only needs a small camera interface: ``read()`` returns the next
OpenCV BGR frame and ``release()`` stops the stream. SITL uses OpenCV/GStreamer
directly. The real Raspberry Pi profile uses ``rpicam-vid`` with MJPEG because
that path works on Pi OS Lite even when H.264 encoding is not available.
"""
from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import time
from typing import Any, Optional, Protocol

import cv2
import numpy as np


SUPPORTED_CAMERA_SOURCES = {"udp_h264", "gstreamer_pipeline", "device", "rpicam_mjpeg"}


class CameraLike(Protocol):
    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        ...

    def release(self) -> None:
        ...

    def isOpened(self) -> bool:
        ...


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

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        deadline = time.monotonic() + self.read_timeout_s
        fd = self.process.stdout.fileno()
        while time.monotonic() < deadline:
            jpeg = self._pop_latest_jpeg()
            if jpeg is not None:
                frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    return True, frame
                continue
            if self.process.poll() is not None:
                return False, None
            remaining = max(0.0, min(0.1, deadline - time.monotonic()))
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(fd, self.chunk_size)
            if not chunk:
                return False, None
            self.buffer.extend(chunk)
        return False, None

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
    elif source == "rpicam_mjpeg":
        return RpicamMjpegCamera(camera_config)
    else:
        raise RuntimeError(f"Unsupported camera source: {source}")
    if not cap.isOpened():
        raise RuntimeError(f"Camera source did not open: {description}")
    return cap

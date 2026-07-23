#!/usr/bin/env python3
"""Shared, camera-only helpers for OpenCV diagnostics and tuning tools."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
MISSION_DIR = ROOT / "target_mission_v2"
if str(MISSION_DIR) not in sys.path:
    sys.path.insert(0, str(MISSION_DIR))

from camera_sources import CameraFrame, CameraLike, open_camera  # noqa: E402
from configuration import load_config  # noqa: E402


def load_profile(path: Path | str) -> dict[str, Any]:
    config = load_config(path)
    if "camera" not in config or "vision" not in config:
        raise ValueError("profile must contain camera and vision sections")
    return config


def resize_width(image: np.ndarray, width: int) -> np.ndarray:
    if width <= 0 or image.shape[1] == width:
        return image
    scale = width / image.shape[1]
    return cv2.resize(
        image,
        (width, max(1, round(image.shape[0] * scale))),
        interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR,
    )


def draw_panel(
    image: np.ndarray,
    lines: list[str],
    scale: float = 0.48,
    alpha: float = 0.48,
) -> np.ndarray:
    output = image.copy()
    max_width = max(120, output.shape[1] - 42)
    fitted: list[str] = []
    for line in lines:
        value = line
        while (
            len(value) > 4
            and cv2.getTextSize(
                value + "...", cv2.FONT_HERSHEY_SIMPLEX, scale, 1
            )[0][0]
            > max_width
        ):
            value = value[:-1]
        fitted.append(value if value == line else value + "...")
    line_height = max(17, round(38 * scale))
    panel_width = min(
        output.shape[1] - 16,
        max(
            cv2.getTextSize(
                line, cv2.FONT_HERSHEY_SIMPLEX, scale, 1
            )[0][0]
            for line in fitted
        )
        + 20,
    )
    panel_height = 12 + line_height * len(fitted)
    panel = output.copy()
    cv2.rectangle(
        panel,
        (8, 8),
        (8 + panel_width, 8 + panel_height),
        (0, 0, 0),
        -1,
    )
    cv2.addWeighted(panel, alpha, output, 1.0 - alpha, 0, output)
    y = 28
    for line in fitted:
        cv2.putText(
            output,
            line,
            (18, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        y += line_height
    return output


def vcgencmd(argument: str) -> str:
    if shutil.which("vcgencmd") is None:
        return "unavailable"
    try:
        result = subprocess.run(
            ["vcgencmd", argument],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error:{exc}"
    return (result.stdout or result.stderr).strip() or "unavailable"


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class LatestCameraReader:
    """Continuously capture while retaining only the newest complete frame."""

    def __init__(self, camera: CameraLike) -> None:
        self.camera = camera
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest: Optional[CameraFrame] = None
        self._sequence = 0
        self._last_read_sequence = 0
        self.captured = 0
        self.overwritten = 0
        self.failure: Optional[str] = None

    @classmethod
    def from_config(cls, camera_config: dict[str, Any]) -> "LatestCameraReader":
        return cls(open_camera(camera_config))

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("camera reader already started")
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="opencv-tool-camera",
            daemon=True,
        )
        self._thread.start()

    def _capture_loop(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self.camera.read_frame()
            except Exception as exc:
                self.failure = str(exc)
                return
            if frame is None:
                time.sleep(0.005)
                continue
            with self._lock:
                if self._sequence > self._last_read_sequence:
                    self.overwritten += 1
                self._sequence += 1
                self.captured += 1
                self._latest = CameraFrame(
                    frame_id=self._sequence,
                    sensor_timestamp_ns=frame.sensor_timestamp_ns,
                    received_monotonic_s=frame.received_monotonic_s,
                    image_bgr=frame.image_bgr,
                    metadata=frame.metadata,
                    source=frame.source,
                )

    def latest(self) -> Optional[CameraFrame]:
        with self._lock:
            self._last_read_sequence = self._sequence
            return self._latest

    def metrics(self) -> dict[str, int]:
        with self._lock:
            return {
                "captured": self.captured,
                "overwritten": self.overwritten,
                "latest_frame_id": self._sequence,
            }

    def stop(self) -> None:
        self._stop.set()
        self.camera.release()
        if self._thread is not None:
            self._thread.join(timeout=3.0)


class LatestJpegServer:
    """Encode and serve only the latest submitted frame on a worker thread."""

    def __init__(
        self,
        bind: str = "127.0.0.1",
        port: int = 5602,
        fps: float = 8.0,
        quality: int = 82,
        width: int = 854,
    ) -> None:
        self.bind = bind
        self.port = int(port)
        self.fps = float(fps)
        self.quality = int(quality)
        self.width = int(width)
        self._condition = threading.Condition()
        self._raw: Optional[np.ndarray] = None
        self._raw_sequence = 0
        self._consumed_sequence = 0
        self._jpeg: Optional[bytes] = None
        self._jpeg_sequence = 0
        self._stopping = False
        self._server: Optional[ThreadingHTTPServer] = None
        self._encoder: Optional[threading.Thread] = None
        self._http: Optional[threading.Thread] = None
        self.submitted = 0
        self.encoded = 0
        self.overwritten = 0

    def start(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path not in {"/", "/stream.mjpg"}:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "multipart/x-mixed-replace; boundary=frame",
                )
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                sequence = -1
                try:
                    while True:
                        with owner._condition:
                            owner._condition.wait_for(
                                lambda: owner._stopping
                                or (
                                    owner._jpeg is not None
                                    and owner._jpeg_sequence != sequence
                                ),
                                timeout=2.0,
                            )
                            if owner._stopping:
                                return
                            if (
                                owner._jpeg is None
                                or owner._jpeg_sequence == sequence
                            ):
                                continue
                            jpeg = owner._jpeg
                            sequence = owner._jpeg_sequence
                        self.wfile.write(
                            b"--frame\r\nContent-Type: image/jpeg\r\n"
                        )
                        self.wfile.write(
                            f"Content-Length: {len(jpeg)}\r\n\r\n".encode(
                                "ascii"
                            )
                        )
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    return

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer((self.bind, self.port), Handler)
        self._server.daemon_threads = True
        self._encoder = threading.Thread(
            target=self._encode_loop,
            name="opencv-tool-jpeg",
            daemon=True,
        )
        self._http = threading.Thread(
            target=self._server.serve_forever,
            name="opencv-tool-http",
            daemon=True,
        )
        self._encoder.start()
        self._http.start()

    def publish(self, image: np.ndarray) -> None:
        with self._condition:
            if self._raw_sequence > self._consumed_sequence:
                self.overwritten += 1
            self._raw = image
            self._raw_sequence += 1
            self.submitted += 1
            self._condition.notify_all()

    def _encode_loop(self) -> None:
        interval_s = 1.0 / max(0.1, self.fps)
        last_encoded_at = 0.0
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._stopping
                    or self._raw_sequence != self._consumed_sequence,
                    timeout=1.0,
                )
                if self._stopping:
                    return
                if self._raw is None:
                    continue
                image = self._raw
                self._consumed_sequence = self._raw_sequence
            delay = interval_s - (time.monotonic() - last_encoded_at)
            if delay > 0:
                time.sleep(delay)
            output = resize_width(image, self.width)
            ok, encoded = cv2.imencode(
                ".jpg",
                output,
                [int(cv2.IMWRITE_JPEG_QUALITY), self.quality],
            )
            last_encoded_at = time.monotonic()
            if not ok:
                continue
            with self._condition:
                self._jpeg = encoded.tobytes()
                self._jpeg_sequence += 1
                self.encoded += 1
                self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._encoder is not None:
            self._encoder.join(timeout=2.0)
        if self._http is not None:
            self._http.join(timeout=2.0)

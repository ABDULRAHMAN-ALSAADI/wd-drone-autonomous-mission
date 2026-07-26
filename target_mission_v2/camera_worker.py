"""Background camera capture and latest-frame storage."""
from __future__ import annotations

import threading
import time
from typing import Optional

from camera_sources import CameraFrame, CameraLike


class LatestFrameCamera:
    """Capture in the background so camera stalls do not block MAVLink polling."""

    def __init__(self, camera: CameraLike) -> None:
        self.camera = camera
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._frame: Optional[CameraFrame] = None
        self._sequence = 0
        self._last_delivered_sequence = 0
        self.captured_count = 0
        self.overwritten_count = 0
        self._failed = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="camera-capture", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                read_frame = getattr(self.camera, "read_frame", None)
                if callable(read_frame):
                    camera_frame = read_frame()
                else:
                    ok, image = self.camera.read()
                    camera_frame = None if not ok or image is None else CameraFrame(
                        frame_id=self._sequence + 1,
                        sensor_timestamp_ns=None,
                        received_monotonic_s=time.monotonic(),
                        image_bgr=image,
                        metadata={},
                        source=type(self.camera).__name__,
                    )
            except Exception as exc:
                print(f"[CAMERA ERROR] {exc}")
                self._failed = True
                return
            if camera_frame is None:
                time.sleep(0.01)
                continue
            with self._lock:
                if self._frame is not None and self._sequence > self._last_delivered_sequence:
                    self.overwritten_count += 1
                self._sequence += 1
                self.captured_count += 1
                self._frame = CameraFrame(
                    frame_id=self._sequence,
                    sensor_timestamp_ns=camera_frame.sensor_timestamp_ns,
                    received_monotonic_s=camera_frame.received_monotonic_s,
                    image_bgr=camera_frame.image_bgr,
                    metadata=camera_frame.metadata,
                    source=camera_frame.source,
                )

    def latest(self) -> Optional[CameraFrame]:
        with self._lock:
            self._last_delivered_sequence = self._sequence
            return self._frame

    def metrics(self) -> dict[str, int]:
        with self._lock:
            return {
                "captured": self.captured_count,
                "overwritten": self.overwritten_count,
                "latest_frame_id": self._sequence,
            }

    @property
    def failed(self) -> bool:
        return self._failed

    def stop(self) -> None:
        self._stop.set()
        self.camera.release()
        if self._thread is not None:
            self._thread.join(timeout=2.5)

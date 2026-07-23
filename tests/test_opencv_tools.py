#!/usr/bin/env python3
"""Tests for the camera-only OpenCV support tools."""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "target_mission_v2", ROOT / "tools"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from camera_sources import CameraFrame  # noqa: E402
from opencv_common import LatestCameraReader  # noqa: E402
from opencv_live_test import desired_point, scale_detection  # noqa: E402
from test_opencv_refactor import detection  # noqa: E402


class FakeCamera:
    """Generate frames until released, without buffering any images."""

    def __init__(self, interval_s: float = 0.001) -> None:
        self.interval_s = interval_s
        self.released = threading.Event()
        self.frame_id = 0

    def read_frame(self):
        if self.released.is_set():
            return None
        time.sleep(self.interval_s)
        self.frame_id += 1
        return CameraFrame(
            frame_id=self.frame_id,
            sensor_timestamp_ns=None,
            received_monotonic_s=time.monotonic(),
            image_bgr=np.full((12, 16, 3), self.frame_id % 255, np.uint8),
            source="fake",
        )

    def read(self):
        frame = self.read_frame()
        return frame is not None, None if frame is None else frame.image_bgr

    def release(self) -> None:
        self.released.set()

    def isOpened(self) -> bool:
        return not self.released.is_set()


class LatestCameraReaderTests(unittest.TestCase):
    def test_reader_keeps_only_latest_frame_and_counts_overwrites(self):
        reader = LatestCameraReader(FakeCamera())
        reader.start()
        try:
            deadline = time.monotonic() + 1.0
            while reader.metrics()["captured"] < 8 and time.monotonic() < deadline:
                time.sleep(0.003)
            frame = reader.latest()
            metrics = reader.metrics()
            self.assertIsNotNone(frame)
            self.assertGreaterEqual(metrics["captured"], 8)
            self.assertGreater(metrics["overwritten"], 0)
            self.assertEqual(frame.frame_id, metrics["latest_frame_id"])
            self.assertFalse(hasattr(reader, "queue"))
        finally:
            reader.stop()

    def test_reader_stops_camera_and_thread(self):
        camera = FakeCamera()
        reader = LatestCameraReader(camera)
        reader.start()
        time.sleep(0.01)
        reader.stop()
        self.assertTrue(camera.released.is_set())
        self.assertIsNotNone(reader._thread)
        self.assertFalse(reader._thread.is_alive())


class CoordinateMappingTests(unittest.TestCase):
    def test_detection_coordinates_map_between_resolutions(self):
        item = detection(
            "red_triangle",
            3,
            center=(160, 90),
            area=1200.0,
            bbox=(120, 60, 80, 60),
        )
        mapped = scale_detection(item, (180, 320, 3), (720, 1280, 3))
        self.assertEqual((mapped.center_x, mapped.center_y), (640, 360))
        self.assertEqual(
            (mapped.bbox_x, mapped.bbox_y, mapped.bbox_w, mapped.bbox_h),
            (480, 240, 320, 240),
        )
        self.assertEqual(mapped.area_px, 19200.0)

    def test_desired_point_prefers_pixels_then_normalized_values(self):
        self.assertEqual(
            desired_point(
                {
                    "desired_drop_pixel_x": 700,
                    "desired_drop_pixel_y": 330,
                    "desired_drop_x_normalized": 0.5,
                    "desired_drop_y_normalized": 0.5,
                },
                1280,
                720,
            ),
            (700, 330),
        )
        self.assertEqual(
            desired_point(
                {
                    "desired_drop_pixel_x": None,
                    "desired_drop_pixel_y": None,
                    "desired_drop_x_normalized": 0.4,
                    "desired_drop_y_normalized": 0.6,
                },
                1000,
                500,
            ),
            (400, 300),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

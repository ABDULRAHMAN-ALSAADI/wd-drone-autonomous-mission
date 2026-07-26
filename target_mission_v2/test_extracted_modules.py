#!/usr/bin/env python3
"""Hardware-free tests for the extracted mission support modules."""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from camera_sources import CameraFrame  # noqa: E402
from camera_worker import LatestFrameCamera  # noqa: E402
from opencv_live_test import primary_rejection  # noqa: E402
from video_stream import MjpegFrameServer  # noqa: E402
import video_stream  # noqa: E402


def camera_frame(frame_id: int, value: int) -> CameraFrame:
    return CameraFrame(
        frame_id=frame_id,
        sensor_timestamp_ns=None,
        received_monotonic_s=float(frame_id),
        image_bgr=np.full((4, 6, 3), value, dtype=np.uint8),
        metadata={"frame": frame_id},
        source="sequence-camera",
    )


class SequenceCamera:
    """Deliver a finite sequence, then block until the reader releases it."""

    def __init__(self, frames: list[CameraFrame]) -> None:
        self.frames = list(frames)
        self.exhausted = threading.Event()
        self.released = threading.Event()

    def read_frame(self) -> CameraFrame | None:
        if self.frames:
            return self.frames.pop(0)
        self.exhausted.set()
        self.released.wait(timeout=2.0)
        return None

    def release(self) -> None:
        self.released.set()


class LatestFrameCameraTests(unittest.TestCase):
    def test_keeps_only_latest_frame_and_stops_cleanly(self) -> None:
        camera = SequenceCamera(
            [
                camera_frame(101, 10),
                camera_frame(102, 20),
                camera_frame(103, 30),
            ]
        )
        worker = LatestFrameCamera(camera)
        worker.start()
        try:
            self.assertTrue(camera.exhausted.wait(timeout=1.0))
            latest = worker.latest()
            metrics = worker.metrics()

            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.frame_id, 3)
            self.assertEqual(latest.source, "sequence-camera")
            self.assertEqual(int(latest.image_bgr[0, 0, 0]), 30)
            self.assertEqual(
                metrics,
                {"captured": 3, "overwritten": 2, "latest_frame_id": 3},
            )
        finally:
            worker.stop()

        self.assertTrue(camera.released.is_set())
        self.assertIsNotNone(worker._thread)
        assert worker._thread is not None
        self.assertFalse(worker._thread.is_alive())


class MjpegFrameServerTests(unittest.TestCase):
    def test_encoder_consumes_latest_frame_without_opening_port(self) -> None:
        server = MjpegFrameServer(
            {
                "mjpeg_stream_enabled": True,
                "mjpeg_stream_fps": 1_000_000,
                "mjpeg_stream_width": 64,
            }
        )
        encoded_inputs: list[np.ndarray] = []

        def fake_imencode(_extension, frame, _parameters):
            encoded_inputs.append(frame.copy())
            return True, np.frombuffer(b"jpeg-data", dtype=np.uint8)

        first = np.full((4, 6, 3), 10, dtype=np.uint8)
        second = np.full((4, 6, 3), 20, dtype=np.uint8)

        with patch.object(video_stream.cv2, "imencode", side_effect=fake_imencode):
            server.publish(first)
            server.publish(second)
            self.assertEqual(
                server.metrics(),
                {"submitted": 2, "encoded": 0, "overwritten": 1},
            )

            encoder = threading.Thread(target=server._encode_loop)
            server._encoder_thread = encoder
            encoder.start()
            try:
                with server._condition:
                    encoded = server._condition.wait_for(
                        lambda: server._encoded_count == 1,
                        timeout=1.0,
                    )
                self.assertTrue(encoded)
            finally:
                server.stop()

        self.assertIsNone(server._server)
        self.assertFalse(encoder.is_alive())
        self.assertEqual(server._jpeg, b"jpeg-data")
        self.assertEqual(
            server.metrics(),
            {"submitted": 2, "encoded": 1, "overwritten": 1},
        )
        self.assertEqual(len(encoded_inputs), 1)
        np.testing.assert_array_equal(encoded_inputs[0], second)


class PrimaryRejectionTests(unittest.TestCase):
    class Detector:
        def __init__(self, candidates) -> None:
            self.last_candidates = candidates

    def test_returns_none_without_rejected_regions(self) -> None:
        detector = self.Detector(
            [{"status": "confirmed_geometry", "bbox": [0, 0, 100, 100]}]
        )
        self.assertEqual(primary_rejection(detector), "none")

    def test_reports_largest_rejected_region_with_helpful_reason(self) -> None:
        detector = self.Detector(
            [
                {
                    "status": "rejected",
                    "target": "red_triangle",
                    "reason": "small_noise",
                    "bbox": [0, 0, 4, 4],
                },
                {
                    "status": "confirmed_geometry",
                    "target": "red_triangle",
                    "bbox": [0, 0, 200, 200],
                },
                {
                    "status": "rejected",
                    "target": "blue_hexagon",
                    "reason": "partially_visible",
                    "bbox": [10, 10, 20, 12],
                },
            ]
        )
        self.assertEqual(
            primary_rejection(detector),
            "blue_hexagon: partially visible - keep the whole shape inside frame",
        )


if __name__ == "__main__":
    unittest.main()

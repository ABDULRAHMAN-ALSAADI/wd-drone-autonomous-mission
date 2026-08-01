#!/usr/bin/env python3
"""Tests for the OpenCV camera and servo bench support tools."""
from __future__ import annotations

import sys
import threading
import time
import unittest
from collections import deque
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "target_mission_v2", ROOT / "tools"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from camera_sources import CameraFrame  # noqa: E402
from opencv_common import LatestCameraReader  # noqa: E402
from opencv_live_test import desired_point, scale_detection  # noqa: E402
from mission_stream_view import pop_latest_jpeg, recent_fps  # noqa: E402
from opencv_servo_bench_test import (  # noqa: E402
    ACCEPTED,
    SERVO_COMMAND,
    SERVO_FAULT,
    SERVO_HOLD,
    SERVO_IDLE,
    SERVO_WAIT_RELEASE_ACK,
    SERVO_WAIT_RESET_ACK,
    ServoSequence,
    neutral_commands_from_settings,
    read_vehicle_updates,
    servo_settings_from_config,
    targets_for_run,
)
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


class FakeCommandAck:
    def __init__(self, source_system=1, source_component=1):
        self.command = SERVO_COMMAND
        self.result = 0
        self.target_system = 245
        self.target_component = 192
        self.source_system = source_system
        self.source_component = source_component

    def get_type(self):
        return "COMMAND_ACK"

    def get_srcSystem(self):
        return self.source_system

    def get_srcComponent(self):
        return self.source_component


class FakeMavlinkMaster:
    target_system = 1
    target_component = 1
    source_system = 245
    source_component = 192

    def __init__(self, messages):
        self.messages = list(messages)

    def recv_match(self, blocking=False):
        return self.messages.pop(0) if self.messages else None


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


class MissionStreamViewerTests(unittest.TestCase):
    def test_newest_complete_jpeg_wins_and_incomplete_data_is_retained(self):
        first = b"\xff\xd8old\xff\xd9"
        newest = b"\xff\xd8new\xff\xd9"
        incomplete = b"--frame\r\n\xff\xd8partial"
        buffer = bytearray(b"header" + first + b"boundary" + newest + incomplete)

        self.assertEqual(pop_latest_jpeg(buffer), newest)
        self.assertEqual(buffer, b"\xff\xd8partial")

    def test_recent_fps_uses_frame_arrival_times(self):
        self.assertAlmostEqual(recent_fps(deque([10.0, 10.1, 10.2])), 10.0)


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


class ServoProfileTests(unittest.TestCase):
    def test_default_bench_mode_accepts_both_targets(self):
        self.assertEqual(
            targets_for_run("both"),
            {"red_triangle", "blue_hexagon"},
        )

    def test_shared_selector_has_one_neutral_initialization(self):
        settings = {
            "red_triangle": {
                "servo_channel": 5,
                "reset_pwm": 1500,
                "ack_timeout_s": 2.0,
            },
            "blue_hexagon": {
                "servo_channel": 5,
                "reset_pwm": 1500,
                "ack_timeout_s": 2.0,
            },
        }
        self.assertEqual(
            neutral_commands_from_settings(settings),
            [(5, 1500, 2.0)],
        )

    def test_conflicting_neutral_positions_are_rejected(self):
        settings = {
            "red_triangle": {
                "servo_channel": 5,
                "reset_pwm": 1500,
                "ack_timeout_s": 2.0,
            },
            "blue_hexagon": {
                "servo_channel": 5,
                "reset_pwm": 1400,
                "ack_timeout_s": 2.0,
            },
        }
        with self.assertRaises(ValueError):
            neutral_commands_from_settings(settings)

    def test_bench_servo_settings_follow_target_mapping(self):
        config = {
            "payload": {
                "mechanism": "selector_servo",
                "servo_channel": 5,
                "blue_payload_pwm": 1300,
                "red_payload_pwm": 1700,
                "neutral_pwm": 1500,
                "release_hold_s": 3.0,
                "command_ack_timeout_s": 2.0,
            }
        }
        self.assertEqual(
            servo_settings_from_config(config, "red_triangle"),
            {
                "servo_channel": 5,
                "release_pwm": 1300,
                "reset_pwm": 1500,
                "release_hold_s": 3.0,
                "ack_timeout_s": 2.0,
            },
        )
        self.assertEqual(
            servo_settings_from_config(config, "blue_hexagon"),
            {
                "servo_channel": 5,
                "release_pwm": 1700,
                "reset_pwm": 1500,
                "release_hold_s": 3.0,
                "ack_timeout_s": 2.0,
            },
        )


class ServoSequenceTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "servo_channel": 5,
            "release_pwm": 1300,
            "reset_pwm": 1500,
            "release_hold_s": 3.0,
            "ack_timeout_s": 2.0,
        }

    def test_release_hold_and_reset_are_non_blocking(self):
        sequence = ServoSequence()
        sequence.begin_release("red_triangle", self.settings, now=10.0)
        self.assertEqual(sequence.phase, SERVO_WAIT_RELEASE_ACK)
        self.assertEqual(sequence.hold_until, 13.0)

        self.assertIsNone(sequence.handle_ack(ACCEPTED, now=10.2))
        self.assertEqual(sequence.phase, SERVO_HOLD)
        self.assertEqual(sequence.hold_remaining_s(now=12.0), 1.0)

        sequence.begin_reset(now=13.0)
        self.assertEqual(sequence.phase, SERVO_WAIT_RESET_ACK)
        self.assertEqual(
            sequence.handle_ack(ACCEPTED, now=13.2),
            "red_triangle",
        )
        self.assertEqual(sequence.phase, SERVO_IDLE)

    def test_release_hold_is_anchored_to_command_send_time(self):
        sequence = ServoSequence()
        sequence.begin_release("red_triangle", self.settings, now=10.0)
        sequence.handle_ack(ACCEPTED, now=12.5)
        self.assertEqual(sequence.hold_remaining_s(now=12.5), 0.5)

    def test_in_progress_ack_keeps_waiting(self):
        sequence = ServoSequence()
        sequence.begin_release("red_triangle", self.settings, now=10.0)
        self.assertIsNone(
            sequence.handle_ack("MAV_RESULT_IN_PROGRESS", now=10.5)
        )
        self.assertEqual(sequence.phase, SERVO_WAIT_RELEASE_ACK)

    def test_rejected_ack_latches_fault(self):
        sequence = ServoSequence()
        sequence.begin_release("red_triangle", self.settings, now=10.0)
        self.assertIsNone(
            sequence.handle_ack("MAV_RESULT_DENIED", now=10.1)
        )
        self.assertEqual(sequence.phase, SERVO_FAULT)
        self.assertIn("rejected", sequence.fault_reason)

    def test_ack_timeout_latches_fault(self):
        sequence = ServoSequence()
        sequence.begin_release("red_triangle", self.settings, now=10.0)
        reason = sequence.check_ack_timeout(now=12.1)
        self.assertIn("ACK timeout", reason)
        self.assertEqual(sequence.phase, SERVO_FAULT)

    def test_fault_neutral_retries_until_acknowledged(self):
        sequence = ServoSequence()
        sequence.begin_release("red_triangle", self.settings, now=10.0)
        sequence.fail("test fault")
        self.assertTrue(sequence.fault_neutral_due(now=10.0))

        sequence.note_fault_neutral_attempt(now=10.0)
        self.assertFalse(sequence.fault_neutral_due(now=10.5))
        self.assertTrue(sequence.fault_neutral_due(now=11.0))

        sequence.handle_ack(ACCEPTED, now=11.1)
        self.assertTrue(sequence.fault_neutral_confirmed)
        self.assertFalse(sequence.fault_neutral_due(now=20.0))

    def test_dispatcher_keeps_only_ack_from_target_autopilot(self):
        master = FakeMavlinkMaster(
            [
                FakeCommandAck(source_system=42),
                FakeCommandAck(),
            ]
        )
        heartbeat, acknowledgements = read_vehicle_updates(master)
        self.assertIsNone(heartbeat)
        self.assertEqual(acknowledgements, [ACCEPTED])


if __name__ == "__main__":
    unittest.main(verbosity=2)

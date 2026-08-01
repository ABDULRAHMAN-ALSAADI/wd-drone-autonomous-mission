#!/usr/bin/env python3
"""Focused regression tests for the low-latency OpenCV refactor."""
from __future__ import annotations

import time
import unittest

import cv2
import numpy as np

from camera_sources import CameraFrame, Picamera2Camera
from vision import Detection, HitTracker, StrictShapeDetector


def detection(
    target: str,
    frame_id: int,
    center: tuple[int, int] = (320, 240),
    area: float = 5000.0,
    bbox: tuple[int, int, int, int] = (270, 190, 100, 100),
) -> Detection:
    vertices = 3 if target == "red_triangle" else 6
    return Detection(
        target=target,
        center_x=center[0],
        center_y=center[1],
        area_px=area,
        confidence=0.9,
        vertices=vertices,
        triangle_votes=4 if target == "red_triangle" else 0,
        four_corner_votes=0,
        hexagon_votes=4 if target == "blue_hexagon" else 0,
        extent=0.7,
        circularity=0.7,
        solidity=0.95,
        bbox_x=bbox[0],
        bbox_y=bbox[1],
        bbox_w=bbox[2],
        bbox_h=bbox[3],
        frame_id=frame_id,
        colour_score=0.95,
        shape_score=0.90,
        total_score=0.92,
    )


class CameraFrameTests(unittest.TestCase):
    def test_sensor_age_is_preferred_when_clock_is_plausible(self):
        now = time.monotonic()
        frame = CameraFrame(
            frame_id=1,
            sensor_timestamp_ns=round((now - 0.12) * 1_000_000_000),
            received_monotonic_s=now - 0.02,
            image_bgr=np.zeros((8, 10, 3), np.uint8),
        )
        self.assertAlmostEqual(frame.capture_age_s(now), 0.12, places=2)
        self.assertEqual((frame.width, frame.height), (10, 8))

    def test_implausible_sensor_clock_falls_back_to_receive_age(self):
        now = time.monotonic()
        frame = CameraFrame(
            frame_id=1,
            sensor_timestamp_ns=1,
            received_monotonic_s=now - 0.03,
            image_bgr=np.zeros((8, 10, 3), np.uint8),
        )
        self.assertIsNone(frame.sensor_age_s(now))
        self.assertAlmostEqual(frame.capture_age_s(now), 0.03, places=2)

    def test_warmup_controls_cap_exposure_and_lock_white_balance(self):
        controls = Picamera2Camera._locked_auto_controls(
            {
                "ExposureTime": 12000,
                "AnalogueGain": 2.0,
                "ColourGains": (1.4, 1.7),
            },
            max_exposure_time_us=6000,
            max_analogue_gain=8.0,
        )
        self.assertFalse(controls["AeEnable"])
        self.assertFalse(controls["AwbEnable"])
        self.assertEqual(controls["ExposureTime"], 6000)
        self.assertEqual(controls["AnalogueGain"], 4.0)
        self.assertEqual(controls["ColourGains"], (1.4, 1.7))

    def test_adaptive_exposure_keeps_ae_on_and_locks_white_balance(self):
        controls = Picamera2Camera._locked_auto_controls(
            {
                "ExposureTime": 12000,
                "AnalogueGain": 8.0,
                "ColourGains": (1.4, 1.7),
            },
            max_exposure_time_us=6000,
            max_analogue_gain=8.0,
            keep_auto_exposure=True,
        )
        self.assertTrue(controls["AeEnable"])
        self.assertFalse(controls["AwbEnable"])
        self.assertEqual(controls["ColourGains"], (1.4, 1.7))
        self.assertNotIn("ExposureTime", controls)
        self.assertNotIn("AnalogueGain", controls)


class PreprocessingTests(unittest.TestCase):
    def test_search_and_tracking_reuse_one_preprocessing_pass(self):
        detector = StrictShapeDetector(search_min_area_px=80)
        image = np.zeros((360, 640, 3), np.uint8)
        cv2.fillConvexPoly(
            image,
            np.asarray([[260, 50], [150, 290], [370, 290]], np.int32),
            (0, 0, 255),
        )
        detector.reset_instrumentation()
        processed = detector.preprocess(image, frame_id=7)
        found = detector.search_processed(processed, {"red_triangle"})
        self.assertTrue(found)
        detector.begin_tracking(found[0], now=10.0)
        detector.track_processed(
            processed,
            "red_triangle",
            (found[0].center_x, found[0].center_y),
        )
        self.assertEqual(
            detector.preprocess_counts,
            {"hsv": 1, "red_mask": 1, "blue_mask": 1},
        )


class TemporalConfirmationTests(unittest.TestCase):
    def tracker(self) -> HitTracker:
        return HitTracker(
            required_hits=3,
            window_s=1.5,
            max_jump_px=100.0,
            min_duration_s=0.50,
            min_hit_ratio=0.60,
            max_missing_ratio=0.40,
            max_center_std_px=20.0,
            max_area_cv=0.20,
            max_bbox_cv=0.20,
            max_area_jump_ratio=2.0,
            min_colour_score=0.7,
            min_shape_score=0.7,
            min_total_score=0.7,
        )

    def run_rate(self, fps: int) -> float:
        tracker = self.tracker()
        confirmed_at = None
        for frame_id in range(fps + 1):
            timestamp = frame_id / fps
            current = detection(
                "red_triangle",
                frame_id,
                center=(320 + frame_id % 2, 240),
            )
            result = tracker.update(
                [current],
                {"red_triangle"},
                now=timestamp,
                frame_id=frame_id,
            )
            if result is not None:
                confirmed_at = timestamp
                break
        self.assertIsNotNone(confirmed_at)
        return float(confirmed_at)

    def test_confirmation_time_is_similar_across_processing_rates(self):
        times = [self.run_rate(fps) for fps in (5, 10, 20, 30)]
        self.assertLessEqual(max(times) - min(times), 0.20)
        self.assertGreaterEqual(min(times), 0.50)

    def test_fast_burst_does_not_confirm(self):
        tracker = self.tracker()
        result = None
        for frame_id, timestamp in enumerate((0.0, 0.03, 0.06, 0.09)):
            result = tracker.update(
                [detection("red_triangle", frame_id)],
                {"red_triangle"},
                now=timestamp,
                frame_id=frame_id,
            )
        self.assertIsNone(result)

    def test_misses_prevent_confirmation(self):
        tracker = self.tracker()
        result = None
        for frame_id in range(9):
            observations = (
                [detection("blue_hexagon", frame_id)]
                if frame_id in {0, 4, 8}
                else []
            )
            result = tracker.update(
                observations,
                {"blue_hexagon"},
                now=frame_id * 0.1,
                frame_id=frame_id,
            )
        self.assertIsNone(result)

    def test_large_area_jump_resets_old_evidence(self):
        tracker = self.tracker()
        tracker.update(
            [detection("blue_hexagon", 1, area=3000.0)],
            {"blue_hexagon"},
            now=0.0,
            frame_id=1,
        )
        tracker.update(
            [detection("blue_hexagon", 2, area=3100.0)],
            {"blue_hexagon"},
            now=0.3,
            frame_id=2,
        )
        result = tracker.update(
            [detection("blue_hexagon", 3, area=15000.0)],
            {"blue_hexagon"},
            now=0.6,
            frame_id=3,
        )
        self.assertIsNone(result)
        self.assertEqual(tracker.status(0.6)["blue_hexagon"], 1)


class ShapeRejectionTests(unittest.TestCase):
    def setUp(self):
        self.detector = StrictShapeDetector(search_min_area_px=100)

    def targets(self, image: np.ndarray) -> set[str]:
        detections, _ = self.detector.search(image)
        return {item.target for item in detections}

    def test_rejects_red_circle(self):
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.circle(image, (330, 270), 120, (0, 0, 255), -1)
        self.assertNotIn("red_triangle", self.targets(image))

    def test_rejects_blue_circle(self):
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.circle(image, (330, 270), 120, (255, 0, 0), -1)
        self.assertNotIn("blue_hexagon", self.targets(image))

    def test_detects_rotated_blurred_triangle_on_textured_background(self):
        rng = np.random.default_rng(42)
        image = rng.integers(15, 75, size=(540, 960, 3), dtype=np.uint8)
        triangle = np.asarray([[360, 100], [190, 390], [540, 350]], np.int32)
        cv2.fillConvexPoly(image, triangle, (0, 0, 245))
        image = cv2.GaussianBlur(image, (7, 7), 1.3)
        self.assertIn("red_triangle", self.targets(image))

    def test_notched_blue_rectangle_never_confirms_or_locks(self):
        tracker = HitTracker(required_hits=3, window_s=1.5, max_jump_px=160)
        for frame_id in range(12):
            image = np.zeros((540, 960, 3), np.uint8)
            cv2.rectangle(image, (260, 150), (650, 360), (255, 0, 0), -1)
            cv2.rectangle(image, (420, 145), (500, 205), (0, 0, 0), -1)
            image = cv2.GaussianBlur(image, (5, 5), 0.8)
            processed = self.detector.preprocess(
                image,
                frame_id=frame_id,
                captured_at_s=frame_id * 0.1,
                received_at_s=frame_id * 0.1,
            )
            detections = self.detector.search_processed(
                processed,
                {"blue_hexagon"},
            )
            self.assertEqual(detections, [])
            self.assertIsNone(
                tracker.update(
                    detections,
                    {"blue_hexagon"},
                    now=frame_id * 0.1,
                    frame_id=frame_id,
                )
            )
        self.assertEqual(tracker.status(1.1)["blue_hexagon"], 0)
        self.assertIsNone(self.detector.tracking_state)
        self.assertIsNone(
            self.detector.verify_target_frame(
                image,
                "blue_hexagon",
                frame_id=12,
                captured_at_s=1.2,
                received_at_s=1.2,
            )
        )


class StrictTrackingTests(unittest.TestCase):
    def setUp(self):
        self.detector = StrictShapeDetector(
            search_min_area_px=100,
            strong_verify_interval_s=0.3,
            max_strong_geometry_age_s=0.7,
            max_failed_geometry_checks=2,
        )
        self.hexagon = np.zeros((540, 960, 3), np.uint8)
        points = np.asarray(
            [[300, 185], [388, 135], [480, 188], [478, 295], [392, 355], [298, 300]],
            np.int32,
        )
        cv2.fillConvexPoly(self.hexagon, points, (255, 0, 0))
        processed = self.detector.preprocess(
            self.hexagon,
            frame_id=1,
            captured_at_s=1.0,
            received_at_s=1.0,
        )
        strict = self.detector.search_processed(
            processed,
            {"blue_hexagon"},
        )
        self.assertTrue(strict)
        self.initial = strict[0]
        self.detector.begin_tracking(self.initial, now=1.0)

    @staticmethod
    def round_blue_blob() -> np.ndarray:
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.ellipse(image, (390, 245), (105, 85), 0, 0, 360, (255, 0, 0), -1)
        return image

    def track(self, image: np.ndarray, timestamp: float):
        processed = self.detector.preprocess(
            image,
            frame_id=round(timestamp * 100),
            captured_at_s=timestamp,
            received_at_s=timestamp,
        )
        return self.detector.track_processed(
            processed,
            "blue_hexagon",
            (self.initial.center_x, self.initial.center_y),
            max_jump_px=160,
        )

    def test_colour_tracking_is_temporary_between_strict_checks(self):
        tracked = self.track(self.round_blue_blob(), 1.1)
        self.assertIsNotNone(tracked)
        self.assertEqual(tracked.source, "colour_track")
        self.assertEqual(tracked.status, "temporary_colour_tracking")

    def test_failed_strict_check_never_falls_back_to_colour(self):
        self.assertIsNone(self.track(self.round_blue_blob(), 1.31))
        self.assertIsNotNone(self.detector.tracking_state)
        self.assertEqual(
            self.detector.tracking_state.failed_geometry_checks,
            1,
        )
        self.assertIsNone(self.track(self.round_blue_blob(), 1.40))

    def test_two_failed_geometry_checks_drop_lock(self):
        self.assertIsNone(self.track(self.round_blue_blob(), 1.31))
        self.assertIsNone(self.track(self.round_blue_blob(), 1.62))
        self.assertIsNone(self.detector.tracking_state)
        self.assertIn("failures=2", self.detector.last_lock_drop_reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)

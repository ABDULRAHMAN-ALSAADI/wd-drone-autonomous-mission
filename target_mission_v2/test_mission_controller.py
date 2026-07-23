#!/usr/bin/env python3
import json
import math
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np
from pymavlink import mavutil

from vision import Detection, HitTracker, StrictShapeDetector
from control import altitude_velocity_down
from camera_sources import build_rpicam_mjpeg_command
from configuration import load_config
from mission_controller import (
    Controller,
    State,
    Vehicle,
    enforce_parameters,
    heartbeat_is_vehicle,
    optional_seconds_label,
    payload_colour_for_target,
    payload_output_for_target,
    required_ardupilot_parameters,
    validate_config,
)


class FakeHeartbeat:
    def __init__(self, system, component, vehicle_type, autopilot, mode="STABILIZE", armed=False):
        self._system = system
        self._component = component
        self.type = vehicle_type
        self.autopilot = autopilot
        self.base_mode = mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
        if armed:
            self.base_mode |= mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
        self.custom_mode = {
            "STABILIZE": 0,
            "AUTO": 3,
            "GUIDED": 4,
            "LOITER": 5,
            "RTL": 6,
        }.get(mode, 0)

    def get_srcSystem(self):
        return self._system

    def get_srcComponent(self):
        return self._component

    def get_type(self):
        return "HEARTBEAT"


class FakeBadData:
    def get_type(self):
        return "BAD_DATA"


class FakeReceiveQueue:
    def __init__(self, messages):
        self.messages = list(messages)

    def recv_match(self, **_kwargs):
        return self.messages.pop(0) if self.messages else None


class MavlinkFilteringTests(unittest.TestCase):
    def test_heartbeat_filter_accepts_autopilot_only(self):
        vehicle_hb = FakeHeartbeat(1, mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1, mavutil.mavlink.MAV_TYPE_QUADROTOR, mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA)
        gcs_hb = FakeHeartbeat(255, 190, mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID)
        onboard_hb = FakeHeartbeat(1, 0, mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER, mavutil.mavlink.MAV_AUTOPILOT_INVALID)
        self.assertTrue(heartbeat_is_vehicle(vehicle_hb))
        self.assertFalse(heartbeat_is_vehicle(gcs_hb))
        self.assertFalse(heartbeat_is_vehicle(onboard_hb))

    def test_vehicle_ignores_non_vehicle_heartbeat_for_mode_state(self):
        vehicle = Vehicle.__new__(Vehicle)
        vehicle.target_system = 1
        vehicle.mode = "GUIDED"
        vehicle.armed = False
        vehicle.last_heartbeat = 123.0
        ignored = FakeHeartbeat(255, 190, mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID, mode="AUTO", armed=True)
        vehicle._handle_message(ignored)
        self.assertEqual(vehicle.mode, "GUIDED")
        self.assertFalse(vehicle.armed)
        self.assertEqual(vehicle.last_heartbeat, 123.0)

    def test_stale_heartbeat_recovery_drains_beyond_normal_poll_limit(self):
        heartbeat = FakeHeartbeat(
            1,
            mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1,
            mavutil.mavlink.MAV_TYPE_QUADROTOR,
            mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
            mode="AUTO",
            armed=True,
        )
        vehicle = Vehicle.__new__(Vehicle)
        vehicle.target_system = 1
        vehicle.mode = "STABILIZE"
        vehicle.armed = False
        vehicle.last_heartbeat = 1.0
        vehicle.master = FakeReceiveQueue([FakeBadData() for _ in range(150)] + [heartbeat])

        vehicle.poll()
        self.assertEqual(vehicle.last_heartbeat, 1.0)
        self.assertTrue(vehicle.recover_vehicle_heartbeat(timeout_s=0.1))
        self.assertEqual(vehicle.mode, "AUTO")
        self.assertTrue(vehicle.armed)
        self.assertGreater(vehicle.last_heartbeat, 1.0)


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.detector = StrictShapeDetector(search_min_area_px=100)

    def targets(self, image):
        detections, _ = self.detector.search(image)
        return {x.target for x in detections}

    def detections(self, image):
        detections, _ = self.detector.search(image)
        return detections

    def test_detects_red_triangle(self):
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.fillConvexPoly(image, np.array([[220, 70], [70, 370], [370, 370]], np.int32), (0, 0, 255))
        self.assertIn("red_triangle", self.targets(image))

    def test_rejects_red_square(self):
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.rectangle(image, (150, 110), (430, 390), (0, 0, 255), -1)
        self.assertNotIn("red_triangle", self.targets(image))

    def test_rejects_red_rectangle(self):
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.rectangle(image, (100, 170), (500, 320), (0, 0, 255), -1)
        self.assertNotIn("red_triangle", self.targets(image))

    def test_rejects_blurred_red_square(self):
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.rectangle(image, (160, 120), (420, 380), (0, 0, 255), -1)
        image = cv2.GaussianBlur(image, (11, 11), 2.0)
        self.assertNotIn("red_triangle", self.targets(image))

    def test_detects_blue_hexagon(self):
        image = np.zeros((540, 960, 3), np.uint8)
        points = np.array([[520, 220], [585, 120], [710, 120], [775, 220], [710, 320], [585, 320]], np.int32)
        cv2.fillConvexPoly(image, points, (255, 0, 0))
        self.assertIn("blue_hexagon", self.targets(image))

    def test_detects_perspective_blue_hexagon(self):
        image = np.zeros((540, 960, 3), np.uint8)
        points = np.array([[510, 235], [580, 145], [720, 160], [785, 245], [700, 325], [570, 305]], np.int32)
        cv2.fillConvexPoly(image, points, (255, 0, 0))
        image = cv2.GaussianBlur(image, (7, 7), 1.2)
        self.assertIn("blue_hexagon", self.targets(image))

    def test_detects_gazebo_purple_blue_hexagon(self):
        image = np.zeros((540, 960, 3), np.uint8)
        image[:] = (55, 85, 45)
        points = np.array([[300, 185], [388, 135], [480, 188], [478, 295], [392, 355], [298, 300]], np.int32)
        cv2.fillConvexPoly(image, points, (210, 70, 105))
        image = cv2.GaussianBlur(image, (5, 5), 0.9)
        self.assertIn("blue_hexagon", self.targets(image))

    def test_rejects_blue_square(self):
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.rectangle(image, (150, 110), (430, 390), (255, 0, 0), -1)
        self.assertNotIn("blue_hexagon", self.targets(image))

    def test_rejects_blue_rectangle(self):
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.rectangle(image, (100, 170), (500, 320), (255, 0, 0), -1)
        self.assertNotIn("blue_hexagon", self.targets(image))

    def test_rejects_blue_runway_strip(self):
        image = np.zeros((540, 960, 3), np.uint8)
        image[:] = (180, 180, 180)
        box = cv2.boxPoints(((520, 300), (260, 52), -32)).astype(np.int32)
        cv2.fillConvexPoly(image, box, (210, 70, 105))
        image = cv2.GaussianBlur(image, (5, 5), 0.8)
        self.assertNotIn("blue_hexagon", self.targets(image))

    def test_rejects_multiple_blue_runway_bars(self):
        image = np.zeros((540, 960, 3), np.uint8)
        image[:] = (180, 180, 180)
        for center in ((210, 310), (385, 285), (560, 260), (735, 235)):
            box = cv2.boxPoints((center, (135, 28), -10)).astype(np.int32)
            cv2.fillConvexPoly(image, box, (210, 70, 105))
        image = cv2.GaussianBlur(image, (5, 5), 0.8)
        self.assertNotIn("blue_hexagon", self.targets(image))

    def test_rejects_rotated_blue_square(self):
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.fillConvexPoly(image, np.array([[300, 70], [465, 235], [300, 400], [135, 235]], np.int32), (255, 0, 0))
        self.assertNotIn("blue_hexagon", self.targets(image))

    def test_rejects_low_saturation_cyan_hexagon(self):
        image = np.zeros((540, 960, 3), np.uint8)
        points = np.array([[300, 185], [388, 135], [480, 188], [478, 295], [392, 355], [298, 300]], np.int32)
        cv2.fillConvexPoly(image, points, (205, 220, 210))
        self.assertNotIn("blue_hexagon", self.targets(image))

    def test_search_rejects_partial_target_at_frame_border(self):
        image = np.zeros((540, 960, 3), np.uint8)
        points = np.array([[-35, 185], [45, 125], [145, 180], [145, 300], [45, 355], [-35, 300]], np.int32)
        cv2.fillConvexPoly(image, points, (255, 0, 0))
        self.assertNotIn("blue_hexagon", self.targets(image))

    def test_search_rejects_implausibly_large_target(self):
        detector = StrictShapeDetector(search_min_area_px=100, search_max_area_fraction=0.30)
        image = np.zeros((540, 960, 3), np.uint8)
        points = np.array([[60, 270], [220, 25], [740, 25], [900, 270], [740, 515], [220, 515]], np.int32)
        cv2.fillConvexPoly(image, points, (255, 0, 0))
        detections, _ = detector.search(image)
        self.assertNotIn("blue_hexagon", {item.target for item in detections})

    def test_three_hit_confirmation(self):
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.fillConvexPoly(image, np.array([[220, 70], [70, 370], [370, 370]], np.int32), (0, 0, 255))
        detections = self.detections(image)
        tracker = HitTracker(required_hits=3, window_s=1.5, max_jump_px=160)
        self.assertIsNone(tracker.update(detections, {"red_triangle"}, 1.0))
        self.assertIsNone(tracker.update(detections, {"red_triangle"}, 1.2))
        confirmed = tracker.update(detections, {"red_triangle"}, 1.4)
        self.assertIsNotNone(confirmed)
        self.assertEqual(confirmed.hits, 3)

    def test_tracker_rejects_unreasonable_jump(self):
        detector = StrictShapeDetector(search_min_area_px=100)
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.fillConvexPoly(image, np.array([[820, 70], [670, 370], [970, 370]], np.int32), (0, 0, 255))
        tracked, _ = detector.track_colour(image, "red_triangle", (100, 100), max_jump_px=80)
        self.assertIsNone(tracked)

    def test_tracker_rejects_blue_runway_strip_as_hexagon(self):
        image = np.zeros((540, 960, 3), np.uint8)
        image[:] = (180, 180, 180)
        box = cv2.boxPoints(((520, 300), (260, 52), -3)).astype(np.int32)
        cv2.fillConvexPoly(image, box, (210, 70, 105))
        tracked, _ = self.detector.track_colour(image, "blue_hexagon", (520, 300), max_jump_px=400)
        self.assertIsNone(tracked)

    def test_tracker_keeps_valid_blue_hexagon(self):
        image = np.zeros((540, 960, 3), np.uint8)
        points = np.array([[300, 185], [388, 135], [480, 188], [478, 295], [392, 355], [298, 300]], np.int32)
        cv2.fillConvexPoly(image, points, (210, 70, 105))
        tracked, _ = self.detector.track_colour(image, "blue_hexagon", (390, 245), max_jump_px=120)
        self.assertIsNotNone(tracked)
        self.assertEqual(tracked.target, "blue_hexagon")

    def test_tracker_rejects_unknown_target(self):
        image = np.zeros((540, 960, 3), np.uint8)
        tracked, _ = self.detector.track_colour(image, "yellow_circle", (100, 100))
        self.assertIsNone(tracked)

    def test_triangle_tracking_fallback_keeps_broken_confirmed_triangle(self):
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.fillConvexPoly(image, np.array([[480, 80], [300, 420], [660, 420]], np.int32), (0, 0, 255))
        cv2.rectangle(image, (430, 330), (530, 455), (0, 0, 0), -1)
        detections, _ = self.detector.search(image)
        self.assertNotIn("red_triangle", {item.target for item in detections})
        tracked, _ = self.detector.track_colour(image, "red_triangle", (480, 280), max_jump_px=220)
        self.assertIsNotNone(tracked)
        self.assertEqual(tracked.target, "red_triangle")

    def test_triangle_tracking_fallback_rejects_round_red_blob(self):
        image = np.zeros((540, 960, 3), np.uint8)
        cv2.ellipse(image, (480, 280), (140, 95), 0, 0, 360, (0, 0, 255), -1)
        tracked, _ = self.detector.track_colour(image, "red_triangle", (480, 280), max_jump_px=220)
        self.assertIsNone(tracked)


class AltitudeTests(unittest.TestCase):
    def test_optional_seconds_label_accepts_null_for_unlimited(self):
        self.assertEqual(optional_seconds_label(None), "inf")
        self.assertEqual(optional_seconds_label(0.25), "0.2s")

    def test_holds_five_metres(self):
        self.assertEqual(altitude_velocity_down(5.0, 5.0, 0.2, 0.45, 0.3), 0.0)

    def test_descends_only_when_above_five(self):
        self.assertGreater(altitude_velocity_down(6.0, 5.0, 0.2, 0.45, 0.3), 0.0)

    def test_climbs_only_when_below_five(self):
        self.assertLess(altitude_velocity_down(4.0, 5.0, 0.2, 0.45, 0.3), 0.0)

    def test_vertical_speed_is_limited(self):
        self.assertAlmostEqual(altitude_velocity_down(15.0, 5.0, 0.2, 0.45, 0.3), 0.3)


class MissionConfigTests(unittest.TestCase):
    @staticmethod
    def config():
        return {
            "mavlink": {"connection": "udpin:0.0.0.0:14551", "baud": None},
            "camera": {"source": "udp_h264", "udp_port": 5600},
            "mission": {
                "name": "test_mission_2",
                "search_enabled": True,
                "search_start_wp": 2,
                "search_enable_rc_channel": None,
                "search_enable_pwm_min": 1700,
                "survey_altitude_m": 5.0,
                "mode_change_timeout_s": 5.0,
                "max_flight_time_s": 600.0,
            },
            "navigation": {
                "search_speed_source": "qgc_mission",
                "search_speed_m_s": 3.0,
            },
            "parameters": {
                "enforce": True,
                "rtl_alt_cm": 500.0,
                "rtl_climb_min_cm": 0.0,
                "mis_restart": 0,
                "read_timeout_s": 8.0,
                "missing_action": "warn",
            },
            "vision": {
                "backend": "strict_shape",
                "process_width": 960,
                "search_min_area_px": 220.0,
                "tracking_min_area_px": 120.0,
                "required_hits": 3,
                "confirmation_window_s": 1.5,
                "max_lock_jump_px": 160.0,
                "debug_rejects": False,
            },
            "control": {
                "command_rate_hz": 10.0,
                "center_kp": 0.85,
                "center_max_speed_m_s": 0.65,
                "center_tolerance_px": 34.0,
                "center_tolerance_px_by_target": {},
                "center_hold_s": 0.8,
                "target_lost_timeout_s": 2.0,
                "reacquire_after_lost_s": 0.25,
                "altitude_control": "hold_configured",
                "altitude_tolerance_m": 0.2,
                "altitude_kp": 0.45,
                "altitude_max_speed_m_s": 0.3,
                "image_y_to_forward_sign": -1.0,
                "image_x_to_right_sign": 1.0,
            },
            "payload": {
                "simulate_only": True,
                "servo_channel": 9,
                "release_pwm": 1900,
                "reset_pwm": 1100,
                "release_hold_s": 1.0,
                "total_action_time_s": 1.5,
            },
            "safety": {
                "max_center_time_s": 25.0,
                "max_guided_speed_m_s": 0.45,
                "guided_auto_bounce_grace_s": None,
                "max_guided_auto_bounces_per_target": None,
                "active_target_abort_mode": "AUTO",
                "mode_retry_interval_s": 0.2,
                "camera_frame_timeout_s": 2.0,
                "payload_requires_guided": True,
                "payload_min_altitude_m": None,
                "payload_max_altitude_m": None,
            },
            "display": {
                "show_main_window": False,
                "show_masks": False,
                "overlay_font_scale": 0.46,
                "overlay_background_alpha": 0.42,
            },
            "logging": {"directory": "logs/mission_v2", "flush_interval_s": 0.5},
        }

    def test_payload_colour_matches_rotary_wing_rules(self):
        self.assertEqual(payload_colour_for_target("blue_hexagon"), "red")
        self.assertEqual(payload_colour_for_target("red_triangle"), "blue")

    def test_ardupilot_parameters_use_real_names_and_centimetres(self):
        self.assertEqual(
            required_ardupilot_parameters(self.config()),
            {"RTL_ALT": 500.0, "RTL_CLIMB_MIN": 0.0, "MIS_RESTART": 0.0},
        )

    def test_legacy_metre_parameter_config_is_converted(self):
        config = self.config()
        config["parameters"] = {"rtl_alt_m": 5.0, "rtl_climb_min_m": 0.0, "mis_restart": 0}
        self.assertEqual(required_ardupilot_parameters(config)["RTL_ALT"], 500.0)

    def test_validate_config_rejects_bad_command_rate(self):
        config = self.config()
        config["control"]["command_rate_hz"] = 0
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_rejects_bad_target_center_tolerance(self):
        config = self.config()
        config["control"]["center_tolerance_px_by_target"] = {"red_triangle": 0}
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_rejects_unknown_target_center_tolerance(self):
        config = self.config()
        config["control"]["center_tolerance_px_by_target"] = {"green_circle": 20}
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_rejects_bad_mavlink_baud(self):
        config = self.config()
        config["mavlink"]["baud"] = 0
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_rejects_bad_camera_source(self):
        config = self.config()
        config["camera"]["source"] = "magic_camera"
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_accepts_rpicam_mjpeg_source(self):
        config = self.config()
        config["camera"] = {
            "source": "rpicam_mjpeg",
            "camera_index": 0,
            "width": 1280,
            "height": 720,
            "framerate": 15,
            "quality": 85,
            "read_timeout_s": 2.0,
        }
        validate_config(config)

    def test_validate_config_rejects_bad_rpicam_dimensions(self):
        config = self.config()
        config["camera"] = {
            "source": "rpicam_mjpeg",
            "width": 0,
            "height": 720,
            "framerate": 15,
            "quality": 85,
        }
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_rejects_bad_rpicam_camera_index(self):
        config = self.config()
        config["camera"] = {
            "source": "rpicam_mjpeg",
            "camera_index": -1,
            "width": 1280,
            "height": 720,
            "framerate": 15,
            "quality": 85,
        }
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_rpicam_mjpeg_command_outputs_to_stdout(self):
        cmd = build_rpicam_mjpeg_command({
            "source": "rpicam_mjpeg",
            "camera_index": 0,
            "width": 1280,
            "height": 720,
            "framerate": 15,
            "quality": 85,
            "autofocus_mode": "manual",
            "lens_position": 0.0,
        })
        self.assertEqual(cmd[0], "rpicam-vid")
        self.assertIn("mjpeg", cmd)
        self.assertEqual(cmd[cmd.index("--camera") + 1], "0")
        self.assertEqual(cmd[-2:], ["-o", "-"])
        self.assertIn("--flush", cmd)

    def test_validate_config_rejects_unknown_missing_action(self):
        config = self.config()
        config["parameters"]["missing_action"] = "ignore"
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_rejects_unknown_vision_backend(self):
        config = self.config()
        config["vision"]["backend"] = "yolo_experiment"
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_allows_qgc_owned_altitude_without_survey_altitude(self):
        config = self.config()
        config["control"]["altitude_control"] = "off"
        del config["mission"]["survey_altitude_m"]
        validate_config(config)

    def test_validate_config_rejects_unknown_altitude_control(self):
        config = self.config()
        config["control"]["altitude_control"] = "sometimes"
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_rejects_bad_safety_speed(self):
        config = self.config()
        config["safety"]["max_guided_speed_m_s"] = 0.0
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_rejects_bad_search_speed_source(self):
        config = self.config()
        config["navigation"]["search_speed_source"] = "mystery"
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_rejects_bad_search_enable_rc_channel(self):
        config = self.config()
        config["mission"]["search_enable_rc_channel"] = 19
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_rejects_bad_guided_bounce_limit(self):
        config = self.config()
        config["safety"]["max_guided_auto_bounces_per_target"] = -1
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_rejects_bad_active_target_abort_mode(self):
        config = self.config()
        config["safety"]["active_target_abort_mode"] = "DRIFT"
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_rejects_bad_camera_frame_timeout(self):
        config = self.config()
        config["safety"]["camera_frame_timeout_s"] = 0
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_validate_config_requires_companion_search_speed(self):
        config = self.config()
        config["navigation"]["search_speed_source"] = "companion_do_change_speed"
        config["navigation"]["search_speed_m_s"] = 0.0
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_physical_payload_requires_persistent_state(self):
        config = self.config()
        config["payload"]["simulate_only"] = False
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_physical_payload_accepts_enabled_persistent_state(self):
        config = self.config()
        config["payload"]["simulate_only"] = False
        config["payload"].update(
            {
                "mechanism": "selector_servo",
                "red_payload_pwm": 1100,
                "neutral_pwm": 1500,
                "blue_payload_pwm": 1900,
            }
        )
        config["payload_state"] = {"enabled": True, "path": "payload-state.json"}
        validate_config(config)

    def test_profile_configs_are_valid(self):
        paths = [Path(__file__).with_name("operator_config.json"), Path(__file__).with_name("parameter_config.json")]
        paths.extend(sorted(Path(__file__).with_name("configs").glob("*.json")))
        paths.extend(sorted((Path(__file__).resolve().parents[1] / "real_mission" / "parameter_config").glob("*.json")))
        for path in paths:
            with self.subTest(path=path.name):
                validate_config(load_config(path))

    def test_payload_outputs_match_selector_servo_mapping(self):
        payload = {
            "mechanism": "selector_servo",
            "servo_channel": 5,
            "red_payload_pwm": 1100,
            "neutral_pwm": 1500,
            "blue_payload_pwm": 1900,
        }
        self.assertEqual(
            payload_output_for_target(payload, "blue_hexagon"),
            {"servo_channel": 5, "release_pwm": 1100, "reset_pwm": 1500},
        )
        self.assertEqual(
            payload_output_for_target(payload, "red_triangle"),
            {"servo_channel": 5, "release_pwm": 1900, "reset_pwm": 1500},
        )

    def test_enforce_parameters_can_be_disabled(self):
        class FakeVehicle:
            def read_parameter(self, name, timeout_s=8.0):
                raise AssertionError("read_parameter should not be called")

            def set_parameter(self, name, value):
                raise AssertionError("set_parameter should not be called")

        config = self.config()
        config["parameters"]["enforce"] = False
        enforce_parameters(FakeVehicle(), config)

    def test_enforce_parameters_sets_only_when_needed(self):
        class FakeVehicle:
            def __init__(self):
                self.values = {"RTL_ALT": 1500.0, "RTL_CLIMB_MIN": 0.0, "MIS_RESTART": 0.0}
                self.set_calls = []

            def read_parameter(self, name, timeout_s=8.0):
                return self.values[name]

            def set_parameter(self, name, value):
                self.set_calls.append((name, value))
                self.values[name] = value

        vehicle = FakeVehicle()
        enforce_parameters(vehicle, self.config())
        self.assertEqual(vehicle.set_calls, [("RTL_ALT", 500.0)])

    def test_enforce_parameters_can_warn_for_missing_sitl_parameter(self):
        class FakeVehicle:
            def __init__(self):
                self.reads = []

            def read_parameter(self, name, timeout_s=8.0):
                self.reads.append((name, timeout_s))
                return None

            def set_parameter(self, name, value):
                raise AssertionError("set_parameter should not be called")

        config = self.config()
        config["parameters"]["read_timeout_s"] = 2.5
        enforce_parameters(FakeVehicle(), config)

    def test_enforce_parameters_fails_for_missing_real_parameter_when_strict(self):
        class FakeVehicle:
            def read_parameter(self, name, timeout_s=8.0):
                return None

            def set_parameter(self, name, value):
                raise AssertionError("set_parameter should not be called")

        config = self.config()
        config["parameters"]["missing_action"] = "fail"
        with self.assertRaises(RuntimeError):
            enforce_parameters(FakeVehicle(), config)


class FakeVehicle:
    def __init__(self):
        self.mode = "GUIDED"
        self.armed = True
        self.mission_seq = 2
        self.relative_alt_m = 7.0
        self.horizontal_speed_m_s = 0.0
        self.velocity_down_m_s = 0.0
        self.total_speed_m_s = 0.0
        self.acceleration_m_s2 = 0.0
        self.mode_requests = []
        self.velocities = []
        self.servos = []
        self.ground_speeds = []
        self.rc_channels = {}
        now = time.monotonic()
        self.last_heartbeat = now
        self.last_position_at = now
        self.last_mission_at = now
        self.last_rc_at = now
        self.latitude_deg = 37.0
        self.longitude_deg = 32.0
        self.gps_fix_type = 3
        self.gps_satellites = 12
        self.last_gps_at = now
        self.battery_voltage_v = 20.0
        self.ekf_flags = 51
        self.command_acks = {}

    def send_body_velocity(self, forward, right, down):
        self.velocities.append((forward, right, down))

    def set_mode(self, name):
        self.mode_requests.append(name)
        self.mode = name

    def set_servo(self, channel, pwm):
        self.servos.append((channel, pwm))
        sent_at = time.monotonic()
        self.command_acks[mavutil.mavlink.MAV_CMD_DO_SET_SERVO] = (
            sent_at,
            mavutil.mavlink.MAV_RESULT_ACCEPTED,
        )
        return sent_at

    def command_ack_after(self, command, sent_at):
        ack = self.command_acks.get(command)
        if ack is None or ack[0] < sent_at:
            return None
        return ack[1]

    def set_ground_speed(self, speed_m_s):
        self.ground_speeds.append(speed_m_s)


class FakeCamera:
    def release(self):
        pass


class FakeDetector:
    def __init__(self, search_detections=None):
        self.search_detections = search_detections or []

    def track_colour(self, frame, target, previous_center, max_jump_px=220.0):
        mask = np.zeros(frame.shape[:2], np.uint8)
        return None, {"red": mask, "blue": mask}

    def search(self, frame):
        mask = np.zeros(frame.shape[:2], np.uint8)
        return list(self.search_detections), {"red": mask, "blue": mask}


class ControllerFlowTests(unittest.TestCase):
    def config(self):
        config = MissionConfigTests.config()
        config["display"]["show_main_window"] = False
        config["display"]["show_masks"] = False
        return config

    def controller(self, config=None, vehicle=None):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        cfg = config or self.config()
        cfg["logging"]["directory"] = str(Path(tempdir.name) / "logs")
        ctrl = Controller(cfg, vehicle or FakeVehicle(), FakeCamera())
        self.addCleanup(ctrl.log_file.close)
        return ctrl

    def blank_frame(self):
        return np.zeros((540, 960, 3), np.uint8)

    def blank_masks(self):
        mask = np.zeros((540, 960), np.uint8)
        return {"red": mask, "blue": mask}

    def test_guided_speed_is_clamped_by_safety_config(self):
        vehicle = FakeVehicle()
        config = self.config()
        config["safety"]["max_guided_speed_m_s"] = 0.2
        ctrl = self.controller(config, vehicle)
        ctrl.send_velocity(1.0, -1.0, 0.0)
        self.assertAlmostEqual(math.hypot(*vehicle.velocities[-1][:2]), 0.2)

    def test_qgc_owned_search_speed_sends_no_speed_command(self):
        vehicle = FakeVehicle()
        config = self.config()
        ctrl = self.controller(config, vehicle)
        ctrl.transition(State.SEARCH, "test search")
        self.assertEqual(vehicle.ground_speeds, [])

    def test_companion_search_speed_sends_change_speed(self):
        vehicle = FakeVehicle()
        config = self.config()
        config["navigation"]["search_speed_source"] = "companion_do_change_speed"
        config["navigation"]["search_speed_m_s"] = 3.2
        ctrl = self.controller(config, vehicle)
        ctrl.transition(State.SEARCH, "test search")
        self.assertEqual(vehicle.ground_speeds, [3.2])

    def test_search_does_not_start_when_mission_profile_disables_it(self):
        vehicle = FakeVehicle()
        vehicle.mode = "AUTO"
        vehicle.mission_seq = 9
        config = self.config()
        config["mission"]["search_enabled"] = False
        ctrl = self.controller(config, vehicle)
        ctrl.state = State.WAITING_FOR_AUTO
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.WAITING_FOR_AUTO)
        self.assertIn("search blocked", ctrl.status_message)

    def test_search_does_not_start_until_rc_enable_switch_is_high(self):
        vehicle = FakeVehicle()
        vehicle.mode = "AUTO"
        vehicle.mission_seq = 9
        vehicle.rc_channels[7] = 1200
        config = self.config()
        config["mission"]["search_enable_rc_channel"] = 7
        config["mission"]["search_enable_pwm_min"] = 1700
        ctrl = self.controller(config, vehicle)
        ctrl.state = State.WAITING_FOR_AUTO
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.WAITING_FOR_AUTO)
        self.assertIn("RC7=1200", ctrl.status_message)
        vehicle.rc_channels[7] = 1800
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.SEARCH)

    def test_auto_bounce_in_center_retries_guided_without_losing_target(self):
        vehicle = FakeVehicle()
        vehicle.mode = "AUTO"
        config = self.config()
        ctrl = self.controller(config, vehicle)
        ctrl.state = State.CENTER
        ctrl.current_target = "red_triangle"
        ctrl.last_seen_at = time.monotonic()
        ctrl.center_started_at = time.monotonic()
        ctrl.last_mode_request_at = time.monotonic()
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.CENTER)
        self.assertEqual(ctrl.current_target, "red_triangle")
        self.assertEqual(vehicle.mode_requests[-1], "GUIDED")
        self.assertEqual(ctrl.guided_bounce_count, 1)

    def test_waiting_for_guided_timeout_keeps_target_lock(self):
        vehicle = FakeVehicle()
        vehicle.mode = "AUTO"
        config = self.config()
        config["mission"]["mode_change_timeout_s"] = 0.1
        ctrl = self.controller(config, vehicle)
        ctrl.state = State.WAITING_FOR_GUIDED
        ctrl.state_started_at = time.monotonic() - 1.0
        ctrl.current_target = "blue_hexagon"
        ctrl.last_detection = Detection("blue_hexagon", 480, 270, 500.0, 0.9, 6, 0, 0, 6, 0.8, 0.7, 0.9, 460, 250, 40, 40)
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.WAITING_FOR_GUIDED)
        self.assertEqual(ctrl.current_target, "blue_hexagon")
        self.assertEqual(vehicle.mode_requests[-1], "GUIDED")

    def test_waiting_for_guided_stands_down_on_external_mode(self):
        vehicle = FakeVehicle()
        vehicle.mode = "LOITER"
        ctrl = self.controller(vehicle=vehicle)
        ctrl.state = State.WAITING_FOR_GUIDED
        ctrl.current_target = "blue_hexagon"
        ctrl.last_detection = Detection("blue_hexagon", 480, 270, 500.0, 0.9, 6, 0, 0, 6, 0.8, 0.7, 0.9, 460, 250, 40, 40)

        ctrl.update(self.blank_frame(), [], self.blank_masks())

        self.assertEqual(ctrl.state, State.WAITING_FOR_AUTO)
        self.assertIsNone(ctrl.current_target)
        self.assertEqual(vehicle.mode_requests, [])
        self.assertEqual(vehicle.velocities[-1], (0.0, 0.0, 0.0))

    def test_center_holds_guided_while_target_is_temporarily_lost(self):
        vehicle = FakeVehicle()
        config = self.config()
        config["control"]["target_lost_timeout_s"] = 4.0
        config["control"]["reacquire_after_lost_s"] = 0.25
        ctrl = self.controller(config, vehicle)
        ctrl.detector = FakeDetector()
        ctrl.state = State.CENTER
        ctrl.current_target = "red_triangle"
        ctrl.last_detection = Detection("red_triangle", 450, 260, 400.0, 0.8, 3, 3, 0, 0, 0.5, 0.6, 0.9, 430, 240, 40, 40)
        ctrl.last_seen_at = time.monotonic() - 1.0
        ctrl.center_started_at = time.monotonic()
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.CENTER)
        self.assertEqual(ctrl.current_target, "red_triangle")
        self.assertEqual(vehicle.mode_requests, [])
        self.assertEqual(vehicle.velocities[-1][:2], (0.0, 0.0))

    def test_center_reacquires_same_target_before_timeout(self):
        vehicle = FakeVehicle()
        config = self.config()
        config["control"]["target_lost_timeout_s"] = 4.0
        config["control"]["reacquire_after_lost_s"] = 0.25
        ctrl = self.controller(config, vehicle)
        reacquired = Detection("red_triangle", 480, 270, 500.0, 0.9, 3, 3, 0, 0, 0.5, 0.6, 0.9, 460, 250, 40, 40)
        ctrl.detector = FakeDetector([reacquired])
        ctrl.state = State.CENTER
        ctrl.current_target = "red_triangle"
        ctrl.last_detection = Detection("red_triangle", 300, 260, 400.0, 0.8, 3, 3, 0, 0, 0.5, 0.6, 0.9, 280, 240, 40, 40)
        ctrl.last_seen_at = time.monotonic() - 1.0
        ctrl.center_started_at = time.monotonic()
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.CENTER)
        self.assertEqual(ctrl.last_detection, reacquired)
        self.assertTrue(ctrl.last_seen_at > ctrl.state_started_at)

    def test_auto_bounce_aborts_after_grace_time(self):
        vehicle = FakeVehicle()
        vehicle.mode = "AUTO"
        config = self.config()
        config["safety"]["guided_auto_bounce_grace_s"] = 0.1
        ctrl = self.controller(config, vehicle)
        ctrl.state = State.CENTER
        ctrl.current_target = "red_triangle"
        ctrl.last_seen_at = time.monotonic()
        ctrl.center_started_at = time.monotonic()
        ctrl.guided_mode_lost_since = time.monotonic() - 1.0
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.WAITING_FOR_AUTO_RESUME)
        self.assertIsNone(ctrl.current_target)
        self.assertEqual(vehicle.mode_requests[-1], "AUTO")
        self.assertIn("GUIDED_MODE_LOST", ctrl.active_abort_reason)

    def test_finite_auto_bounce_limit_aborts_target(self):
        vehicle = FakeVehicle()
        vehicle.mode = "AUTO"
        config = self.config()
        config["safety"]["max_guided_auto_bounces_per_target"] = 1
        ctrl = self.controller(config, vehicle)
        ctrl.state = State.CENTER
        ctrl.current_target = "red_triangle"
        ctrl.last_seen_at = time.monotonic()
        ctrl.center_started_at = time.monotonic()
        ctrl.guided_bounce_count_for_target = 1
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.WAITING_FOR_AUTO_RESUME)
        self.assertIsNone(ctrl.current_target)
        self.assertEqual(vehicle.mode_requests[-1], "AUTO")
        self.assertIn("GUIDED_AUTO_BOUNCES", ctrl.active_abort_reason)

    def test_center_stands_down_on_external_mode_instead_of_forcing_auto(self):
        vehicle = FakeVehicle()
        vehicle.mode = "STABILIZE"
        ctrl = self.controller(vehicle=vehicle)
        ctrl.state = State.CENTER
        ctrl.current_target = "red_triangle"
        ctrl.last_detection = Detection("red_triangle", 450, 260, 400.0, 0.8, 3, 3, 0, 0, 0.5, 0.6, 0.9, 430, 240, 40, 40)
        ctrl.last_seen_at = time.monotonic()
        ctrl.center_started_at = time.monotonic()

        ctrl.update(self.blank_frame(), [], self.blank_masks())

        self.assertEqual(ctrl.state, State.WAITING_FOR_AUTO)
        self.assertIsNone(ctrl.current_target)
        self.assertEqual(vehicle.mode_requests, [])
        self.assertEqual(vehicle.velocities[-1], (0.0, 0.0, 0.0))
        self.assertTrue(ctrl.manual_override_latched)

        vehicle.mode = "AUTO"
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.WAITING_FOR_AUTO)
        self.assertIn("Pilot override latched", ctrl.status_message)

    def test_center_warning_keeps_guided_target_lock(self):
        vehicle = FakeVehicle()
        config = self.config()
        config["safety"]["center_warning_time_s"] = 0.1
        ctrl = self.controller(config, vehicle)
        ctrl.state = State.CENTER
        ctrl.current_target = "red_triangle"
        ctrl.last_detection = Detection(
            "red_triangle", 450, 260, 400.0, 0.8, 3, 3, 0, 0,
            0.5, 0.6, 0.9, 430, 240, 40, 40,
        )
        ctrl.last_seen_at = time.monotonic()
        ctrl.center_started_at = time.monotonic() - 1.0
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.CENTER)
        self.assertEqual(ctrl.current_target, "red_triangle")
        self.assertTrue(ctrl.center_warning_printed)

    def test_target_lost_after_timeout_keeps_guided_and_searches(self):
        vehicle = FakeVehicle()
        config = self.config()
        config["control"]["target_lost_timeout_s"] = 0.1
        ctrl = self.controller(config, vehicle)
        ctrl.detector = FakeDetector()
        ctrl.state = State.CENTER
        ctrl.current_target = "red_triangle"
        ctrl.last_detection = Detection("red_triangle", 450, 260, 400.0, 0.8, 3, 3, 0, 0, 0.5, 0.6, 0.9, 430, 240, 40, 40)
        ctrl.last_seen_at = time.monotonic() - 1.0
        ctrl.center_started_at = time.monotonic()
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.CENTER)
        self.assertEqual(ctrl.current_target, "red_triangle")

    def test_payload_waits_for_guided_instead_of_aborting(self):
        vehicle = FakeVehicle()
        vehicle.mode = "AUTO"
        ctrl = self.controller(vehicle=vehicle)
        ctrl.state = State.PAYLOAD
        ctrl.current_target = "red_triangle"
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.PAYLOAD)
        self.assertEqual(ctrl.current_target, "red_triangle")
        self.assertEqual(vehicle.mode_requests[-1], "GUIDED")
        self.assertEqual(vehicle.servos, [])
        self.assertNotIn("red_triangle", ctrl.completed_targets)

    def test_payload_stands_down_on_external_mode(self):
        vehicle = FakeVehicle()
        vehicle.mode = "LOITER"
        ctrl = self.controller(vehicle=vehicle)
        ctrl.state = State.PAYLOAD
        ctrl.current_target = "red_triangle"

        ctrl.update(self.blank_frame(), [], self.blank_masks())

        self.assertEqual(ctrl.state, State.WAITING_FOR_AUTO)
        self.assertIsNone(ctrl.current_target)
        self.assertEqual(vehicle.mode_requests, [])
        self.assertEqual(vehicle.servos, [])

    def test_camera_timeout_zeroes_velocity_and_returns_to_auto(self):
        vehicle = FakeVehicle()
        vehicle.mode = "GUIDED"
        config = self.config()
        config["control"]["altitude_control"] = "off"
        ctrl = self.controller(config, vehicle)
        ctrl.state = State.CENTER
        ctrl.current_target = "red_triangle"
        ctrl.last_frame_at = time.monotonic() - 3.0

        ctrl.handle_camera_frame_miss(time.monotonic())

        self.assertEqual(ctrl.state, State.WAITING_FOR_AUTO_RESUME)
        self.assertIsNone(ctrl.current_target)
        self.assertEqual(vehicle.velocities[-1], (0.0, 0.0, 0.0))
        self.assertEqual(vehicle.mode_requests[-1], "AUTO")
        self.assertIn("CAMERA_TIMEOUT", ctrl.active_abort_reason)

    def test_complete_state_resets_for_next_auto_run(self):
        vehicle = FakeVehicle()
        vehicle.mode = "AUTO"
        vehicle.mission_seq = 2
        ctrl = self.controller(vehicle=vehicle)
        ctrl.state = State.COMPLETE
        ctrl.completed_targets = {"red_triangle", "blue_hexagon"}
        ctrl.mission_done_count = 1
        ctrl.update(self.blank_frame(), [], self.blank_masks())
        self.assertEqual(ctrl.state, State.SEARCH)
        self.assertEqual(ctrl.completed_targets, set())
        self.assertEqual(ctrl.mission_done_count, 1)

    def test_mission_duration_is_warning_only(self):
        vehicle = FakeVehicle()
        vehicle.mode = "AUTO"
        config = self.config()
        config["mission"]["max_flight_time_s"] = 0.1
        ctrl = self.controller(config, vehicle)
        ctrl.state = State.SEARCH
        ctrl.mission_started_at = time.monotonic() - 1.0

        ctrl.update(self.blank_frame(), [], self.blank_masks())

        self.assertTrue(ctrl.mission_timeout_warned)
        self.assertNotIn("RTL", vehicle.mode_requests)

    def test_physical_payload_requires_ack_and_persists_attempt(self):
        vehicle = FakeVehicle()
        config = self.config()
        config["payload"]["simulate_only"] = False
        config["payload"].update(
            {
                "mechanism": "selector_servo",
                "red_payload_pwm": 1100,
                "neutral_pwm": 1500,
                "blue_payload_pwm": 1900,
            }
        )
        config["payload_state"] = {"enabled": True, "path": "placeholder.json"}
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        config["payload_state"]["path"] = str(Path(tempdir.name) / "payload.json")
        ctrl = self.controller(config, vehicle)
        ctrl.state = State.PAYLOAD
        ctrl.current_target = "red_triangle"
        ctrl.completed_targets = {"blue_hexagon"}
        started = time.monotonic()
        ctrl.last_detection = Detection(
            "red_triangle", 480, 270, 500.0, 0.9, 3, 3, 0, 0,
            0.5, 0.6, 0.9, 460, 250, 40, 40,
        )
        ctrl.last_frame_at = started
        ctrl.last_tracking_at = started
        ctrl.last_strong_geometry_at = started
        ctrl.last_center_error_px = 0.0
        ctrl.center_lock_completed_at = started
        ctrl.center_error_history.extend(
            [(started - 0.1, 0.0), (started, 0.0)]
        )

        ctrl.payload_action(started)
        self.assertFalse(ctrl.payload_release_accepted)
        state = json.loads(Path(config["payload_state"]["path"]).read_text(encoding="utf-8"))
        self.assertFalse(state["targets"]["red_triangle"]["payload_command_accepted"])
        self.assertTrue(state["targets"]["red_triangle"]["payload_release_attempted"])
        ctrl.payload_action(started + 0.1)
        self.assertTrue(ctrl.payload_release_accepted)
        state = json.loads(Path(config["payload_state"]["path"]).read_text(encoding="utf-8"))
        self.assertTrue(state["targets"]["red_triangle"]["payload_command_accepted"])
        self.assertTrue(state["targets"]["red_triangle"]["payload_release_attempted"])

        ctrl.payload_action(started + 1.1)
        ctrl.payload_action(started + 1.2)
        ctrl.payload_action(started + 2.0)
        self.assertIn("red_triangle", ctrl.completed_targets)
        self.assertIn("RTL", vehicle.mode_requests)


if __name__ == "__main__":
    unittest.main(verbosity=2)

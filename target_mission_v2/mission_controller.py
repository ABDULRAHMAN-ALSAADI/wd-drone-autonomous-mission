#!/usr/bin/env python3
"""Active target mission controller.

This is the main program for the rotary-wing second mission. It connects to
ArduPilot, reads the camera, confirms red-triangle/blue-hexagon targets, requests
GUIDED for centering, simulates or triggers the correct payload, resumes AUTO,
and requests RTL after both targets are done.

Normal tuning belongs in JSON config files, especially `parameter_config.json`
and `configs/real_pi_camera_module_3.json`.
"""
from __future__ import annotations

import argparse
import json
import math
import signal
import time
from collections import deque
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Optional

import cv2

from camera_sources import CameraLike, open_camera
from camera_worker import LatestFrameCamera
from config_validation import (
    DEFAULT_NAVIGATION,
    DEFAULT_SAFETY,
    navigation_config,
    optional_seconds_label,
    required_ardupilot_parameters,
    safety_config,
    validate_config,
)
from configuration import load_config
from overlay import draw_mission_overlay
from payload import (
    TARGET_PAYLOAD_COLOUR,
    load_payload_state,
    manage_payload_state,
    payload_colour_for_target,
    payload_output_for_target,
    perform_payload_action,
    persist_payload_release,
    verify_payload_geometry as verify_payload_geometry_frame,
)
from safety import (
    guidance_health_errors as evaluate_guidance_health,
    measurement_age,
    payload_release_gate_errors as evaluate_payload_release_gate,
    payload_safety_error as evaluate_payload_safety,
)
from vision import (
    Detection,
    HitTracker,
    ProcessedVisionFrame,
    create_detector,
)
from vehicle import (
    Vehicle,
    enforce_parameters,
    heartbeat_is_target_vehicle,
    heartbeat_is_vehicle,
)
from video_stream import MjpegFrameServer
from control import altitude_velocity_down


MISSION_OWNED_MODES = {"AUTO", "GUIDED"}


class State(str, Enum):
    WAITING_FOR_AUTO = "WAITING_FOR_AUTO"
    SEARCH = "SEARCH"
    WAITING_FOR_GUIDED = "WAITING_FOR_GUIDED"
    CENTER = "CENTER"
    PAYLOAD = "PAYLOAD"
    WAITING_FOR_AUTO_RESUME = "WAITING_FOR_AUTO_RESUME"
    WAITING_FOR_RTL = "WAITING_FOR_RTL"
    COMPLETE = "COMPLETE"


def clamp_vector(x: float, y: float, limit: float) -> tuple[float, float]:
    magnitude = math.hypot(x, y)
    if magnitude <= limit or magnitude <= 1e-9:
        return x, y
    scale = limit / magnitude
    return x * scale, y * scale


def horizontal_distance_m(
    start: tuple[float, float], current: tuple[float, float]
) -> float:
    lat1, lon1 = map(math.radians, start)
    lat2, lon2 = map(math.radians, current)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    return 6_371_000.0 * 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


class Controller:
    def __init__(self, config: dict[str, Any], vehicle: Vehicle, camera: CameraLike) -> None:
        self.config = config
        self.vehicle = vehicle
        self.camera = camera
        vcfg = config["vision"]
        self.detector = create_detector(vcfg)
        self.tracker = HitTracker(
            vcfg["required_hits"],
            vcfg["confirmation_window_s"],
            vcfg["max_lock_jump_px"],
            min_duration_s=float(vcfg.get("confirmation_min_duration_s", 0.0)),
            min_hit_ratio=float(vcfg.get("confirmation_min_hit_ratio", 0.60)),
            max_missing_ratio=float(
                vcfg.get("confirmation_max_missing_ratio", 0.40)
            ),
            max_center_std_px=float(vcfg.get("confirmation_max_center_std_px", 80.0)),
            max_area_cv=float(vcfg.get("confirmation_max_area_cv", 0.75)),
            max_bbox_cv=float(vcfg.get("confirmation_max_bbox_cv", 0.75)),
            max_area_jump_ratio=float(
                vcfg.get("confirmation_max_area_jump_ratio", 4.0)
            ),
            min_colour_score=float(vcfg.get("confirmation_min_colour_score", 0.0)),
            min_shape_score=float(vcfg.get("confirmation_min_shape_score", 0.0)),
            min_total_score=float(vcfg.get("confirmation_min_total_score", 0.0)),
        )
        self.safety = safety_config(config)
        self.navigation = navigation_config(config)
        self.state = State.WAITING_FOR_AUTO
        self.state_started_at = time.monotonic()
        self.last_mode_request_at = 0.0
        self.last_velocity_at = 0.0
        self.last_velocity_command = (0.0, 0.0)
        self.last_search_speed_request_at = 0.0
        self.mission_started_at: Optional[float] = None
        self.last_log_flush_at = time.monotonic()
        self.last_sample_log_at = 0.0
        self.stop_requested = False
        self.completed_targets: set[str] = set()
        self.completed_center_errors: dict[str, float] = {}
        self.current_target: Optional[str] = None
        self.last_detection: Optional[Detection] = None
        self.last_seen_at = 0.0
        self.last_strong_geometry_at = 0.0
        self.last_payload_geometry_at = 0.0
        self.last_payload_geometry_detection: Optional[Detection] = None
        self.last_payload_geometry_error_px: Optional[float] = None
        self.last_payload_geometry_reason = "not checked"
        self.last_vision_result_at = 0.0
        self.last_tracking_at = 0.0
        self.last_full_reacquire_at = 0.0
        self.center_lock_completed_at = 0.0
        self.center_error_history: Deque[tuple[float, float]] = deque()
        self.centered_since: Optional[float] = None
        self.center_started_at: Optional[float] = None
        self.guided_mode_lost_since: Optional[float] = None
        self.guided_bounce_count = 0
        self.guided_bounce_count_for_target = 0
        self.last_guided_bounce_print_at = 0.0
        self.last_center_error_px: Optional[float] = None
        self.last_center_forward: Optional[float] = None
        self.last_center_right: Optional[float] = None
        self.last_frame_at = time.monotonic()
        self.last_camera_timeout_print_at = 0.0
        self.camera_timeout_active = False
        self.target_loss_active = False
        self.active_abort_reason: Optional[str] = None
        self.guidance_health_failed_at: Optional[float] = None
        self.payload_block_started_at: Optional[float] = None
        self.manual_override_latched = False
        self.manual_override_reason = ""
        self.mission_timeout_warned = False
        self.filtered_center: Optional[tuple[float, float]] = None
        self.center_origin: Optional[tuple[float, float]] = None
        self.center_best_error_px: Optional[float] = None
        self.center_last_progress_at: Optional[float] = None
        self.center_direction_signature = (0, 0)
        self.center_direction_started_at: Optional[float] = None
        self.center_warning_printed = False
        self.status_message = "Waiting for AUTO at search waypoint"
        self.payload_started = False
        self.payload_reset = False
        self.payload_release_sent_at: Optional[float] = None
        self.payload_release_accepted = False
        self.payload_reset_sent_at: Optional[float] = None
        self.payload_reset_accepted = False
        self.payload_failed = False
        self.last_payload_block_reason = ""
        self.mission_done_count = 0
        log_dir = Path(config["logging"]["directory"])
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = (log_dir / f"mission-v2-{time.strftime('%Y%m%d-%H%M%S')}.jsonl").open("a", encoding="utf-8")
        self.payload_state_config = config.get("payload_state", {"enabled": False})
        self.payload_state_path: Optional[Path] = None
        if bool(self.payload_state_config.get("enabled", False)):
            self.payload_state_path = Path(str(self.payload_state_config["path"]))
            self._load_payload_state()

    def event(self, name: str, **details: Any) -> None:
        print(f"[EVENT] {name}" + (f" {details}" if details else ""))
        self.log_file.write(json.dumps({
            "time": time.time(),
            "event": name,
            "state": self.state.value,
            "mode": self.vehicle.mode,
            **details,
        }, sort_keys=True) + "\n")

    def _load_payload_state(self) -> None:
        load_payload_state(self)

    def _persist_payload_release(self, target: str, payload_colour: str, accepted: bool) -> None:
        persist_payload_release(self, target, payload_colour, accepted)

    def transition(self, state: State, reason: str) -> None:
        print(f"[STATE] {self.state.value} -> {state.value}: {reason}")
        self.state = state
        self.state_started_at = time.monotonic()
        self.centered_since = None
        self.center_started_at = self.state_started_at if state == State.CENTER else None
        self.guided_mode_lost_since = None
        self.status_message = reason
        if state == State.CENTER:
            self.filtered_center = None
            self.center_best_error_px = None
            self.center_last_progress_at = self.state_started_at
            self.center_direction_signature = (0, 0)
            self.center_direction_started_at = None
            self.center_warning_printed = False
            self.center_error_history.clear()
            self.camera_timeout_active = False
            self.target_loss_active = False
            self.active_abort_reason = None
            self.guidance_health_failed_at = None
            self.payload_block_started_at = None
            lat = getattr(self.vehicle, "latitude_deg", None)
            lon = getattr(self.vehicle, "longitude_deg", None)
            self.center_origin = (lat, lon) if lat is not None and lon is not None else None
        elif state == State.PAYLOAD:
            # Each target owns a separate payload transaction.  Do not let the
            # completed first target's command timestamps satisfy the second.
            self.payload_started = False
            self.payload_reset = False
            self.payload_release_sent_at = None
            self.payload_release_accepted = False
            self.payload_reset_sent_at = None
            self.payload_reset_accepted = False
            self.payload_failed = False
            self.payload_block_started_at = None
            self.last_payload_block_reason = ""
            self.last_payload_geometry_at = 0.0
            self.last_payload_geometry_detection = None
            self.last_payload_geometry_error_px = None
            self.last_payload_geometry_reason = "awaiting full-resolution check"
        elif state not in {State.PAYLOAD}:
            self.center_origin = None
        if state == State.SEARCH and self.mission_started_at is None:
            self.mission_started_at = self.state_started_at
        if state == State.SEARCH:
            self.apply_search_speed_policy()

    def incomplete_targets(self) -> set[str]:
        return {"red_triangle", "blue_hexagon"} - self.completed_targets

    def reset_for_next_mission(self, reason: str) -> None:
        self.completed_targets.clear()
        self.completed_center_errors.clear()
        self.current_target = None
        self.last_detection = None
        self.last_seen_at = 0.0
        self.last_strong_geometry_at = 0.0
        self.last_payload_geometry_at = 0.0
        self.last_payload_geometry_detection = None
        self.last_payload_geometry_error_px = None
        self.last_payload_geometry_reason = "not checked"
        self.last_vision_result_at = 0.0
        self.last_tracking_at = 0.0
        self.last_full_reacquire_at = 0.0
        self.center_lock_completed_at = 0.0
        self.center_error_history.clear()
        self.center_direction_signature = (0, 0)
        self.center_direction_started_at = None
        self.centered_since = None
        self.center_started_at = None
        self.guided_mode_lost_since = None
        self.guided_bounce_count_for_target = 0
        self.last_center_error_px = None
        self.last_center_forward = None
        self.last_center_right = None
        self.payload_started = False
        self.payload_reset = False
        self.payload_release_sent_at = None
        self.payload_release_accepted = False
        self.payload_reset_sent_at = None
        self.payload_reset_accepted = False
        self.payload_failed = False
        self.last_payload_block_reason = ""
        self.camera_timeout_active = False
        self.target_loss_active = False
        self.active_abort_reason = None
        self.guidance_health_failed_at = None
        self.payload_block_started_at = None
        self.mission_started_at = time.monotonic()
        self.mission_timeout_warned = False
        self.manual_override_latched = False
        self.manual_override_reason = ""
        self.tracker.reset()
        reset_tracking = getattr(self.detector, "reset_tracking", None)
        if callable(reset_tracking):
            reset_tracking()
        self.transition(State.SEARCH, reason)

    def altitude_down(self) -> float:
        if self.config["control"].get("altitude_control", "off") == "off":
            return 0.0
        c = self.config["control"]
        return altitude_velocity_down(
            self.vehicle.relative_alt_m,
            float(self.config["mission"]["survey_altitude_m"]),
            float(c["altitude_tolerance_m"]),
            float(c["altitude_kp"]),
            float(c["altitude_max_speed_m_s"]),
        )

    def send_velocity(self, forward: float, right: float, down: float, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self.last_velocity_at >= 1.0 / float(self.config["control"]["command_rate_hz"]):
            max_guided_speed = self.safety.get("max_guided_speed_m_s")
            if max_guided_speed is not None:
                forward, right = clamp_vector(forward, right, float(max_guided_speed))
            accel_limit = self.safety.get("max_command_accel_m_s2")
            if not force and accel_limit is not None and self.last_velocity_at > 0.0:
                dt = max(1e-3, now - self.last_velocity_at)
                max_delta = float(accel_limit) * dt
                delta_f = forward - self.last_velocity_command[0]
                delta_r = right - self.last_velocity_command[1]
                delta_f, delta_r = clamp_vector(delta_f, delta_r, max_delta)
                forward = self.last_velocity_command[0] + delta_f
                right = self.last_velocity_command[1] + delta_r
            self.vehicle.send_body_velocity(forward, right, down)
            self.last_velocity_at = now
            self.last_velocity_command = (forward, right)

    def request_mode_repeated(self, mode: str, force: bool = False) -> None:
        if self.vehicle.mode == mode:
            return
        now = time.monotonic()
        interval = self.safety.get("mode_retry_interval_s")
        retry_interval_s = 1.0 if interval is None else float(interval)
        if force or now - self.last_mode_request_at >= retry_interval_s:
            self.vehicle.set_mode(mode)
            self.last_mode_request_at = now

    def apply_search_speed_policy(self) -> None:
        if self.navigation["search_speed_source"] != "companion_do_change_speed":
            return
        now = time.monotonic()
        if now - self.last_search_speed_request_at < 1.0:
            return
        self.vehicle.set_ground_speed(float(self.navigation["search_speed_m_s"]))
        self.last_search_speed_request_at = now

    def resize(self, frame):
        width = int(self.config["vision"]["process_width"])
        if width <= 0 or frame.shape[1] == width:
            return frame
        scale = width / frame.shape[1]
        return cv2.resize(frame, (width, max(1, round(frame.shape[0] * scale))), interpolation=cv2.INTER_AREA)

    def desired_drop_point(self, width: int, height: int) -> tuple[float, float]:
        control = self.config["control"]
        normalized_x = control.get("desired_drop_x_normalized")
        normalized_y = control.get("desired_drop_y_normalized")
        pixel_x = control.get("desired_drop_pixel_x")
        pixel_y = control.get("desired_drop_pixel_y")
        if pixel_x is not None and pixel_y is not None:
            return float(pixel_x), float(pixel_y)
        if normalized_x is not None and normalized_y is not None:
            return float(normalized_x) * width, float(normalized_y) * height
        return width / 2.0, height / 2.0

    def centre_velocity(self, detection: Detection, width: int, height: int) -> tuple[float, float, float]:
        c = self.config["control"]
        alpha = float(self.safety.get("center_filter_alpha", 0.35))
        observed = (float(detection.center_x), float(detection.center_y))
        if self.filtered_center is None:
            self.filtered_center = observed
        else:
            self.filtered_center = (
                alpha * observed[0] + (1.0 - alpha) * self.filtered_center[0],
                alpha * observed[1] + (1.0 - alpha) * self.filtered_center[1],
            )
        desired_x, desired_y = self.desired_drop_point(width, height)
        ex = self.filtered_center[0] - desired_x
        ey = self.filtered_center[1] - desired_y
        forward = float(c["image_y_to_forward_sign"]) * float(c["center_kp"]) * ey / (height / 2.0)
        right = float(c["image_x_to_right_sign"]) * float(c["center_kp"]) * ex / (width / 2.0)
        maximum = float(c["center_max_speed_m_s"])
        forward, right = clamp_vector(forward, right, maximum)
        return forward, right, math.hypot(ex, ey)

    def center_tolerance_px(self) -> float:
        target = self.current_target or ""
        per_target = self.config["control"].get("center_tolerance_px_by_target", {})
        if target in per_target:
            return float(per_target[target])
        return float(self.config["control"]["center_tolerance_px"])

    def search_speed_label(self) -> str:
        if self.navigation["search_speed_source"] == "companion_do_change_speed":
            return f"companion {float(self.navigation['search_speed_m_s']):.1f}m/s"
        return "QGC mission"

    def active_target_phase(self) -> bool:
        return self.state in {State.WAITING_FOR_GUIDED, State.CENTER, State.PAYLOAD} and self.current_target is not None

    @staticmethod
    def _age(now: float, timestamp: Optional[float]) -> Optional[float]:
        return measurement_age(now, timestamp)

    def guidance_health_errors(self, expected_mode: str, now: Optional[float] = None) -> list[str]:
        return evaluate_guidance_health(self, expected_mode, now)

    def latch_pilot_override(self, reason: str) -> None:
        if not self.manual_override_latched:
            self.event("PILOT_OVERRIDE_LATCHED", reason=reason)
        self.manual_override_latched = True
        self.manual_override_reason = reason
        self.stand_down_for_external_mode(reason, latch=False)

    def stand_down_for_external_mode(self, reason: str, latch: bool = True) -> None:
        if latch:
            self.manual_override_latched = True
            self.manual_override_reason = reason
            self.event("PILOT_OVERRIDE_LATCHED", reason=reason)
        print(f"[EXTERNAL MODE] {reason}; standing down")
        self.send_velocity(0.0, 0.0, 0.0, force=True)
        self.current_target = None
        self.last_detection = None
        self.last_seen_at = 0.0
        self.centered_since = None
        self.center_started_at = None
        self.guided_mode_lost_since = None
        self.tracker.reset()
        self.transition(State.WAITING_FOR_AUTO, f"{reason}; pilot owns vehicle")

    def handle_camera_frame_miss(self, now: float) -> None:
        timeout_s = self.safety.get("camera_frame_timeout_s")
        if timeout_s is None or not self.active_target_phase():
            return
        missed_for = now - self.last_frame_at
        if missed_for < float(timeout_s):
            return
        self.status_message = f"Camera frame timeout {missed_for:.1f}s; aborting target"
        self.camera_timeout_active = True
        if self.vehicle.mode in MISSION_OWNED_MODES:
            self.send_velocity(0.0, 0.0, self.altitude_down(), force=True)
        if now - self.last_camera_timeout_print_at >= 1.0:
            print(f"[CAMERA TIMEOUT] no frame for {missed_for:.1f}s during {self.state.value}")
            self.last_camera_timeout_print_at = now
        self.event(
            "CAMERA_TIMEOUT",
            target=self.current_target,
            missed_for_s=round(missed_for, 2),
        )
        self.abandon_active_target(now, f"CAMERA_TIMEOUT {missed_for:.1f}s")

    def search_gate_status(self, now: Optional[float] = None) -> tuple[bool, str]:
        mission = self.config["mission"]
        if not bool(mission.get("search_enabled", True)):
            return False, "search disabled by mission profile"
        rc_channel = mission.get("search_enable_rc_channel")
        if rc_channel is not None:
            timestamp = time.monotonic() if now is None else now
            channel = int(rc_channel)
            value = self.vehicle.rc_channels.get(channel)
            threshold = int(mission.get("search_enable_pwm_min", 1700))
            rc_age = self._age(timestamp, getattr(self.vehicle, "last_rc_at", None))
            if rc_age is not None and rc_age > float(mission.get("rc_timeout_s", 2.0)):
                return False, f"RC channel data stale ({rc_age:.1f}s)"
            if value is None:
                return False, f"waiting for RC{channel} mission-enable PWM"
            if value < threshold:
                return False, f"RC{channel}={value} below enable threshold {threshold}"
            return True, f"RC{channel}={value} enabled"
        return True, "enabled by config"

    def guided_displacement_m(self) -> Optional[float]:
        lat = getattr(self.vehicle, "latitude_deg", None)
        lon = getattr(self.vehicle, "longitude_deg", None)
        if self.center_origin is None or lat is None or lon is None:
            return None
        return horizontal_distance_m(self.center_origin, (lat, lon))

    def auto_search_start_ready(self) -> tuple[bool, str]:
        m = self.config["mission"]
        if not self.vehicle.armed:
            return False, "waiting for arm"
        if self.vehicle.mode != "AUTO":
            return False, f"waiting for AUTO, current mode {self.vehicle.mode}"
        if self.vehicle.mission_seq is None:
            return False, "waiting for mission waypoint"
        if self.vehicle.mission_seq < int(m["search_start_wp"]):
            return False, f"waiting for search waypoint {m['search_start_wp']}, current {self.vehicle.mission_seq}"
        enabled, reason = self.search_gate_status()
        if not enabled:
            return False, f"search blocked: {reason}"
        return True, f"AUTO item {self.vehicle.mission_seq}; {reason}"

    def payload_safety_error(self) -> Optional[str]:
        return evaluate_payload_safety(self)

    def center_error_variance_px(self, now: Optional[float] = None) -> Optional[float]:
        timestamp = time.monotonic() if now is None else float(now)
        window_s = float(self.safety.get("center_variance_window_s", 1.5))
        while self.center_error_history and timestamp - self.center_error_history[0][0] > window_s:
            self.center_error_history.popleft()
        if len(self.center_error_history) < 2:
            return None
        values = [value for _, value in self.center_error_history]
        mean = sum(values) / len(values)
        return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))

    def single_direction_elapsed_s(
        self,
        now: float,
        forward: float,
        right: float,
    ) -> float:
        deadband = float(self.safety.get("single_direction_deadband_m_s", 0.05))
        signature = (
            1 if forward > deadband else -1 if forward < -deadband else 0,
            1 if right > deadband else -1 if right < -deadband else 0,
        )
        if signature == (0, 0):
            self.center_direction_signature = signature
            self.center_direction_started_at = None
            return 0.0
        if (
            signature != self.center_direction_signature
            or self.center_direction_started_at is None
        ):
            self.center_direction_signature = signature
            self.center_direction_started_at = now
            return 0.0
        return now - self.center_direction_started_at

    def verify_payload_geometry(
        self,
        full_resolution_frame: Any,
        process_width: int,
        process_height: int,
        now: float,
        frame_id: int = 0,
        captured_at_s: Optional[float] = None,
        received_at_s: Optional[float] = None,
    ) -> bool:
        return verify_payload_geometry_frame(
            self,
            full_resolution_frame,
            process_width,
            process_height,
            now,
            frame_id,
            captured_at_s,
            received_at_s,
        )

    def payload_release_gate_errors(self, now: Optional[float] = None) -> list[str]:
        return evaluate_payload_release_gate(self, now)

    def active_target_abort_mode(self) -> str:
        return str(self.safety.get("active_target_abort_mode", "AUTO"))

    def abandon_active_target(self, now: float, reason: str) -> None:
        mode = self.active_target_abort_mode()
        print(f"[TARGET ABORT] {reason}; requesting {mode}")
        self.active_abort_reason = reason
        self.event("TARGET_ABORT", target=self.current_target, reason=reason, recovery_mode=mode)
        self.send_velocity(0.0, 0.0, self.altitude_down())
        self.current_target = None
        self.last_detection = None
        self.tracker.reset()
        reset_tracking = getattr(self.detector, "reset_tracking", None)
        if callable(reset_tracking):
            reset_tracking()
        self.vehicle.set_mode(mode)
        self.last_mode_request_at = now
        self.transition(State.WAITING_FOR_AUTO_RESUME if mode == "AUTO" else State.WAITING_FOR_RTL,
                        f"{reason}; abort to {mode}")

    def payload_action(self, now: float) -> None:
        perform_payload_action(
            self,
            now,
            State.WAITING_FOR_AUTO_RESUME,
            State.WAITING_FOR_RTL,
        )

    def _track_active_target(
        self,
        frame: Any,
        processed: Optional[ProcessedVisionFrame],
        masks: dict[str, Any],
        now: float,
    ) -> tuple[Optional[Detection], dict[str, Any]]:
        """Track only the locked target using this frame's existing masks."""
        if self.last_detection is None or self.current_target is None:
            return None, masks

        if processed is not None and hasattr(self.detector, "track_processed"):
            tracked = self.detector.track_processed(
                processed,
                self.current_target,
                (self.last_detection.center_x, self.last_detection.center_y),
                float(self.config["vision"]["max_lock_jump_px"]),
            )
            masks = processed.masks
        else:
            tracked, masks = self.detector.track_colour(
                frame,
                self.current_target,
                (self.last_detection.center_x, self.last_detection.center_y),
                float(self.config["vision"]["max_lock_jump_px"]),
            )

        if tracked is None:
            if (
                hasattr(self.detector, "tracking_state")
                and self.detector.tracking_state is None
            ):
                reason = str(
                    getattr(
                        self.detector,
                        "last_lock_drop_reason",
                        "strict geometry lock expired",
                    )
                )
                self.event(
                    "VISION_LOCK_DROPPED",
                    target=self.current_target,
                    reason=reason,
                )
                self.last_detection = None
                self.last_payload_geometry_at = 0.0
                self.last_payload_geometry_detection = None
                self.last_payload_geometry_error_px = None
                self.last_payload_geometry_reason = reason
            return None, masks
        self.last_detection = tracked
        self.last_seen_at = now
        self.last_tracking_at = now
        self.last_vision_result_at = now
        if str(getattr(tracked, "source", "")).startswith("geometry"):
            self.last_strong_geometry_at = now
        return tracked, masks

    def _search_processed_or_legacy(
        self,
        frame: Any,
        processed: Optional[ProcessedVisionFrame],
        allowed_targets: set[str],
    ) -> tuple[list[Detection], dict[str, Any]]:
        """Search selected classes without repeating HSV or morphology."""
        if processed is not None and hasattr(self.detector, "search_processed"):
            return (
                self.detector.search_processed(processed, allowed_targets),
                processed.masks,
            )
        detections, masks = self.detector.search(frame)
        return [
            item for item in detections if item.target in allowed_targets
        ], masks

    def update(
        self,
        frame: Any,
        detections: list[Detection],
        masks: dict[str, Any],
        processed: Optional[ProcessedVisionFrame] = None,
        full_resolution_frame: Optional[Any] = None,
        frame_id: int = 0,
        captured_at_s: Optional[float] = None,
        received_at_s: Optional[float] = None,
    ):
        now = time.monotonic()
        m = self.config["mission"]
        c = self.config["control"]
        height, width = frame.shape[:2]

        if self.manual_override_latched:
            if not self.vehicle.armed:
                self.manual_override_latched = False
                self.manual_override_reason = ""
                self.mission_started_at = None
                self.tracker.reset()
                self.transition(State.WAITING_FOR_AUTO, "pilot override reset after disarm")
            else:
                self.status_message = f"Pilot override latched: {self.manual_override_reason}"
            return detections, masks

        if (
            self.mission_started_at is not None
            and self.state not in {State.WAITING_FOR_RTL, State.COMPLETE}
            and now - self.mission_started_at > float(m.get("max_flight_time_s", 600.0))
            and not self.mission_timeout_warned
        ):
            self.mission_timeout_warned = True
            self.event(
                "MISSION_DURATION_WARNING",
                elapsed_s=round(now - self.mission_started_at, 1),
                note="warning only; pilot and ArduPilot retain recovery authority",
            )

        if self.state == State.WAITING_FOR_AUTO:
            ready, reason = self.auto_search_start_ready()
            self.status_message = reason
            if ready:
                self.transition(State.SEARCH, reason)

        elif self.state == State.SEARCH:
            enabled, reason = self.search_gate_status()
            if not enabled:
                self.tracker.reset()
                self.transition(State.WAITING_FOR_AUTO, reason)
            elif self.vehicle.mode != "AUTO":
                self.transition(State.WAITING_FOR_AUTO, f"left AUTO: {self.vehicle.mode}")
            else:
                confirmed = self.tracker.update(detections, self.incomplete_targets(), now)
                if confirmed:
                    health_errors = self.guidance_health_errors("AUTO", now)
                    if health_errors:
                        reason = "; ".join(health_errors)
                        self.status_message = f"Target seen but GUIDED blocked: {reason}"
                        self.event("GUIDED_ENTRY_BLOCKED", target=confirmed.target, reason=reason)
                        self.tracker.reset()
                        return detections, masks
                    self.current_target = confirmed.target
                    self.last_detection = next(x for x in detections if x.target == confirmed.target)
                    self.last_seen_at = now
                    self.last_tracking_at = now
                    self.last_vision_result_at = now
                    self.last_strong_geometry_at = now
                    begin_tracking = getattr(self.detector, "begin_tracking", None)
                    if callable(begin_tracking):
                        begin_tracking(self.last_detection, now)
                    self.status_message = f"Confirmed {confirmed.target}; requesting GUIDED"
                    print(f"[TARGET CONFIRMED] {confirmed.target} hits={confirmed.hits}/{self.config['vision']['required_hits']} confidence={confirmed.confidence:.2f}")
                    self.vehicle.set_mode("GUIDED")
                    self.last_mode_request_at = now
                    self.transition(State.WAITING_FOR_GUIDED, "target confirmed")

        elif self.state == State.WAITING_FOR_GUIDED:
            tracked, masks = self._track_active_target(
                frame,
                processed,
                masks,
                now,
            )
            if tracked is not None:
                detections = [tracked]
            if self.vehicle.mode == "GUIDED":
                self.send_velocity(0.0, 0.0, self.altitude_down())
                self.transition(State.CENTER, "GUIDED confirmed; centering target")
            elif self.vehicle.mode != "AUTO":
                self.stand_down_for_external_mode(f"external mode {self.vehicle.mode} before GUIDED lock")
                return detections, masks
            elif now - self.state_started_at > float(
                self.safety.get(
                    "max_guided_entry_time_s",
                    m["mode_change_timeout_s"],
                )
            ):
                self.abandon_active_target(
                    now,
                    "GUIDED_ENTRY_TIMEOUT",
                )
                return detections, masks
            else:
                self.request_mode_repeated("GUIDED")

        elif self.state == State.CENTER:
            if self.vehicle.mode != "GUIDED":
                if self.vehicle.mode == "AUTO":
                    if self.guided_mode_lost_since is None:
                        self.guided_mode_lost_since = now
                        self.guided_bounce_count += 1
                        self.guided_bounce_count_for_target += 1
                    elapsed = now - self.guided_mode_lost_since
                    grace_s = self.safety.get("guided_auto_bounce_grace_s")
                    grace_label = optional_seconds_label(grace_s)
                    self.status_message = (
                        f"GUIDED lock: AUTO {elapsed:.1f}s/{grace_label} "
                        f"bounce {self.guided_bounce_count_for_target}; forcing GUIDED"
                    )
                    if now - self.last_guided_bounce_print_at >= 1.0:
                        print(
                            f"[GUIDED BOUNCE] target={self.current_target} "
                            f"auto_for={elapsed:.1f}s/{grace_label} "
                            f"target_count={self.guided_bounce_count_for_target} "
                            f"total_count={self.guided_bounce_count}; forcing GUIDED"
                        )
                        self.last_guided_bounce_print_at = now
                    max_bounces = self.safety.get(
                        "max_guided_auto_bounces_per_target"
                    )
                    if (
                        max_bounces is not None
                        and self.guided_bounce_count_for_target > int(max_bounces)
                    ):
                        self.abandon_active_target(
                            now,
                            f"GUIDED_AUTO_BOUNCES {self.guided_bounce_count_for_target}",
                        )
                        return detections, masks
                    if grace_s is not None and elapsed > float(grace_s):
                        self.abandon_active_target(
                            now,
                            f"GUIDED_MODE_LOST {elapsed:.1f}s",
                        )
                        return detections, masks
                    self.request_mode_repeated("GUIDED", force=True)
                    return detections, masks
                self.stand_down_for_external_mode(f"external mode {self.vehicle.mode} during centering")
                return detections, masks
            self.guided_mode_lost_since = None
            self.request_mode_repeated("GUIDED")
            enabled, gate_reason = self.search_gate_status(now=now)
            if not enabled:
                self.latch_pilot_override(f"autonomy enable removed: {gate_reason}")
                return detections, masks
            health_errors = self.guidance_health_errors("GUIDED", now)
            if health_errors:
                reason = "; ".join(health_errors)
                if self.guidance_health_failed_at is None:
                    self.guidance_health_failed_at = now
                    self.event(
                        "GUIDANCE_HEALTH_FAILED",
                        target=self.current_target,
                        reason=reason,
                    )
                failed_for = now - self.guidance_health_failed_at
                self.status_message = (
                    f"GUIDED health failure {failed_for:.1f}s: {reason}"
                )
                self.send_velocity(0.0, 0.0, self.altitude_down(), force=True)
                if failed_for >= float(
                    self.safety.get("guidance_health_grace_s", 2.0)
                ):
                    self.abandon_active_target(
                        now,
                        f"GUIDANCE_HEALTH {reason}",
                    )
                return detections, masks
            self.guidance_health_failed_at = None
            warning_s = self.safety.get("center_warning_time_s")
            if (
                warning_s is not None
                and self.center_started_at is not None
                and now - self.center_started_at > float(warning_s)
                and not self.center_warning_printed
            ):
                self.center_warning_printed = True
                self.event(
                    "CENTERING_SLOW_WARNING",
                    target=self.current_target,
                    elapsed_s=round(now - self.center_started_at, 1),
                    note="warning only; centering continues",
                )
            elapsed_center = (
                0.0 if self.center_started_at is None else now - self.center_started_at
            )
            max_center_time = self.safety.get("max_center_time_s")
            if max_center_time is not None and elapsed_center > float(max_center_time):
                self.abandon_active_target(
                    now,
                    f"CENTER_TIMEOUT {elapsed_center:.1f}s",
                )
                return detections, masks
            displacement = self.guided_displacement_m()
            max_displacement = self.safety.get("max_guided_displacement_m")
            if displacement is not None and max_displacement is not None and displacement > float(max_displacement):
                self.abandon_active_target(
                    now,
                    f"GUIDED_DISPLACEMENT {displacement:.1f}m>{float(max_displacement):.1f}m",
                )
                return detections, masks
            fresh_detection = False
            tracked, masks = self._track_active_target(
                frame,
                processed,
                masks,
                now,
            )
            if tracked is not None:
                detections = [tracked]
                fresh_detection = True
            lost_for = now - self.last_seen_at if self.last_seen_at else float("inf")
            reacquire_interval_s = float(c.get("reacquire_interval_s", 0.5))
            if (
                not fresh_detection
                and self.current_target
                and lost_for >= float(c.get("reacquire_after_lost_s", 0.25))
                and now - self.last_full_reacquire_at >= reacquire_interval_s
            ):
                search_detections, masks = self._search_processed_or_legacy(
                    frame,
                    processed,
                    {self.current_target},
                )
                self.last_full_reacquire_at = now
                matches = [
                    item
                    for item in search_detections
                    if item.target == self.current_target
                ]
                if matches:
                    reacquired = max(matches, key=lambda item: item.confidence)
                    self.last_detection = reacquired
                    self.last_seen_at = now
                    self.last_tracking_at = now
                    self.last_vision_result_at = now
                    self.last_strong_geometry_at = now
                    begin_tracking = getattr(self.detector, "begin_tracking", None)
                    if callable(begin_tracking):
                        begin_tracking(reacquired, now)
                    detections = [reacquired]
                    fresh_detection = True
                    self.status_message = f"Reacquired {self.current_target}; centering"
                else:
                    detections = search_detections
            lost_for = now - self.last_seen_at if self.last_seen_at else float("inf")
            lost_timeout_s = float(
                self.safety.get(
                    "max_lost_detection_s",
                    c["target_lost_timeout_s"],
                )
            )
            if not fresh_detection:
                self.centered_since = None
                self.target_loss_active = (
                    self.last_seen_at <= 0.0 or lost_for > lost_timeout_s
                )
                if self.target_loss_active:
                    self.status_message = (
                        f"Target lock lost for {lost_for:.1f}s; "
                        "holding GUIDED and searching"
                    )
                else:
                    self.status_message = (
                        f"Looking for {self.current_target} in GUIDED "
                        f"{lost_for:.1f}/{lost_timeout_s:.1f}s"
                    )
                self.send_velocity(0.0, 0.0, self.altitude_down())
                return detections, masks
            self.target_loss_active = False
            forward, right, distance = self.centre_velocity(self.last_detection, width, height)
            minimum_progress = float(self.safety.get("center_min_progress_px", 8.0))
            if self.center_best_error_px is None or distance <= self.center_best_error_px - minimum_progress:
                self.center_best_error_px = distance
                self.center_last_progress_at = now
            progress_window = self.safety.get("center_progress_window_s")
            stagnant_for = 0.0 if self.center_last_progress_at is None else now - self.center_last_progress_at
            slow_progress = progress_window is not None and stagnant_for > float(progress_window)
            self.last_center_error_px = distance
            self.last_center_forward = forward
            self.last_center_right = right
            self.center_error_history.append((now, distance))
            direction_elapsed = self.single_direction_elapsed_s(now, forward, right)
            max_direction_time = self.safety.get("max_single_direction_time_s")
            if (
                max_direction_time is not None
                and direction_elapsed > float(max_direction_time)
            ):
                self.abandon_active_target(
                    now,
                    f"SINGLE_DIRECTION_TIMEOUT {direction_elapsed:.1f}s",
                )
                return detections, masks
            tolerance_px = self.center_tolerance_px()
            if distance <= tolerance_px:
                forward = right = 0.0
                if self.centered_since is None:
                    self.centered_since = now
                    self.status_message = f"Center lock started on {self.current_target}: err={distance:.0f}px"
                elif now - self.centered_since >= float(c["center_hold_s"]):
                    self.status_message = f"Centering complete on {self.current_target}: err={distance:.0f}px"
                    self.event(
                        "CENTER_LOCK_ACCEPTED",
                        target=self.current_target,
                        error_px=round(distance, 1),
                        tolerance_px=round(tolerance_px, 1),
                    )
                    self.payload_started = False
                    self.payload_reset = False
                    self.center_lock_completed_at = now
                    self.transition(State.PAYLOAD, "centred; payload")
                else:
                    held = now - self.centered_since
                    self.status_message = f"Holding center on {self.current_target}: err={distance:.0f}px hold={held:.1f}/{float(c['center_hold_s']):.1f}s"
            else:
                self.centered_since = None
                self.status_message = (
                    f"Centering {self.current_target}: err={distance:.0f}px "
                    f"tol={tolerance_px:.0f}px fwd={forward:.2f} right={right:.2f}"
                )
                if slow_progress:
                    self.abandon_active_target(
                        now,
                        f"NO_CENTER_PROGRESS {stagnant_for:.1f}s",
                    )
                    return detections, masks
            self.send_velocity(forward, right, self.altitude_down())

        elif self.state == State.PAYLOAD:
            if self.vehicle.mode not in MISSION_OWNED_MODES:
                self.stand_down_for_external_mode(f"external mode {self.vehicle.mode} during payload")
                return detections, masks
            self.request_mode_repeated("GUIDED")
            tracked, masks = self._track_active_target(
                frame,
                processed,
                masks,
                now,
            )
            if tracked is not None:
                detections = [tracked]
                _, _, distance = self.centre_velocity(tracked, width, height)
                self.last_center_error_px = distance
                self.center_error_history.append((now, distance))
            if self.payload_release_sent_at is None:
                verification_frame = (
                    frame
                    if full_resolution_frame is None
                    else full_resolution_frame
                )
                self.verify_payload_geometry(
                    verification_frame,
                    width,
                    height,
                    now,
                    frame_id=frame_id,
                    captured_at_s=captured_at_s,
                    received_at_s=received_at_s,
                )
            self.payload_action(now)

        elif self.state == State.WAITING_FOR_AUTO_RESUME:
            if self.vehicle.mode == "AUTO":
                self.transition(State.SEARCH, f"AUTO resumed item {self.vehicle.mission_seq}")
            elif self.vehicle.mode not in {"GUIDED", "AUTO"}:
                self.latch_pilot_override(f"external mode {self.vehicle.mode} while resuming AUTO")
            else:
                self.request_mode_repeated("AUTO")

        elif self.state == State.WAITING_FOR_RTL:
            if self.vehicle.mode == "RTL":
                self.mission_done_count += 1
                self.transition(State.COMPLETE, "RTL confirmed")
            elif self.vehicle.mode not in {"GUIDED", "AUTO", "RTL"}:
                self.latch_pilot_override(f"external mode {self.vehicle.mode} while waiting for RTL")
            else:
                self.request_mode_repeated("RTL")

        elif self.state == State.COMPLETE:
            ready, reason = self.auto_search_start_ready()
            if ready and self.payload_state_path is None:
                self.reset_for_next_mission(f"new AUTO run at waypoint {self.vehicle.mission_seq}; {reason}")
            elif ready:
                self.status_message = "Payload state retained; explicit reset required for another attempt"

        return detections, masks

    def draw(self, frame, detections):
        return draw_mission_overlay(self, frame, detections)

    def run(self) -> int:
        print("=" * 72)
        print("WD DRONE TARGET MISSION V2")
        print("QGC/ARDUPILOT OWNS AUTO ALTITUDE/SPEED. COMPANION CENTERS TARGETS IN GUIDED.")
        gate_enabled, gate_reason = self.search_gate_status()
        print(f"MISSION PROFILE: {self.config['mission'].get('name', 'unnamed')}")
        print(f"SEARCH START WP: {self.config['mission']['search_start_wp']} | SEARCH SPEED OWNER: {self.search_speed_label()}")
        print(f"SEARCH GATE: {'enabled' if gate_enabled else 'blocked'} ({gate_reason})")
        print(
            f"GUIDED HOLD: {optional_seconds_label(self.safety.get('guided_auto_bounce_grace_s'))} | "
            f"MAX AUTO BOUNCES/TARGET: {self.safety.get('max_guided_auto_bounces_per_target')} | "
            f"TARGET LOST TIMEOUT: {float(self.config['control']['target_lost_timeout_s']):.1f}s"
        )
        print("=" * 72)
        camera_worker = LatestFrameCamera(self.camera)
        video_stream = MjpegFrameServer(self.config["display"])
        camera_worker.start()
        video_stream.start()
        last_frame_sequence = 0
        try:
            while not self.stop_requested:
                self.vehicle.poll()
                heartbeat_timeout_s = float(self.config["mavlink"].get("heartbeat_timeout_s", 8.0))
                heartbeat_age = time.monotonic() - self.vehicle.last_heartbeat
                if heartbeat_age > heartbeat_timeout_s:
                    print(
                        f"[MAVLINK WARNING] Vehicle heartbeat is {heartbeat_age:.1f}s old; "
                        "checking the queued telemetry backlog"
                    )
                    if not self.vehicle.recover_vehicle_heartbeat(timeout_s=1.0):
                        heartbeat_age = time.monotonic() - self.vehicle.last_heartbeat
                        print(f"[FATAL] Heartbeat lost; last vehicle heartbeat was {heartbeat_age:.1f}s ago")
                        return 3
                    print("[MAVLINK] Vehicle heartbeat recovered from telemetry backlog")
                now = time.monotonic()
                if self.active_target_phase() and self.vehicle.mode not in MISSION_OWNED_MODES:
                    self.latch_pilot_override(f"external mode {self.vehicle.mode} while camera/control active")
                camera_frame = camera_worker.latest()
                if camera_frame is None or camera_frame.frame_id == last_frame_sequence:
                    if camera_worker.failed:
                        print("[FATAL] Camera capture thread stopped")
                        return 4
                    self.handle_camera_frame_miss(now)
                    if (
                        self.state == State.PAYLOAD
                        and self.vehicle.mode in MISSION_OWNED_MODES
                        and self.payload_release_sent_at is not None
                    ):
                        self.payload_action(now)
                    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                        break
                    time.sleep(0.01)
                    continue
                last_frame_sequence = camera_frame.frame_id
                frame_age_s = camera_frame.capture_age_s(now)
                max_frame_age_s = float(
                    self.safety.get("max_input_frame_age_s", 0.50)
                )
                if frame_age_s > max_frame_age_s:
                    if now - self.last_camera_timeout_print_at >= 1.0:
                        print(
                            f"[STALE CAMERA FRAME] id={camera_frame.frame_id} "
                            f"age={frame_age_s:.3f}s limit={max_frame_age_s:.3f}s"
                        )
                        self.last_camera_timeout_print_at = now
                    self.handle_camera_frame_miss(now)
                    time.sleep(0.005)
                    continue
                self.last_frame_at = camera_frame.received_monotonic_s
                full_resolution_frame = camera_frame.image_bgr
                frame = self.resize(full_resolution_frame)
                captured_at_s = (
                    camera_frame.sensor_timestamp_ns / 1_000_000_000.0
                    if camera_frame.has_sensor_timestamp
                    else camera_frame.received_monotonic_s
                )
                processed: Optional[ProcessedVisionFrame] = None
                detections: list[Detection] = []
                needs_vision = self.state in {
                    State.SEARCH,
                    State.WAITING_FOR_GUIDED,
                    State.CENTER,
                    State.PAYLOAD,
                }
                if needs_vision or bool(self.config["display"].get("show_masks", False)):
                    processed = self.detector.preprocess(
                        frame,
                        frame_id=camera_frame.frame_id,
                        captured_at_s=captured_at_s,
                        received_at_s=camera_frame.received_monotonic_s,
                    )
                    masks = processed.masks
                    if self.state == State.SEARCH:
                        detections = self.detector.search_processed(
                            processed,
                            self.incomplete_targets(),
                        )
                else:
                    masks = self.detector.empty_masks(frame)
                detections, masks = self.update(
                    frame,
                    detections,
                    masks,
                    processed=processed,
                    full_resolution_frame=full_resolution_frame,
                    frame_id=camera_frame.frame_id,
                    captured_at_s=captured_at_s,
                    received_at_s=camera_frame.received_monotonic_s,
                )
                now = time.monotonic()
                sample_rate_hz = float(self.config["logging"].get("sample_rate_hz", 2.0))
                if sample_rate_hz > 0 and now - self.last_sample_log_at >= 1.0 / sample_rate_hz:
                    self.log_file.write(json.dumps({
                        "time": time.time(),
                        "sample": "telemetry",
                        "state": self.state.value,
                        "mode": self.vehicle.mode,
                        "altitude_m": self.vehicle.relative_alt_m,
                        "waypoint": self.vehicle.mission_seq,
                        "current_target": self.current_target,
                        "completed_targets": sorted(self.completed_targets),
                        "mission_done_count": self.mission_done_count,
                        "mission_profile": self.config["mission"].get("name"),
                        "search_gate": self.search_gate_status()[1],
                        "accepted_payload_locks_px": self.completed_center_errors,
                        "payload_colour": (
                            payload_colour_for_target(self.current_target)
                            if self.current_target
                            else None
                        ),
                        "camera": {
                            **camera_worker.metrics(),
                            "source": camera_frame.source,
                            "frame_age_s": round(camera_frame.capture_age_s(now), 4),
                            "receive_age_s": round(camera_frame.frame_age_s(now), 4),
                            "sensor_age_s": (
                                None
                                if camera_frame.sensor_age_s(now) is None
                                else round(camera_frame.sensor_age_s(now) or 0.0, 4)
                            ),
                        },
                        "vision_timings_ms": (
                            {} if processed is None else processed.timings_ms
                        ),
                        "detections": [asdict(x) for x in detections],
                    }, sort_keys=True) + "\n")
                    self.last_sample_log_at = now
                if now - self.last_log_flush_at >= float(self.config["logging"].get("flush_interval_s", 0.5)):
                    self.log_file.flush()
                    self.last_log_flush_at = now
                if self.config["display"]["show_main_window"] or video_stream.enabled:
                    view = self.draw(frame, detections)
                    video_stream.publish(view)
                    if self.config["display"]["show_main_window"]:
                        cv2.imshow("WD DRONE Target Mission V2", view)
                if self.config["display"]["show_masks"]:
                    cv2.imshow("Red mask", masks["red"])
                    cv2.imshow("Blue mask", masks["blue"])
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
        finally:
            if self.vehicle.mode == "GUIDED":
                self.send_velocity(0.0, 0.0, 0.0, force=True)
            camera_worker.stop()
            video_stream.stop()
            close_detector = getattr(self.detector, "close", None)
            if callable(close_detector):
                close_detector()
            self.log_file.close()
            cv2.destroyAllWindows()
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("mission_config.json"))
    state_actions = parser.add_mutually_exclusive_group()
    state_actions.add_argument("--show-payload-state", action="store_true")
    state_actions.add_argument("--reset-payload-state", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    validate_config(config)
    if args.show_payload_state or args.reset_payload_state:
        return manage_payload_state(config, reset=args.reset_payload_state)
    if not bool(config["mission"].get("controller_enabled", True)):
        raise RuntimeError(
            "This is a camera-only profile; mission controller and MAVLink "
            "commands are disabled. Use tools/opencv_live_test.py."
        )
    vehicle = Vehicle(config["mavlink"]["connection"], config["mavlink"].get("baud"))
    enforce_parameters(vehicle, config)
    camera = open_camera(config["camera"])
    controller = Controller(config, vehicle, camera)
    signal.signal(signal.SIGINT, lambda *_: setattr(controller, "stop_requested", True))
    signal.signal(signal.SIGTERM, lambda *_: setattr(controller, "stop_requested", True))
    return controller.run()


if __name__ == "__main__":
    raise SystemExit(main())

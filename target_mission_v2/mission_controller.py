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
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import cv2
from pymavlink import mavutil

from camera_sources import CameraLike, SUPPORTED_CAMERA_SOURCES, open_camera
from vision import Detection, HitTracker, SUPPORTED_VISION_BACKENDS, create_detector
from control import altitude_velocity_down, clamp


AUTOPILOT_COMPONENTS = {mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1}
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


TARGET_PAYLOAD_COLOUR = {
    "blue_hexagon": "red",
    "red_triangle": "blue",
}

DEFAULT_SAFETY = {
    "max_center_time_s": 25.0,
    "max_guided_speed_m_s": 0.45,
    "guided_auto_bounce_grace_s": None,
    "max_guided_auto_bounces_per_target": None,
    "active_target_abort_mode": "AUTO",
    "mode_retry_interval_s": 0.5,
    "camera_frame_timeout_s": 2.0,
    "payload_requires_guided": True,
    "payload_min_altitude_m": None,
    "payload_max_altitude_m": None,
}

DEFAULT_NAVIGATION = {
    "search_speed_source": "qgc_mission",
    "search_speed_m_s": None,
}

def payload_colour_for_target(target: str) -> str:
    try:
        return TARGET_PAYLOAD_COLOUR[target]
    except KeyError as exc:
        raise ValueError(f"Unknown mission target: {target}") from exc


def safety_config(config: dict[str, Any]) -> dict[str, Any]:
    safety = dict(DEFAULT_SAFETY)
    safety.update(config.get("safety", {}))
    return safety


def navigation_config(config: dict[str, Any]) -> dict[str, Any]:
    navigation = dict(DEFAULT_NAVIGATION)
    navigation.update(config.get("navigation", {}))
    return navigation


def optional_seconds_label(value: Any) -> str:
    if value is None:
        return "inf"
    return f"{float(value):.1f}s"


def heartbeat_is_vehicle(message: Any) -> bool:
    if message.get_srcSystem() <= 0:
        return False
    if message.get_srcComponent() not in AUTOPILOT_COMPONENTS:
        return False
    if message.type in (
        mavutil.mavlink.MAV_TYPE_GCS,
        mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
    ):
        return False
    return message.autopilot != mavutil.mavlink.MAV_AUTOPILOT_INVALID


def heartbeat_is_target_vehicle(message: Any, target_system: int) -> bool:
    return message.get_srcSystem() == target_system and heartbeat_is_vehicle(message)


def required_ardupilot_parameters(config: dict[str, Any]) -> dict[str, float]:
    params = config["parameters"]
    rtl_alt_cm = params.get("rtl_alt_cm")
    if rtl_alt_cm is None:
        rtl_alt_cm = float(params["rtl_alt_m"]) * 100.0
    rtl_climb_min_cm = params.get("rtl_climb_min_cm")
    if rtl_climb_min_cm is None:
        rtl_climb_min_cm = float(params["rtl_climb_min_m"]) * 100.0
    return {
        "RTL_ALT": float(rtl_alt_cm),
        "RTL_CLIMB_MIN": float(rtl_climb_min_cm),
        "MIS_RESTART": float(params["mis_restart"]),
    }


def validate_config(config: dict[str, Any]) -> None:
    required_sections = (
        "mavlink", "camera", "mission", "parameters", "vision",
        "control", "payload", "display", "logging",
    )
    missing = [section for section in required_sections if section not in config]
    if missing:
        raise ValueError(f"Missing config sections: {', '.join(missing)}")
    baud = config["mavlink"].get("baud")
    if baud is not None and int(baud) <= 0:
        raise ValueError("mavlink.baud must be positive or null")
    camera_source = config["camera"].get("source", "udp_h264")
    if camera_source not in SUPPORTED_CAMERA_SOURCES:
        supported = ", ".join(sorted(SUPPORTED_CAMERA_SOURCES))
        raise ValueError(f"camera.source must be one of: {supported}")
    if camera_source == "udp_h264" and int(config["camera"].get("udp_port", 0)) <= 0:
        raise ValueError("camera.udp_port must be positive for udp_h264")
    if camera_source == "gstreamer_pipeline" and not str(config["camera"].get("pipeline", "")).strip():
        raise ValueError("camera.pipeline is required for gstreamer_pipeline")
    if camera_source == "device" and int(config["camera"].get("device_index", 0)) < 0:
        raise ValueError("camera.device_index must be zero or positive")
    if camera_source == "rpicam_mjpeg":
        if int(config["camera"].get("camera_index", 0)) < 0:
            raise ValueError("camera.camera_index must be zero or positive for rpicam_mjpeg")
        for key in ("width", "height", "quality"):
            if int(config["camera"].get(key, 1)) <= 0:
                raise ValueError(f"camera.{key} must be positive for rpicam_mjpeg")
        if float(config["camera"].get("framerate", 1.0)) <= 0:
            raise ValueError("camera.framerate must be positive for rpicam_mjpeg")
        if float(config["camera"].get("read_timeout_s", 2.0)) <= 0:
            raise ValueError("camera.read_timeout_s must be positive for rpicam_mjpeg")
    if float(config["mission"].get("max_flight_time_s", 600.0)) <= 0:
        raise ValueError("mission.max_flight_time_s must be positive")
    if int(config["mission"].get("search_start_wp", 0)) < 0:
        raise ValueError("mission.search_start_wp must be zero or positive")
    if float(config["mission"].get("mode_change_timeout_s", 5.0)) <= 0:
        raise ValueError("mission.mode_change_timeout_s must be positive")
    rc_channel = config["mission"].get("search_enable_rc_channel")
    if rc_channel is not None and not 1 <= int(rc_channel) <= 18:
        raise ValueError("mission.search_enable_rc_channel must be null or 1..18")
    search_enable_pwm_min = int(config["mission"].get("search_enable_pwm_min", 1700))
    if not 900 <= search_enable_pwm_min <= 2200:
        raise ValueError("mission.search_enable_pwm_min must be a valid RC PWM value")
    if int(config["vision"]["required_hits"]) < 1:
        raise ValueError("vision.required_hits must be at least 1")
    vision_backend = config["vision"].get("backend", "strict_shape")
    if vision_backend not in SUPPORTED_VISION_BACKENDS:
        supported = ", ".join(sorted(SUPPORTED_VISION_BACKENDS))
        raise ValueError(f"vision.backend must be one of: {supported}")
    if float(config["control"]["command_rate_hz"]) <= 0:
        raise ValueError("control.command_rate_hz must be positive")
    if float(config["control"].get("center_tolerance_px", 1.0)) <= 0:
        raise ValueError("control.center_tolerance_px must be positive")
    for target, value in config["control"].get("center_tolerance_px_by_target", {}).items():
        if target not in {"red_triangle", "blue_hexagon"}:
            raise ValueError("control.center_tolerance_px_by_target keys must be red_triangle or blue_hexagon")
        if float(value) <= 0:
            raise ValueError("control.center_tolerance_px_by_target values must be positive")
    if float(config["control"].get("target_lost_timeout_s", 2.0)) <= 0:
        raise ValueError("control.target_lost_timeout_s must be positive")
    if float(config["control"].get("reacquire_after_lost_s", 0.25)) < 0:
        raise ValueError("control.reacquire_after_lost_s must be zero or positive")
    missing_action = config["parameters"].get("missing_action", "fail")
    if missing_action not in {"fail", "warn"}:
        raise ValueError("parameters.missing_action must be 'fail' or 'warn'")
    altitude_control = config["control"].get("altitude_control", "off")
    if altitude_control not in {"off", "hold_configured"}:
        raise ValueError("control.altitude_control must be 'off' or 'hold_configured'")
    if altitude_control == "hold_configured" and float(config["mission"]["survey_altitude_m"]) <= 0:
        raise ValueError("mission.survey_altitude_m must be positive when altitude hold is enabled")
    safety = safety_config(config)
    max_center_time_s = safety.get("max_center_time_s")
    if max_center_time_s is not None and float(max_center_time_s) <= 0:
        raise ValueError("safety.max_center_time_s must be positive or null")
    max_guided_speed_m_s = safety.get("max_guided_speed_m_s")
    if max_guided_speed_m_s is not None and float(max_guided_speed_m_s) <= 0:
        raise ValueError("safety.max_guided_speed_m_s must be positive or null")
    guided_auto_bounce_grace_s = safety.get("guided_auto_bounce_grace_s")
    if guided_auto_bounce_grace_s is not None and float(guided_auto_bounce_grace_s) < 0:
        raise ValueError("safety.guided_auto_bounce_grace_s must be zero, positive, or null")
    max_guided_bounces = safety.get("max_guided_auto_bounces_per_target")
    if max_guided_bounces is not None and int(max_guided_bounces) < 0:
        raise ValueError("safety.max_guided_auto_bounces_per_target must be zero, positive, or null")
    active_abort_mode = safety.get("active_target_abort_mode", "AUTO")
    if active_abort_mode not in {"AUTO", "RTL", "LOITER", "LAND"}:
        raise ValueError("safety.active_target_abort_mode must be AUTO, RTL, LOITER, or LAND")
    mode_retry_interval_s = safety.get("mode_retry_interval_s")
    if mode_retry_interval_s is not None and float(mode_retry_interval_s) <= 0:
        raise ValueError("safety.mode_retry_interval_s must be positive or null")
    camera_frame_timeout_s = safety.get("camera_frame_timeout_s")
    if camera_frame_timeout_s is not None and float(camera_frame_timeout_s) <= 0:
        raise ValueError("safety.camera_frame_timeout_s must be positive or null")
    min_alt = safety.get("payload_min_altitude_m")
    max_alt = safety.get("payload_max_altitude_m")
    if min_alt is not None and max_alt is not None and float(min_alt) > float(max_alt):
        raise ValueError("safety.payload_min_altitude_m cannot exceed payload_max_altitude_m")
    navigation = navigation_config(config)
    if navigation["search_speed_source"] not in {"qgc_mission", "companion_do_change_speed"}:
        raise ValueError("navigation.search_speed_source must be 'qgc_mission' or 'companion_do_change_speed'")
    if navigation["search_speed_source"] == "companion_do_change_speed":
        if navigation.get("search_speed_m_s") is None or float(navigation["search_speed_m_s"]) <= 0:
            raise ValueError("navigation.search_speed_m_s must be positive when companion speed control is enabled")
    if float(config["display"].get("overlay_font_scale", 0.46)) <= 0:
        raise ValueError("display.overlay_font_scale must be positive")
    overlay_alpha = float(config["display"].get("overlay_background_alpha", 0.42))
    if not 0.0 <= overlay_alpha <= 1.0:
        raise ValueError("display.overlay_background_alpha must be between 0 and 1")
    required_ardupilot_parameters(config)


class Vehicle:
    VELOCITY_ONLY_MASK = 3527

    def __init__(self, connection: str, baud: Optional[int] = None) -> None:
        print(f"[MAVLINK] Connecting to {connection} baud={baud or 'default'}")
        kwargs: dict[str, Any] = {"source_system": 245, "source_component": 191}
        if baud is not None:
            kwargs["baud"] = int(baud)
        self.master = mavutil.mavlink_connection(connection, **kwargs)
        hb = self._wait_vehicle_heartbeat(timeout_s=30.0)
        if hb is None:
            raise RuntimeError("No ArduPilot heartbeat")
        self.target_system = hb.get_srcSystem()
        self.target_component = hb.get_srcComponent()
        self.master.target_system = self.target_system
        self.master.target_component = self.target_component
        self.mode = mavutil.mode_string_v10(hb)
        self.armed = bool(hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        self.relative_alt_m: Optional[float] = None
        self.mission_seq: Optional[int] = None
        self.velocity_north_m_s: Optional[float] = None
        self.velocity_east_m_s: Optional[float] = None
        self.velocity_down_m_s: Optional[float] = None
        self.horizontal_speed_m_s: Optional[float] = None
        self.total_speed_m_s: Optional[float] = None
        self.acceleration_m_s2: Optional[float] = None
        self._last_velocity_sample: Optional[tuple[float, float, float, float]] = None
        self.rc_channels: dict[int, int] = {}
        self.last_heartbeat = time.monotonic()
        self._request_interval(mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT, 10.0)
        self._request_interval(mavutil.mavlink.MAVLINK_MSG_ID_MISSION_CURRENT, 4.0)
        self._request_interval(mavutil.mavlink.MAVLINK_MSG_ID_RC_CHANNELS, 4.0)
        print(f"[MAVLINK] Connected system={self.target_system} component={self.target_component}")

    def _wait_vehicle_heartbeat(self, timeout_s: float) -> Optional[Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            message = self.master.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
            if message is None:
                continue
            if heartbeat_is_vehicle(message):
                return message
            source = f"{message.get_srcSystem()}:{message.get_srcComponent()}"
            print(f"[MAVLINK] Ignoring non-vehicle heartbeat src={source} mode={mavutil.mode_string_v10(message)}")
        return None

    def _request_interval(self, message_id: int, hz: float) -> None:
        self.master.mav.command_long_send(
            self.target_system, self.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
            message_id, int(1_000_000 / hz), 0, 0, 0, 0, 0,
        )

    def poll(self) -> None:
        for _ in range(100):
            msg = self.master.recv_match(blocking=False)
            if msg is None:
                return
            self._handle_message(msg)

    def _handle_message(self, msg: Any) -> None:
        kind = msg.get_type()
        if kind != "BAD_DATA" and msg.get_srcSystem() not in (0, self.target_system):
            return
        if kind == "HEARTBEAT":
            if not heartbeat_is_target_vehicle(msg, self.target_system):
                return
            self.mode = mavutil.mode_string_v10(msg)
            self.armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            self.last_heartbeat = time.monotonic()
        elif kind == "GLOBAL_POSITION_INT":
            self.relative_alt_m = msg.relative_alt / 1000.0
            now = time.monotonic()
            vn = float(msg.vx) / 100.0
            ve = float(msg.vy) / 100.0
            vd = float(msg.vz) / 100.0
            self.velocity_north_m_s = vn
            self.velocity_east_m_s = ve
            self.velocity_down_m_s = vd
            self.horizontal_speed_m_s = math.hypot(vn, ve)
            self.total_speed_m_s = math.sqrt(vn * vn + ve * ve + vd * vd)
            if self._last_velocity_sample is not None:
                last_t, last_vn, last_ve, last_vd = self._last_velocity_sample
                dt = max(1e-6, now - last_t)
                dv = math.sqrt((vn - last_vn) ** 2 + (ve - last_ve) ** 2 + (vd - last_vd) ** 2)
                self.acceleration_m_s2 = dv / dt
            self._last_velocity_sample = (now, vn, ve, vd)
        elif kind == "MISSION_CURRENT":
            self.mission_seq = int(msg.seq)
        elif kind == "RC_CHANNELS":
            for channel in range(1, 19):
                value = int(getattr(msg, f"chan{channel}_raw", 0))
                if value > 0:
                    self.rc_channels[channel] = value
        elif kind == "STATUSTEXT":
            text = msg.text.decode(errors="replace") if isinstance(msg.text, bytes) else msg.text
            if int(msg.severity) <= mavutil.mavlink.MAV_SEVERITY_WARNING:
                print(f"[ARDUPILOT] {text}")
        elif kind == "COMMAND_ACK":
            result = mavutil.mavlink.enums["MAV_RESULT"].get(msg.result)
            print(f"[COMMAND ACK] command={msg.command} result={result.name if result else msg.result}")

    def set_mode(self, name: str) -> None:
        mapping = self.master.mode_mapping()
        if name not in mapping:
            raise RuntimeError(f"Mode unavailable: {name}")
        print(f"[MODE REQUEST] {self.mode} -> {name}")
        self.master.mav.set_mode_send(
            self.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            mapping[name],
        )

    def send_body_velocity(self, forward: float, right: float, down: float) -> None:
        self.master.mav.set_position_target_local_ned_send(
            int(time.monotonic() * 1000) & 0xFFFFFFFF,
            self.target_system,
            self.target_component,
            mavutil.mavlink.MAV_FRAME_BODY_OFFSET_NED,
            self.VELOCITY_ONLY_MASK,
            0, 0, 0,
            float(forward), float(right), float(down),
            0, 0, 0,
            0, 0,
        )

    def set_servo(self, channel: int, pwm: int) -> None:
        self.master.mav.command_long_send(
            self.target_system, self.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_SERVO, 0,
            float(channel), float(pwm), 0, 0, 0, 0, 0,
        )

    def set_ground_speed(self, speed_m_s: float) -> None:
        print(f"[SPEED REQUEST] AUTO ground speed {speed_m_s:.2f} m/s")
        self.master.mav.command_long_send(
            self.target_system,
            self.target_component,
            mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
            0,
            1.0,
            float(speed_m_s),
            -1.0,
            0,
            0,
            0,
            0,
        )

    @staticmethod
    def _param_id(message: Any) -> str:
        param_id = message.param_id.decode(errors="replace") if isinstance(message.param_id, bytes) else message.param_id
        return param_id.rstrip("\x00")

    def _parameter_components(self) -> list[int]:
        components = [self.target_component, 1, 0]
        return list(dict.fromkeys(components))

    def read_parameter(self, name: str, timeout_s: float = 8.0) -> Optional[float]:
        deadline = time.monotonic() + timeout_s
        for component in self._parameter_components():
            self.master.mav.param_request_read_send(self.target_system, component, name.encode(), -1)
            attempt_deadline = min(deadline, time.monotonic() + max(0.8, timeout_s / 3.0))
            while time.monotonic() < attempt_deadline:
                msg = self.master.recv_match(blocking=True, timeout=0.25)
                if msg is None:
                    continue
                self._handle_message(msg)
                if msg.get_type() == "PARAM_VALUE" and self._param_id(msg) == name:
                    return float(msg.param_value)
            if time.monotonic() >= deadline:
                break
        return None

    def set_parameter(self, name: str, value: float, timeout_s: float = 6.0) -> None:
        print(f"[PARAMETER] Setting {name}={value}")
        deadline = time.monotonic() + timeout_s
        last_send_at = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_send_at >= 1.0:
                for component in self._parameter_components():
                    self.master.mav.param_set_send(
                        self.target_system, component,
                        name.encode(), float(value), mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
                    )
                last_send_at = now
            msg = self.master.recv_match(blocking=True, timeout=0.3)
            if msg is None:
                continue
            self._handle_message(msg)
            if msg.get_type() == "PARAM_VALUE" and self._param_id(msg) == name and abs(float(msg.param_value) - value) <= 0.5:
                print(f"[PARAMETER VERIFIED] {name}={float(msg.param_value)}")
                return
        raise RuntimeError(f"ArduPilot did not confirm {name}={value}")


def enforce_parameters(vehicle: Vehicle, config: dict[str, Any]) -> None:
    params = config["parameters"]
    if not bool(params.get("enforce", True)):
        print("[PARAMETER] Enforcement disabled; QGC/ArduPilot parameters are left unchanged")
        return
    timeout_s = float(params.get("read_timeout_s", 8.0))
    missing_action = params.get("missing_action", "fail")
    for name, value in required_ardupilot_parameters(config).items():
        current = vehicle.read_parameter(name, timeout_s=timeout_s)
        if current is None:
            message = f"Could not read {name}; parameter was not verified"
            if missing_action == "warn":
                print(f"[PARAMETER WARNING] {message}")
                continue
            raise RuntimeError(message)
        print(f"[PARAMETER] {name} current={current}")
        if abs(current - value) > 0.5:
            vehicle.set_parameter(name, value)
        else:
            print(f"[PARAMETER VERIFIED] {name}={current}")


class Controller:
    def __init__(self, config: dict[str, Any], vehicle: Vehicle, camera: CameraLike) -> None:
        self.config = config
        self.vehicle = vehicle
        self.camera = camera
        vcfg = config["vision"]
        self.detector = create_detector(vcfg)
        self.tracker = HitTracker(vcfg["required_hits"], vcfg["confirmation_window_s"], vcfg["max_lock_jump_px"])
        self.safety = safety_config(config)
        self.navigation = navigation_config(config)
        self.state = State.WAITING_FOR_AUTO
        self.state_started_at = time.monotonic()
        self.last_mode_request_at = 0.0
        self.last_velocity_at = 0.0
        self.last_search_speed_request_at = 0.0
        self.mission_started_at: Optional[float] = None
        self.last_log_flush_at = time.monotonic()
        self.stop_requested = False
        self.completed_targets: set[str] = set()
        self.current_target: Optional[str] = None
        self.last_detection: Optional[Detection] = None
        self.last_seen_at = 0.0
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
        self.status_message = "Waiting for AUTO at search waypoint"
        self.payload_started = False
        self.payload_reset = False
        self.mission_done_count = 0
        log_dir = Path(config["logging"]["directory"])
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = (log_dir / f"mission-v2-{time.strftime('%Y%m%d-%H%M%S')}.jsonl").open("a", encoding="utf-8")

    def transition(self, state: State, reason: str) -> None:
        print(f"[STATE] {self.state.value} -> {state.value}: {reason}")
        self.state = state
        self.state_started_at = time.monotonic()
        self.centered_since = None
        self.center_started_at = self.state_started_at if state == State.CENTER else None
        self.guided_mode_lost_since = None
        self.status_message = reason
        if state == State.SEARCH and self.mission_started_at is None:
            self.mission_started_at = self.state_started_at
        if state == State.SEARCH:
            self.apply_search_speed_policy()

    def incomplete_targets(self) -> set[str]:
        return {"red_triangle", "blue_hexagon"} - self.completed_targets

    def reset_for_next_mission(self, reason: str) -> None:
        self.completed_targets.clear()
        self.current_target = None
        self.last_detection = None
        self.last_seen_at = 0.0
        self.centered_since = None
        self.center_started_at = None
        self.guided_mode_lost_since = None
        self.guided_bounce_count_for_target = 0
        self.last_center_error_px = None
        self.last_center_forward = None
        self.last_center_right = None
        self.payload_started = False
        self.payload_reset = False
        self.mission_started_at = time.monotonic()
        self.tracker.reset()
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
                forward = clamp(forward, float(max_guided_speed))
                right = clamp(right, float(max_guided_speed))
            self.vehicle.send_body_velocity(forward, right, down)
            self.last_velocity_at = now

    def request_mode_repeated(self, mode: str, force: bool = False) -> None:
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

    def centre_velocity(self, detection: Detection, width: int, height: int) -> tuple[float, float, float]:
        c = self.config["control"]
        ex = detection.center_x - width / 2.0
        ey = detection.center_y - height / 2.0
        forward = float(c["image_y_to_forward_sign"]) * float(c["center_kp"]) * ey / (height / 2.0)
        right = float(c["image_x_to_right_sign"]) * float(c["center_kp"]) * ex / (width / 2.0)
        maximum = float(c["center_max_speed_m_s"])
        return clamp(forward, maximum), clamp(right, maximum), math.hypot(ex, ey)

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

    def stand_down_for_external_mode(self, reason: str) -> None:
        print(f"[EXTERNAL MODE] {reason}; standing down and waiting for AUTO")
        self.send_velocity(0.0, 0.0, 0.0, force=True)
        self.current_target = None
        self.last_detection = None
        self.last_seen_at = 0.0
        self.centered_since = None
        self.center_started_at = None
        self.guided_mode_lost_since = None
        self.tracker.reset()
        self.transition(State.WAITING_FOR_AUTO, f"{reason}; waiting for AUTO")

    def handle_camera_frame_miss(self, now: float) -> None:
        timeout_s = self.safety.get("camera_frame_timeout_s")
        if timeout_s is None or not self.active_target_phase():
            return
        missed_for = now - self.last_frame_at
        if missed_for < float(timeout_s):
            return
        self.status_message = f"Camera frame timeout {missed_for:.1f}s; holding position"
        if self.vehicle.mode in MISSION_OWNED_MODES:
            self.send_velocity(0.0, 0.0, self.altitude_down(), force=True)
            self.request_mode_repeated("GUIDED", force=True)
        if now - self.last_camera_timeout_print_at >= 1.0:
            print(f"[CAMERA TIMEOUT] no frame for {missed_for:.1f}s during {self.state.value}; holding")
            self.last_camera_timeout_print_at = now

    def search_gate_status(self) -> tuple[bool, str]:
        mission = self.config["mission"]
        if not bool(mission.get("search_enabled", True)):
            return False, "search disabled by mission profile"
        rc_channel = mission.get("search_enable_rc_channel")
        if rc_channel is not None:
            channel = int(rc_channel)
            value = self.vehicle.rc_channels.get(channel)
            threshold = int(mission.get("search_enable_pwm_min", 1700))
            if value is None:
                return False, f"waiting for RC{channel} mission-enable PWM"
            if value < threshold:
                return False, f"RC{channel}={value} below enable threshold {threshold}"
            return True, f"RC{channel}={value} enabled"
        return True, "enabled by config"

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
        if bool(self.safety.get("payload_requires_guided", True)) and self.vehicle.mode != "GUIDED":
            return f"vehicle mode is {self.vehicle.mode}, not GUIDED"
        altitude = self.vehicle.relative_alt_m
        min_alt = self.safety.get("payload_min_altitude_m")
        max_alt = self.safety.get("payload_max_altitude_m")
        if min_alt is not None:
            if altitude is None:
                return "altitude is unknown"
            if altitude < float(min_alt):
                return f"altitude {altitude:.2f} m is below {float(min_alt):.2f} m"
        if max_alt is not None:
            if altitude is None:
                return "altitude is unknown"
            if altitude > float(max_alt):
                return f"altitude {altitude:.2f} m is above {float(max_alt):.2f} m"
        return None

    def active_target_abort_mode(self) -> str:
        return str(self.safety.get("active_target_abort_mode", "AUTO"))

    def abandon_active_target(self, now: float, reason: str) -> None:
        mode = self.active_target_abort_mode()
        print(f"[TARGET ABORT] {reason}; requesting {mode}")
        self.send_velocity(0.0, 0.0, self.altitude_down())
        self.current_target = None
        self.last_detection = None
        self.tracker.reset()
        self.vehicle.set_mode(mode)
        self.last_mode_request_at = now
        self.transition(State.WAITING_FOR_AUTO_RESUME if mode == "AUTO" else State.WAITING_FOR_RTL,
                        f"{reason}; abort to {mode}")

    def payload_action(self, now: float) -> None:
        p = self.config["payload"]
        safety_error = self.payload_safety_error()
        if (
            safety_error
            and bool(self.safety.get("payload_requires_guided", True))
            and self.vehicle.mode != "GUIDED"
        ):
            self.status_message = f"Payload locked; waiting for GUIDED instead of {self.vehicle.mode}"
            self.send_velocity(0.0, 0.0, self.altitude_down())
            self.request_mode_repeated("GUIDED", force=True)
            return
        if safety_error:
            self.abandon_active_target(now, f"payload blocked: {safety_error}")
            return
        self.send_velocity(0.0, 0.0, self.altitude_down())
        if not self.payload_started:
            altitude = self.vehicle.relative_alt_m if self.vehicle.relative_alt_m is not None else float("nan")
            payload_colour = payload_colour_for_target(self.current_target or "")
            self.status_message = f"Dropping {payload_colour} payload on {self.current_target}"
            if p["simulate_only"]:
                print(f"[PAYLOAD] SIMULATED {payload_colour} DROP over {self.current_target} at {altitude:.2f} m")
            else:
                print(f"[PAYLOAD] {payload_colour} DROP servo={p['servo_channel']} pwm={p['release_pwm']} over {self.current_target}")
                self.vehicle.set_servo(int(p["servo_channel"]), int(p["release_pwm"]))
            self.payload_started = True
        elapsed = now - self.state_started_at
        if not p["simulate_only"] and not self.payload_reset and elapsed >= float(p["release_hold_s"]):
            self.vehicle.set_servo(int(p["servo_channel"]), int(p["reset_pwm"]))
            self.payload_reset = True
        if elapsed >= float(p["total_action_time_s"]):
            if self.current_target:
                self.completed_targets.add(self.current_target)
                print(f"[TARGET COMPLETE] {self.current_target}; done={sorted(self.completed_targets)}")
            self.current_target = None
            self.last_detection = None
            self.tracker.reset()
            next_mode = "AUTO" if self.incomplete_targets() else "RTL"
            self.vehicle.set_mode(next_mode)
            self.last_mode_request_at = now
            self.transition(State.WAITING_FOR_AUTO_RESUME if next_mode == "AUTO" else State.WAITING_FOR_RTL,
                            "payload complete")

    def update(self, frame, detections, masks):
        now = time.monotonic()
        m = self.config["mission"]
        c = self.config["control"]
        height, width = frame.shape[:2]

        if (
            self.mission_started_at is not None
            and self.state not in {State.WAITING_FOR_RTL, State.COMPLETE}
            and now - self.mission_started_at > float(m.get("max_flight_time_s", 600.0))
        ):
            print("[MISSION TIMEOUT] Requesting RTL")
            self.current_target = None
            self.last_detection = None
            self.tracker.reset()
            self.vehicle.set_mode("RTL")
            self.last_mode_request_at = now
            self.transition(State.WAITING_FOR_RTL, "mission time limit reached")
            return detections, masks

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
                    self.current_target = confirmed.target
                    self.last_detection = next(x for x in detections if x.target == confirmed.target)
                    self.last_seen_at = now
                    self.status_message = f"Confirmed {confirmed.target}; requesting GUIDED"
                    print(f"[TARGET CONFIRMED] {confirmed.target} hits={confirmed.hits}/{self.config['vision']['required_hits']} confidence={confirmed.confidence:.2f}")
                    self.vehicle.set_mode("GUIDED")
                    self.last_mode_request_at = now
                    self.transition(State.WAITING_FOR_GUIDED, "target confirmed")

        elif self.state == State.WAITING_FOR_GUIDED:
            if self.vehicle.mode == "GUIDED":
                self.send_velocity(0.0, 0.0, self.altitude_down())
                self.transition(State.CENTER, "GUIDED confirmed; centering target")
            elif self.vehicle.mode != "AUTO":
                self.stand_down_for_external_mode(f"external mode {self.vehicle.mode} before GUIDED lock")
                return detections, masks
            elif now - self.state_started_at > float(m["mode_change_timeout_s"]):
                self.status_message = "Target locked; still forcing GUIDED"
                self.request_mode_repeated("GUIDED", force=True)
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
                    self.request_mode_repeated("GUIDED", force=True)
                    return detections, masks
                self.stand_down_for_external_mode(f"external mode {self.vehicle.mode} during centering")
                return detections, masks
            self.guided_mode_lost_since = None
            self.request_mode_repeated("GUIDED")
            max_center_time_s = self.safety.get("max_center_time_s")
            if (
                max_center_time_s is not None
                and self.center_started_at is not None
                and now - self.center_started_at > float(max_center_time_s)
            ):
                self.status_message = (
                    f"Centering {self.current_target} is slow; holding GUIDED "
                    f"after {float(max_center_time_s):.1f}s timeout"
                )
                self.send_velocity(0.0, 0.0, self.altitude_down())
                self.request_mode_repeated("GUIDED", force=True)
                return detections, masks
            fresh_detection = False
            if self.last_detection:
                tracked, masks = self.detector.track_colour(
                    frame,
                    self.current_target or "",
                    (self.last_detection.center_x, self.last_detection.center_y),
                    float(self.config["vision"]["max_lock_jump_px"]),
                )
                if tracked:
                    self.last_detection = tracked
                    self.last_seen_at = now
                    detections = [tracked]
                    fresh_detection = True
            lost_for = now - self.last_seen_at if self.last_seen_at else float("inf")
            if not fresh_detection and self.current_target and lost_for >= float(c.get("reacquire_after_lost_s", 0.25)):
                search_detections, masks = self.detector.search(frame)
                matches = [item for item in search_detections if item.target == self.current_target]
                if matches:
                    reacquired = max(matches, key=lambda item: item.confidence)
                    self.last_detection = reacquired
                    self.last_seen_at = now
                    detections = [reacquired]
                    fresh_detection = True
                    self.status_message = f"Reacquired {self.current_target}; centering"
                else:
                    detections = search_detections
            lost_for = now - self.last_seen_at if self.last_seen_at else float("inf")
            if self.last_detection is None or now - self.last_seen_at > float(c["target_lost_timeout_s"]):
                self.centered_since = None
                self.status_message = (
                    f"Target lock lost for {lost_for:.1f}s; holding GUIDED and searching"
                )
                self.send_velocity(0.0, 0.0, self.altitude_down())
                self.request_mode_repeated("GUIDED", force=True)
                return detections, masks
            if not fresh_detection:
                self.centered_since = None
                self.status_message = (
                    f"Looking for {self.current_target} in GUIDED "
                    f"{lost_for:.1f}/{float(c['target_lost_timeout_s']):.1f}s"
                )
                self.send_velocity(0.0, 0.0, self.altitude_down())
                return detections, masks
            forward, right, distance = self.centre_velocity(self.last_detection, width, height)
            self.last_center_error_px = distance
            self.last_center_forward = forward
            self.last_center_right = right
            tolerance_px = self.center_tolerance_px()
            if distance <= tolerance_px:
                forward = right = 0.0
                if self.centered_since is None:
                    self.centered_since = now
                    self.status_message = f"Center lock started on {self.current_target}: err={distance:.0f}px"
                elif now - self.centered_since >= float(c["center_hold_s"]):
                    self.status_message = f"Centering complete on {self.current_target}: err={distance:.0f}px"
                    self.payload_started = False
                    self.payload_reset = False
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
            self.send_velocity(forward, right, self.altitude_down())

        elif self.state == State.PAYLOAD:
            if self.vehicle.mode not in MISSION_OWNED_MODES:
                self.stand_down_for_external_mode(f"external mode {self.vehicle.mode} during payload")
                return detections, masks
            self.request_mode_repeated("GUIDED")
            self.payload_action(now)

        elif self.state == State.WAITING_FOR_AUTO_RESUME:
            if self.vehicle.mode == "AUTO":
                self.transition(State.SEARCH, f"AUTO resumed item {self.vehicle.mission_seq}")
            else:
                self.request_mode_repeated("AUTO")

        elif self.state == State.WAITING_FOR_RTL:
            if self.vehicle.mode == "RTL":
                self.mission_done_count += 1
                self.transition(State.COMPLETE, "RTL confirmed")
            else:
                self.request_mode_repeated("RTL")

        elif self.state == State.COMPLETE:
            ready, reason = self.auto_search_start_ready()
            if ready:
                self.reset_for_next_mission(f"new AUTO run at waypoint {self.vehicle.mission_seq}; {reason}")

        return detections, masks

    def draw(self, frame, detections):
        out = frame.copy()
        h, w = out.shape[:2]
        image_center = (w // 2, h // 2)
        cv2.drawMarker(out, image_center, (255, 255, 255), cv2.MARKER_CROSS, 26, 1)
        draw_items = list(detections)
        if self.last_detection and all(item.target != self.last_detection.target for item in draw_items):
            draw_items.append(self.last_detection)
        for item in draw_items:
            colour = (0, 0, 255) if item.target == "red_triangle" else (255, 0, 0)
            cv2.rectangle(out, (item.bbox_x, item.bbox_y), (item.bbox_x + item.bbox_w, item.bbox_y + item.bbox_h), colour, 2)
            cv2.circle(out, (item.center_x, item.center_y), 5, colour, -1)
            cv2.line(out, image_center, (item.center_x, item.center_y), colour, 2, cv2.LINE_AA)
            error = math.hypot(item.center_x - image_center[0], item.center_y - image_center[1])
            cv2.putText(out, f"{item.target} {item.confidence:.2f} err {error:.0f}px",
                        (item.bbox_x, max(18, item.bbox_y - 7)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, colour, 1, cv2.LINE_AA)
        hits = self.tracker.status()
        alt = "unknown" if self.vehicle.relative_alt_m is None else f"{self.vehicle.relative_alt_m:.2f} m"
        centered_for = 0.0 if self.centered_since is None else time.monotonic() - self.centered_since
        center_error = "none" if self.last_center_error_px is None else f"{self.last_center_error_px:.0f}px"
        def fmt(value: Optional[float], unit: str) -> str:
            return "n/a" if value is None else f"{value:.2f}{unit}"

        done = ",".join(sorted(self.completed_targets)) if self.completed_targets else "none"
        gate_enabled, gate_reason = self.search_gate_status()
        gate = "enabled" if gate_enabled else f"blocked: {gate_reason}"
        font_scale = float(self.config["display"].get("overlay_font_scale", 0.46))
        max_text_width = max(120, w - 42)

        def fit_line(text: str) -> str:
            if cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)[0][0] <= max_text_width:
                return text
            clipped = text
            while len(clipped) > 4 and cv2.getTextSize(clipped + "...", cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)[0][0] > max_text_width:
                clipped = clipped[:-1]
            return clipped + "..."

        lines = [fit_line(line) for line in [
            f"Mission {self.state.value} | Mode {self.vehicle.mode} | WP {self.vehicle.mission_seq}",
            f"Action: {self.status_message}",
            f"Target: {self.current_target or 'none'} | Err {center_error} | Hold {centered_for:.1f}s",
            f"Search gate: {gate}",
            f"Hits: triangle {hits['red_triangle']}/{self.config['vision']['required_hits']} | hexagon {hits['blue_hexagon']}/{self.config['vision']['required_hits']}",
            f"Done: {done} | Runs {self.mission_done_count} | Guided bounces {self.guided_bounce_count}",
            f"Abort {self.active_target_abort_mode()} | Search speed {self.search_speed_label()} | Retry {float(self.safety.get('mode_retry_interval_s') or 1.0):.1f}s",
            f"Alt {alt} | Hspd {fmt(self.vehicle.horizontal_speed_m_s, 'm/s')} | Vspd {fmt(None if self.vehicle.velocity_down_m_s is None else -self.vehicle.velocity_down_m_s, 'm/s')} | Acc {fmt(self.vehicle.acceleration_m_s2, 'm/s2')}",
        ]]
        line_height = max(16, int(38 * font_scale))
        panel_width = min(w - 16, max(cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)[0][0] for line in lines) + 20)
        panel_height = 12 + line_height * len(lines)
        panel = out.copy()
        cv2.rectangle(panel, (8, 8), (8 + panel_width, 8 + panel_height), (0, 0, 0), -1)
        alpha = float(self.config["display"].get("overlay_background_alpha", 0.42))
        cv2.addWeighted(panel, alpha, out, 1.0 - alpha, 0, out)
        y = 28
        for line in lines:
            cv2.putText(out, line, (18, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), 1, cv2.LINE_AA)
            y += line_height
        return out

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
            f"ACTIVE TARGET ABORT: {self.active_target_abort_mode()} | "
            f"TARGET LOST TIMEOUT: {float(self.config['control']['target_lost_timeout_s']):.1f}s"
        )
        print("=" * 72)
        try:
            while not self.stop_requested:
                self.vehicle.poll()
                if time.monotonic() - self.vehicle.last_heartbeat > float(self.config["mavlink"].get("heartbeat_timeout_s", 8.0)):
                    print("[FATAL] Heartbeat lost")
                    return 3
                ok, frame = self.camera.read()
                if not ok or frame is None:
                    self.handle_camera_frame_miss(time.monotonic())
                    time.sleep(0.02)
                    continue
                self.last_frame_at = time.monotonic()
                frame = self.resize(frame)
                if self.state in {State.WAITING_FOR_AUTO, State.SEARCH, State.WAITING_FOR_GUIDED, State.WAITING_FOR_AUTO_RESUME, State.WAITING_FOR_RTL, State.COMPLETE}:
                    detections, masks = self.detector.search(frame)
                else:
                    detections, masks = [], self.detector.masks(frame)
                detections, masks = self.update(frame, detections, masks)
                self.log_file.write(json.dumps({
                    "time": time.time(), "state": self.state.value, "mode": self.vehicle.mode,
                    "altitude_m": self.vehicle.relative_alt_m, "waypoint": self.vehicle.mission_seq,
                    "current_target": self.current_target, "completed_targets": sorted(self.completed_targets),
                    "mission_done_count": self.mission_done_count,
                    "mission_profile": self.config["mission"].get("name"),
                    "search_gate": self.search_gate_status()[1],
                    "payload_colour": payload_colour_for_target(self.current_target) if self.current_target else None,
                    "detections": [asdict(x) for x in detections],
                }, sort_keys=True) + "\n")
                now = time.monotonic()
                if now - self.last_log_flush_at >= float(self.config["logging"].get("flush_interval_s", 0.5)):
                    self.log_file.flush()
                    self.last_log_flush_at = now
                if self.config["display"]["show_main_window"]:
                    cv2.imshow("WD DRONE Target Mission V2", self.draw(frame, detections))
                if self.config["display"]["show_masks"]:
                    cv2.imshow("Red mask", masks["red"])
                    cv2.imshow("Blue mask", masks["blue"])
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
        finally:
            if self.vehicle.mode == "GUIDED":
                self.send_velocity(0.0, 0.0, 0.0, force=True)
            self.camera.release()
            self.log_file.close()
            cv2.destroyAllWindows()
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("mission_config.json"))
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    validate_config(config)
    vehicle = Vehicle(config["mavlink"]["connection"], config["mavlink"].get("baud"))
    enforce_parameters(vehicle, config)
    camera = open_camera(config["camera"])
    controller = Controller(config, vehicle, camera)
    signal.signal(signal.SIGINT, lambda *_: setattr(controller, "stop_requested", True))
    signal.signal(signal.SIGTERM, lambda *_: setattr(controller, "stop_requested", True))
    return controller.run()


if __name__ == "__main__":
    raise SystemExit(main())

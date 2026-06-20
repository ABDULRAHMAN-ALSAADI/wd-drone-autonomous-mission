#!/usr/bin/env python3
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

from vision import Detection, HitTracker, StrictShapeDetector
from control import altitude_velocity_down, clamp


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
    "guided_auto_bounce_grace_s": 2.0,
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
    if float(config["mission"].get("max_flight_time_s", 600.0)) <= 0:
        raise ValueError("mission.max_flight_time_s must be positive")
    if int(config["vision"]["required_hits"]) < 1:
        raise ValueError("vision.required_hits must be at least 1")
    if float(config["control"]["command_rate_hz"]) <= 0:
        raise ValueError("control.command_rate_hz must be positive")
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
    required_ardupilot_parameters(config)


class Vehicle:
    VELOCITY_ONLY_MASK = 3527

    def __init__(self, connection: str) -> None:
        print(f"[MAVLINK] Connecting to {connection}")
        self.master = mavutil.mavlink_connection(connection, source_system=245, source_component=191)
        hb = self.master.wait_heartbeat(timeout=30)
        if hb is None:
            raise RuntimeError("No ArduPilot heartbeat")
        self.target_system = self.master.target_system
        self.target_component = self.master.target_component or 1
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
        self.last_heartbeat = time.monotonic()
        self._request_interval(mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT, 10.0)
        self._request_interval(mavutil.mavlink.MAVLINK_MSG_ID_MISSION_CURRENT, 4.0)
        print(f"[MAVLINK] Connected system={self.target_system} component={self.target_component}")

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
        if kind == "HEARTBEAT":
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


def pipeline(port: int) -> str:
    return (
        f'udpsrc address=0.0.0.0 port={port} '
        'caps="application/x-rtp,media=video,encoding-name=H264,payload=96" ! '
        'rtpjitterbuffer latency=70 drop-on-latency=true ! '
        'rtph264depay ! h264parse ! avdec_h264 ! '
        'videoconvert ! video/x-raw,format=BGR ! '
        'appsink drop=true max-buffers=1 sync=false'
    )


def open_camera(port: int) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(pipeline(port), cv2.CAP_GSTREAMER)
    if not cap.isOpened():
        raise RuntimeError(f"Camera UDP {port} did not open. Run enable_camera and close every old viewer.")
    return cap


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
    def __init__(self, config: dict[str, Any], vehicle: Vehicle, camera: cv2.VideoCapture) -> None:
        self.config = config
        self.vehicle = vehicle
        self.camera = camera
        vcfg = config["vision"]
        self.detector = StrictShapeDetector(vcfg["search_min_area_px"], vcfg["tracking_min_area_px"], vcfg["debug_rejects"])
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
        self.last_center_error_px: Optional[float] = None
        self.last_center_forward: Optional[float] = None
        self.last_center_right: Optional[float] = None
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

    def send_velocity(self, forward: float, right: float, down: float) -> None:
        now = time.monotonic()
        if now - self.last_velocity_at >= 1.0 / float(self.config["control"]["command_rate_hz"]):
            max_guided_speed = self.safety.get("max_guided_speed_m_s")
            if max_guided_speed is not None:
                forward = clamp(forward, float(max_guided_speed))
                right = clamp(right, float(max_guided_speed))
            self.vehicle.send_body_velocity(forward, right, down)
            self.last_velocity_at = now

    def request_mode_repeated(self, mode: str) -> None:
        now = time.monotonic()
        if now - self.last_mode_request_at >= 1.0:
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

    def abandon_target_and_resume_auto(self, now: float, reason: str) -> None:
        print(f"[TARGET ABORT] {reason}; returning to AUTO")
        self.send_velocity(0.0, 0.0, self.altitude_down())
        self.current_target = None
        self.last_detection = None
        self.tracker.reset()
        self.vehicle.set_mode("AUTO")
        self.last_mode_request_at = now
        self.transition(State.WAITING_FOR_AUTO_RESUME, reason)

    def payload_action(self, now: float) -> None:
        p = self.config["payload"]
        safety_error = self.payload_safety_error()
        if safety_error:
            self.abandon_target_and_resume_auto(now, f"payload blocked: {safety_error}")
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
            if self.vehicle.armed and self.vehicle.mode == "AUTO" and self.vehicle.mission_seq is not None and self.vehicle.mission_seq >= int(m["search_start_wp"]):
                self.transition(State.SEARCH, f"AUTO item {self.vehicle.mission_seq}")

        elif self.state == State.SEARCH:
            if self.vehicle.mode != "AUTO":
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
            elif now - self.state_started_at > float(m["mode_change_timeout_s"]):
                self.current_target = None
                self.last_detection = None
                self.tracker.reset()
                self.transition(State.WAITING_FOR_AUTO, "GUIDED timeout")
            else:
                self.request_mode_repeated("GUIDED")

        elif self.state == State.CENTER:
            if self.vehicle.mode != "GUIDED":
                if self.vehicle.mode == "AUTO":
                    if self.guided_mode_lost_since is None:
                        self.guided_mode_lost_since = now
                    elapsed = now - self.guided_mode_lost_since
                    grace_s = self.safety.get("guided_auto_bounce_grace_s")
                    if grace_s is not None and elapsed <= float(grace_s):
                        self.status_message = f"GUIDED bounce guard: mode=AUTO for {elapsed:.1f}/{float(grace_s):.1f}s"
                        self.request_mode_repeated("GUIDED")
                        return detections, masks
                self.abandon_target_and_resume_auto(now, f"left GUIDED: {self.vehicle.mode}")
                return detections, masks
            self.guided_mode_lost_since = None
            max_center_time_s = self.safety.get("max_center_time_s")
            if (
                max_center_time_s is not None
                and self.center_started_at is not None
                and now - self.center_started_at > float(max_center_time_s)
            ):
                self.abandon_target_and_resume_auto(now, f"center timeout after {float(max_center_time_s):.1f}s")
                return detections, masks
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
            if self.last_detection is None or now - self.last_seen_at > float(c["target_lost_timeout_s"]):
                self.abandon_target_and_resume_auto(now, "target lost")
                return detections, masks
            forward, right, distance = self.centre_velocity(self.last_detection, width, height)
            self.last_center_error_px = distance
            self.last_center_forward = forward
            self.last_center_right = right
            if distance <= float(c["center_tolerance_px"]):
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
                self.status_message = f"Centering {self.current_target}: err={distance:.0f}px fwd={forward:.2f} right={right:.2f}"
            self.send_velocity(forward, right, self.altitude_down())

        elif self.state == State.PAYLOAD:
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
            if self.vehicle.armed and self.vehicle.mode == "AUTO" and self.vehicle.mission_seq is not None and self.vehicle.mission_seq >= int(m["search_start_wp"]):
                self.reset_for_next_mission(f"new AUTO run at waypoint {self.vehicle.mission_seq}")

        return detections, masks

    def draw(self, frame, detections):
        out = frame.copy()
        h, w = out.shape[:2]
        image_center = (w // 2, h // 2)
        cv2.drawMarker(out, image_center, (255, 255, 255), cv2.MARKER_CROSS, 34, 2)
        draw_items = list(detections)
        if self.last_detection and all(item.target != self.last_detection.target for item in draw_items):
            draw_items.append(self.last_detection)
        for item in draw_items:
            colour = (0, 0, 255) if item.target == "red_triangle" else (255, 0, 0)
            cv2.rectangle(out, (item.bbox_x, item.bbox_y), (item.bbox_x + item.bbox_w, item.bbox_y + item.bbox_h), colour, 2)
            cv2.circle(out, (item.center_x, item.center_y), 5, colour, -1)
            cv2.line(out, image_center, (item.center_x, item.center_y), colour, 2, cv2.LINE_AA)
            error = math.hypot(item.center_x - image_center[0], item.center_y - image_center[1])
            cv2.putText(out, f"{item.target} conf={item.confidence:.2f} v={item.vertices} ext={item.extent:.2f}",
                        (item.bbox_x, max(24, item.bbox_y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 2, cv2.LINE_AA)
            cv2.putText(out, f"err={error:.0f}px tol={float(self.config['control']['center_tolerance_px']):.0f}px",
                        (item.bbox_x, item.bbox_y + item.bbox_h + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 2, cv2.LINE_AA)
        hits = self.tracker.status()
        alt = "unknown" if self.vehicle.relative_alt_m is None else f"{self.vehicle.relative_alt_m:.2f} m"
        centered_for = 0.0 if self.centered_since is None else time.monotonic() - self.centered_since
        center_error = "none" if self.last_center_error_px is None else f"{self.last_center_error_px:.0f}px"
        lines = [
            f"STATE: {self.state.value}", f"MODE: {self.vehicle.mode}", f"ALT: {alt}",
            f"ACTION: {self.status_message}",
            f"TARGET: {self.current_target or 'none'} center_err={center_error} centered_for={centered_for:.1f}s",
            f"WAYPOINT: {self.vehicle.mission_seq}",
            f"MISSIONS DONE: {self.mission_done_count}",
            f"red_triangle hits: {hits['red_triangle']}/{self.config['vision']['required_hits']}",
            f"blue_hexagon hits: {hits['blue_hexagon']}/{self.config['vision']['required_hits']}",
            f"DONE: {sorted(self.completed_targets)}",
        ]
        y = 24
        for line in lines:
            cv2.putText(out, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
            y += 24
        def fmt(value: Optional[float], unit: str) -> str:
            return "n/a" if value is None else f"{value:.2f}{unit}"

        telemetry = [
            f"hspd {fmt(self.vehicle.horizontal_speed_m_s, 'm/s')}",
            f"vspd {fmt(None if self.vehicle.velocity_down_m_s is None else -self.vehicle.velocity_down_m_s, 'm/s')}",
            f"spd  {fmt(self.vehicle.total_speed_m_s, 'm/s')}",
            f"acc  {fmt(self.vehicle.acceleration_m_s2, 'm/s2')}",
        ]
        ty = h - 74
        for line in telemetry:
            cv2.putText(out, line, (12, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
            ty += 18
        return out

    def run(self) -> int:
        print("=" * 72)
        print("WD DRONE TARGET MISSION V2")
        print("QGC/ARDUPILOT OWNS AUTO ALTITUDE/SPEED. COMPANION CENTERS TARGETS IN GUIDED.")
        print("=" * 72)
        try:
            while not self.stop_requested:
                self.vehicle.poll()
                if time.monotonic() - self.vehicle.last_heartbeat > float(self.config["mavlink"].get("heartbeat_timeout_s", 8.0)):
                    print("[FATAL] Heartbeat lost")
                    return 3
                ok, frame = self.camera.read()
                if not ok or frame is None:
                    time.sleep(0.02)
                    continue
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
                self.vehicle.send_body_velocity(0.0, 0.0, 0.0)
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
    vehicle = Vehicle(config["mavlink"]["connection"])
    enforce_parameters(vehicle, config)
    camera = open_camera(int(config["camera"]["udp_port"]))
    controller = Controller(config, vehicle, camera)
    signal.signal(signal.SIGINT, lambda *_: setattr(controller, "stop_requested", True))
    signal.signal(signal.SIGTERM, lambda *_: setattr(controller, "stop_requested", True))
    return controller.run()


if __name__ == "__main__":
    raise SystemExit(main())

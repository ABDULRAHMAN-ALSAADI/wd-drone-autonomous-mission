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
import threading
import time
from collections import deque
from dataclasses import asdict
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Optional

import cv2
from pymavlink import mavutil

from camera_sources import CameraFrame, CameraLike, open_camera
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
from payload import (
    TARGET_PAYLOAD_COLOUR,
    payload_colour_for_target,
    payload_output_for_target,
)
from vision import (
    Detection,
    HitTracker,
    ProcessedVisionFrame,
    create_detector,
)
from control import altitude_velocity_down


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
        self.latitude_deg: Optional[float] = None
        self.longitude_deg: Optional[float] = None
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
        self.last_position_at: Optional[float] = None
        self.last_mission_at: Optional[float] = None
        self.last_rc_at: Optional[float] = None
        self.gps_fix_type: Optional[int] = None
        self.gps_satellites: Optional[int] = None
        self.last_gps_at: Optional[float] = None
        self.battery_voltage_v: Optional[float] = None
        self.battery_remaining_pct: Optional[int] = None
        self.last_battery_at: Optional[float] = None
        self.ekf_flags: Optional[int] = None
        self.last_ekf_at: Optional[float] = None
        self.roll_rad: Optional[float] = None
        self.pitch_rad: Optional[float] = None
        self.yaw_rad: Optional[float] = None
        self.last_attitude_at: Optional[float] = None
        self.command_acks: dict[int, tuple[float, int]] = {}
        self._request_interval(mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT, 10.0)
        self._request_interval(mavutil.mavlink.MAVLINK_MSG_ID_MISSION_CURRENT, 4.0)
        self._request_interval(mavutil.mavlink.MAVLINK_MSG_ID_RC_CHANNELS, 4.0)
        self._request_interval(mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT, 4.0)
        self._request_interval(mavutil.mavlink.MAVLINK_MSG_ID_SYS_STATUS, 2.0)
        self._request_interval(mavutil.mavlink.MAVLINK_MSG_ID_EKF_STATUS_REPORT, 2.0)
        self._request_interval(mavutil.mavlink.MAVLINK_MSG_ID_ATTITUDE, 10.0)
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

    def recover_vehicle_heartbeat(self, timeout_s: float = 1.0) -> bool:
        """Drain a telemetry backlog before declaring the vehicle heartbeat lost."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            msg = self.master.recv_match(blocking=True, timeout=min(0.1, max(0.0, deadline - time.monotonic())))
            if msg is None:
                continue
            self._handle_message(msg)
            if msg.get_type() == "HEARTBEAT" and heartbeat_is_target_vehicle(msg, self.target_system):
                return True
        return False

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
            now = time.monotonic()
            self.relative_alt_m = msg.relative_alt / 1000.0
            self.latitude_deg = float(msg.lat) / 1e7
            self.longitude_deg = float(msg.lon) / 1e7
            self.last_position_at = now
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
            self.last_mission_at = time.monotonic()
        elif kind == "RC_CHANNELS":
            for channel in range(1, 19):
                value = int(getattr(msg, f"chan{channel}_raw", 0))
                if value > 0:
                    self.rc_channels[channel] = value
            self.last_rc_at = time.monotonic()
        elif kind == "GPS_RAW_INT":
            self.gps_fix_type = int(msg.fix_type)
            self.gps_satellites = int(msg.satellites_visible)
            self.last_gps_at = time.monotonic()
        elif kind == "SYS_STATUS":
            voltage_mv = int(getattr(msg, "voltage_battery", 0))
            self.battery_voltage_v = voltage_mv / 1000.0 if voltage_mv not in (0, 65535) else None
            remaining = int(getattr(msg, "battery_remaining", -1))
            self.battery_remaining_pct = remaining if remaining >= 0 else None
            self.last_battery_at = time.monotonic()
        elif kind == "EKF_STATUS_REPORT":
            self.ekf_flags = int(msg.flags)
            self.last_ekf_at = time.monotonic()
        elif kind == "ATTITUDE":
            self.roll_rad = float(msg.roll)
            self.pitch_rad = float(msg.pitch)
            self.yaw_rad = float(msg.yaw)
            self.last_attitude_at = time.monotonic()
        elif kind == "STATUSTEXT":
            text = msg.text.decode(errors="replace") if isinstance(msg.text, bytes) else msg.text
            if int(msg.severity) <= mavutil.mavlink.MAV_SEVERITY_WARNING:
                print(f"[ARDUPILOT] {text}")
        elif kind == "COMMAND_ACK":
            result = mavutil.mavlink.enums["MAV_RESULT"].get(msg.result)
            print(f"[COMMAND ACK] command={msg.command} result={result.name if result else msg.result}")
            self.command_acks[int(msg.command)] = (time.monotonic(), int(msg.result))

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

    def set_servo(self, channel: int, pwm: int) -> float:
        sent_at = time.monotonic()
        self.master.mav.command_long_send(
            self.target_system, self.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_SERVO, 0,
            float(channel), float(pwm), 0, 0, 0, 0, 0,
        )
        return sent_at

    def command_ack_after(self, command: int, sent_at: float) -> Optional[int]:
        ack = self.command_acks.get(int(command))
        if ack is None or ack[0] < sent_at:
            return None
        return ack[1]

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


class MjpegFrameServer:
    """Serve the mission overlay without encoding in the mission loop."""

    def __init__(self, display_config: dict[str, Any]) -> None:
        self.enabled = bool(display_config.get("mjpeg_stream_enabled", False))
        self.bind = str(display_config.get("mjpeg_stream_bind", "127.0.0.1"))
        self.port = int(display_config.get("mjpeg_stream_port", 5602))
        self.max_fps = float(display_config.get("mjpeg_stream_fps", 10.0))
        self.quality = int(display_config.get("mjpeg_stream_quality", 65))
        self.width = int(display_config.get("mjpeg_stream_width", 960))
        self._condition = threading.Condition()
        self._raw_frame: Optional[Any] = None
        self._raw_sequence = 0
        self._raw_consumed_sequence = 0
        self._jpeg: Optional[bytes] = None
        self._sequence = 0
        self._stopping = False
        self._last_encode_at = 0.0
        self._submitted_count = 0
        self._encoded_count = 0
        self._overwritten_count = 0
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._encoder_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.enabled:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path not in {"/", "/stream.mjpg"}:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                sequence = -1
                try:
                    while True:
                        with owner._condition:
                            owner._condition.wait_for(
                                lambda: owner._stopping or owner._sequence != sequence,
                                timeout=1.0,
                            )
                            if owner._stopping:
                                return
                            if owner._jpeg is None or owner._sequence == sequence:
                                continue
                            jpeg = owner._jpeg
                            sequence = owner._sequence
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii"))
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    return

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        try:
            self._server = ThreadingHTTPServer((self.bind, self.port), Handler)
            self._server.daemon_threads = True
        except OSError as exc:
            self.enabled = False
            print(f"[VIDEO STREAM WARNING] disabled: {exc}")
            return
        self._encoder_thread = threading.Thread(
            target=self._encode_loop,
            name="mission-video-encoder",
            daemon=True,
        )
        self._encoder_thread.start()
        self._thread = threading.Thread(target=self._server.serve_forever, name="mission-video", daemon=True)
        self._thread.start()
        print(f"[VIDEO STREAM] http://{self.bind}:{self.port}/stream.mjpg via SSH tunnel")

    def publish(self, frame: Any) -> None:
        if not self.enabled:
            return
        with self._condition:
            if self._raw_sequence != self._raw_consumed_sequence:
                self._overwritten_count += 1
            self._raw_frame = frame
            self._raw_sequence += 1
            self._submitted_count += 1
            self._condition.notify_all()

    def _encode_loop(self) -> None:
        minimum_interval = 1.0 / max(0.1, self.max_fps)
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._stopping or self._raw_sequence != self._raw_consumed_sequence,
                    timeout=1.0,
                )
                if self._stopping:
                    return
                if self._raw_frame is None:
                    continue
                frame = self._raw_frame
                self._raw_consumed_sequence = self._raw_sequence

            delay = minimum_interval - (time.monotonic() - self._last_encode_at)
            if delay > 0:
                time.sleep(delay)
            output = frame
            if output.shape[1] > self.width:
                scale = self.width / output.shape[1]
                output = cv2.resize(
                    output,
                    (self.width, max(1, round(output.shape[0] * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            ok, encoded = cv2.imencode(
                ".jpg",
                output,
                [int(cv2.IMWRITE_JPEG_QUALITY), self.quality],
            )
            self._last_encode_at = time.monotonic()
            if not ok:
                continue
            with self._condition:
                self._jpeg = encoded.tobytes()
                self._sequence += 1
                self._encoded_count += 1
                self._condition.notify_all()

    def metrics(self) -> dict[str, int]:
        with self._condition:
            return {
                "submitted": self._submitted_count,
                "encoded": self._encoded_count,
                "overwritten": self._overwritten_count,
            }

    def stop(self) -> None:
        if not self.enabled and self._server is None:
            return
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._encoder_thread is not None:
            self._encoder_thread.join(timeout=2.0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)


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
        assert self.payload_state_path is not None
        if not self.payload_state_path.exists():
            print(f"[PAYLOAD STATE] empty: {self.payload_state_path}")
            return
        data = json.loads(self.payload_state_path.read_text(encoding="utf-8"))
        profile = str(self.config["mission"].get("name", ""))
        if data.get("mission_profile") != profile:
            raise RuntimeError(
                f"Payload state profile mismatch: file={data.get('mission_profile')} config={profile}. "
                "Reset the state explicitly before this attempt."
            )
        targets = data.get("targets", {})
        self.completed_targets.update(
            target for target, record in targets.items()
            if bool(record.get("payload_release_attempted", False))
        )
        print(f"[PAYLOAD STATE] loaded completed={sorted(self.completed_targets)} path={self.payload_state_path}")

    def _persist_payload_release(self, target: str, payload_colour: str, accepted: bool) -> None:
        if self.payload_state_path is None:
            return
        output = payload_output_for_target(self.config["payload"], target)
        data: dict[str, Any] = {
            "mission_profile": self.config["mission"].get("name"),
            "targets": {},
        }
        if self.payload_state_path.exists():
            data = json.loads(self.payload_state_path.read_text(encoding="utf-8"))
        data.setdefault("targets", {})[target] = {
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "waypoint": self.vehicle.mission_seq,
            "payload_colour": payload_colour,
            "payload_mechanism": self.config["payload"].get(
                "mechanism",
                "legacy_simulation",
            ),
            "servo_channel": output["servo_channel"],
            "release_pwm": output["release_pwm"],
            "payload_command_accepted": bool(accepted),
            "payload_release_attempted": True,
            "physical_release_confirmed": False,
        }
        self.payload_state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.payload_state_path.with_suffix(self.payload_state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.payload_state_path)

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
        return None if timestamp is None else now - timestamp

    def guidance_health_errors(self, expected_mode: str, now: Optional[float] = None) -> list[str]:
        now = time.monotonic() if now is None else now
        errors: list[str] = []
        if not self.vehicle.armed:
            errors.append("vehicle is disarmed")
        if self.vehicle.mode != expected_mode:
            errors.append(f"mode is {self.vehicle.mode}, expected {expected_mode}")

        heartbeat_age = now - float(getattr(self.vehicle, "last_heartbeat", 0.0))
        if heartbeat_age > float(self.safety.get("heartbeat_recent_s", 3.0)):
            errors.append(f"heartbeat stale ({heartbeat_age:.1f}s)")

        position_age = self._age(now, getattr(self.vehicle, "last_position_at", None))
        if self.vehicle.relative_alt_m is None:
            errors.append("altitude unknown")
        elif position_age is not None and position_age > float(self.safety.get("position_recent_s", 3.0)):
            errors.append(f"position/altitude stale ({position_age:.1f}s)")
        elif bool(self.safety.get("require_position", False)) and position_age is None:
            errors.append("position timestamp unavailable")

        min_alt = self.safety.get("payload_min_altitude_m")
        max_alt = self.safety.get("payload_max_altitude_m")
        if self.vehicle.relative_alt_m is not None:
            if min_alt is not None and self.vehicle.relative_alt_m < float(min_alt):
                errors.append(f"altitude {self.vehicle.relative_alt_m:.2f}m below {float(min_alt):.2f}m")
            if max_alt is not None and self.vehicle.relative_alt_m > float(max_alt):
                errors.append(f"altitude {self.vehicle.relative_alt_m:.2f}m above {float(max_alt):.2f}m")

        if bool(self.safety.get("require_gps", False)):
            gps_age = self._age(now, getattr(self.vehicle, "last_gps_at", None))
            if gps_age is None or gps_age > float(self.safety.get("gps_recent_s", 4.0)):
                errors.append("GPS status unavailable or stale")
            fix = getattr(self.vehicle, "gps_fix_type", None)
            satellites = getattr(self.vehicle, "gps_satellites", None)
            if fix is None or fix < int(self.safety.get("min_gps_fix_type", 3)):
                errors.append(f"GPS fix {fix} below required {int(self.safety.get('min_gps_fix_type', 3))}")
            if satellites is None or satellites < int(self.safety.get("min_gps_satellites", 6)):
                errors.append(f"GPS satellites {satellites} below required {int(self.safety.get('min_gps_satellites', 6))}")

        if bool(self.safety.get("require_ekf_status", False)):
            flags = getattr(self.vehicle, "ekf_flags", None)
            required = int(self.safety.get("ekf_required_flags", 51))
            if flags is None:
                errors.append("EKF status unavailable")
            elif flags & required != required:
                errors.append(f"EKF flags 0x{flags:x} missing required 0x{required:x}")

        battery = getattr(self.vehicle, "battery_voltage_v", None)
        minimum_battery = self.safety.get("min_battery_voltage_v")
        if bool(self.safety.get("require_battery", False)) and battery is None:
            errors.append("battery voltage unavailable")
        if minimum_battery is not None and battery is not None and battery < float(minimum_battery):
            errors.append(f"battery {battery:.2f}V below {float(minimum_battery):.2f}V")

        if bool(self.safety.get("require_attitude", False)):
            attitude_age = self._age(now, getattr(self.vehicle, "last_attitude_at", None))
            if attitude_age is None:
                errors.append("attitude unavailable")
            elif attitude_age > float(self.safety.get("attitude_recent_s", 1.0)):
                errors.append(f"attitude stale ({attitude_age:.1f}s)")

        enabled, reason = self.search_gate_status(now=now)
        if not enabled:
            errors.append(f"autonomy gate disabled: {reason}")
        return errors

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
        """Require a new strict full-resolution shape result before release."""
        target = self.current_target
        verify = getattr(self.detector, "verify_target_frame", None)
        previous_reason = self.last_payload_geometry_reason
        was_verified = self.last_payload_geometry_detection is not None
        self.last_payload_geometry_at = 0.0
        self.last_payload_geometry_detection = None
        self.last_payload_geometry_error_px = None

        if target not in TARGET_PAYLOAD_COLOUR:
            reason = f"invalid payload target {target!r}"
            detection = None
        elif self.last_detection is None:
            reason = "no active visual lock"
            detection = None
        elif not callable(verify):
            reason = "detector has no full-resolution payload verifier"
            detection = None
        else:
            expected_center = (
                self.last_detection.center_x / max(1.0, float(process_width)),
                self.last_detection.center_y / max(1.0, float(process_height)),
            )
            detection = verify(
                full_resolution_frame,
                target,
                expected_center_normalized=expected_center,
                max_center_distance_fraction=float(
                    self.config["vision"].get(
                        "payload_association_max_fraction",
                        0.18,
                    )
                ),
                frame_id=frame_id,
                captured_at_s=captured_at_s,
                received_at_s=received_at_s,
            )
            verification = getattr(
                self.detector,
                "last_payload_verification",
                {},
            )
            reason = str(
                verification.get(
                    "reason",
                    "strict full-resolution geometry failed",
                )
            )

        if detection is not None:
            source = str(getattr(detection, "source", ""))
            if (
                source != "geometry_payload"
                or detection.status != "valid_shape"
                or detection.target != target
            ):
                detection = None
                reason = "payload verifier returned non-strict evidence"

        if detection is not None:
            frame_height, frame_width = full_resolution_frame.shape[:2]
            process_desired_x, process_desired_y = self.desired_drop_point(
                process_width,
                process_height,
            )
            desired_x = process_desired_x * frame_width / max(
                1.0,
                float(process_width),
            )
            desired_y = process_desired_y * frame_height / max(
                1.0,
                float(process_height),
            )
            center_error = math.hypot(
                detection.center_x - desired_x,
                detection.center_y - desired_y,
            )
            tolerance_scale = frame_width / max(1.0, float(process_width))
            full_resolution_tolerance = (
                self.center_tolerance_px() * tolerance_scale
            )
            if center_error > full_resolution_tolerance:
                detection = None
                reason = (
                    f"full-resolution center error {center_error:.1f}px exceeds "
                    f"{full_resolution_tolerance:.1f}px"
                )
            else:
                self.last_payload_geometry_at = now
                self.last_payload_geometry_detection = detection
                self.last_payload_geometry_error_px = center_error
                self.last_payload_geometry_reason = (
                    "fresh full-resolution strict geometry"
                )
                self.last_strong_geometry_at = now
                if not was_verified:
                    self.event(
                        "PAYLOAD_GEOMETRY_VERIFIED",
                        target=target,
                        frame_id=frame_id,
                        error_px=round(center_error, 1),
                        source=source,
                    )
                return True

        self.last_payload_geometry_reason = reason
        if reason != previous_reason:
            self.event(
                "PAYLOAD_GEOMETRY_REJECTED",
                target=target,
                frame_id=frame_id,
                reason=reason,
            )
        return False

    def payload_release_gate_errors(self, now: Optional[float] = None) -> list[str]:
        timestamp = time.monotonic() if now is None else float(now)
        errors = self.guidance_health_errors("GUIDED", timestamp)
        target = self.current_target
        if target not in TARGET_PAYLOAD_COLOUR:
            errors.append(f"invalid target class {target!r}")
            return errors
        if target in self.completed_targets:
            errors.append(f"payload already attempted for {target}")
        if self.last_detection is None or self.last_detection.target != target:
            errors.append("current target has no matching visual track")

        frame_age = timestamp - self.last_frame_at
        frame_limit = float(self.safety.get("camera_frame_timeout_s", 2.0))
        if frame_age > frame_limit:
            errors.append(f"camera frame stale ({frame_age:.2f}s)")
        result_age = timestamp - self.last_tracking_at if self.last_tracking_at else float("inf")
        if result_age > float(self.safety.get("max_vision_result_age_s", 0.75)):
            errors.append(f"tracking result stale ({result_age:.2f}s)")
        geometry_age = (
            timestamp - self.last_strong_geometry_at
            if self.last_strong_geometry_at
            else float("inf")
        )
        if geometry_age > float(self.safety.get("max_strong_geometry_age_s", 0.7)):
            errors.append(f"strong geometry stale ({geometry_age:.2f}s)")
        payload_geometry = self.last_payload_geometry_detection
        if payload_geometry is None:
            errors.append(
                "fresh full-resolution strict geometry missing "
                f"({self.last_payload_geometry_reason})"
            )
        else:
            if payload_geometry.target != target:
                errors.append("payload geometry target does not match active target")
            if payload_geometry.source != "geometry_payload":
                errors.append("payload geometry source is not strict")
            if payload_geometry.status != "valid_shape":
                errors.append("payload geometry status is not valid_shape")
            payload_geometry_age = timestamp - self.last_payload_geometry_at
            if payload_geometry_age > float(
                self.safety.get("max_payload_geometry_age_s", 0.5)
            ):
                errors.append(
                    f"payload geometry stale ({payload_geometry_age:.2f}s)"
                )
            if self.last_payload_geometry_error_px is None:
                errors.append("full-resolution payload center error unavailable")
        if self.last_center_error_px is None:
            errors.append("center error unavailable")
        elif self.last_center_error_px > self.center_tolerance_px():
            errors.append(
                f"center error {self.last_center_error_px:.1f}px exceeds "
                f"{self.center_tolerance_px():.1f}px"
            )
        if not self.center_lock_completed_at:
            errors.append("continuous center hold not completed")
        variance = self.center_error_variance_px(timestamp)
        if variance is None:
            errors.append("center variance unavailable")
        elif variance > float(self.safety.get("max_center_variance_px", 8.0)):
            errors.append(
                f"center variation {variance:.1f}px exceeds "
                f"{float(self.safety.get('max_center_variance_px', 8.0)):.1f}px"
            )

        horizontal_speed = getattr(self.vehicle, "horizontal_speed_m_s", None)
        if horizontal_speed is None:
            errors.append("horizontal speed unavailable")
        elif horizontal_speed > float(
            self.safety.get("max_payload_horizontal_speed_m_s", 0.6)
        ):
            errors.append(f"horizontal speed {horizontal_speed:.2f}m/s too high")
        vertical_down = getattr(self.vehicle, "velocity_down_m_s", None)
        if vertical_down is None:
            errors.append("vertical speed unavailable")
        elif abs(vertical_down) > float(
            self.safety.get("max_payload_vertical_speed_m_s", 0.4)
        ):
            errors.append(f"vertical speed {abs(vertical_down):.2f}m/s too high")

        if bool(self.safety.get("require_attitude", False)):
            roll = getattr(self.vehicle, "roll_rad", None)
            pitch = getattr(self.vehicle, "pitch_rad", None)
            if roll is None or pitch is None:
                errors.append("roll/pitch unavailable")
            else:
                roll_deg = abs(math.degrees(roll))
                pitch_deg = abs(math.degrees(pitch))
                if roll_deg > float(self.safety.get("max_payload_roll_deg", 15.0)):
                    errors.append(f"roll {roll_deg:.1f}deg too high")
                if pitch_deg > float(self.safety.get("max_payload_pitch_deg", 15.0)):
                    errors.append(f"pitch {pitch_deg:.1f}deg too high")

        displacement = self.guided_displacement_m()
        max_displacement = self.safety.get("max_guided_displacement_m")
        if (
            displacement is not None
            and max_displacement is not None
            and displacement > float(max_displacement)
        ):
            errors.append(
                f"GUIDED displacement {displacement:.1f}m exceeds "
                f"{float(max_displacement):.1f}m"
            )
        if self.camera_timeout_active:
            errors.append("camera timeout active")
        if self.target_loss_active:
            errors.append("target loss active")
        if self.active_abort_reason:
            errors.append(f"abort active: {self.active_abort_reason}")
        if self.manual_override_latched:
            errors.append("pilot override active")
        return errors

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
        p = self.config["payload"]
        if self.payload_release_sent_at is None:
            health_errors = self.payload_release_gate_errors(now)
            if health_errors:
                reason = "; ".join(health_errors)
                if self.payload_block_started_at is None:
                    self.payload_block_started_at = now
                self.status_message = f"Payload blocked: {reason}"
                self.send_velocity(0.0, 0.0, self.altitude_down(), force=True)
                if reason != self.last_payload_block_reason:
                    self.event("PAYLOAD_BLOCKED", target=self.current_target, reason=reason)
                    self.last_payload_block_reason = reason
                if now - self.payload_block_started_at >= float(
                    self.safety.get("payload_authorization_timeout_s", 8.0)
                ):
                    self.abandon_active_target(
                        now,
                        f"PAYLOAD_GATE_TIMEOUT {reason}",
                    )
                return
            self.last_payload_block_reason = ""
            self.payload_block_started_at = None
        self.send_velocity(0.0, 0.0, self.altitude_down())
        altitude = self.vehicle.relative_alt_m if self.vehicle.relative_alt_m is not None else float("nan")
        target = self.current_target or ""
        payload_colour = payload_colour_for_target(target)
        output = payload_output_for_target(p, target)
        simulate_only = bool(p["simulate_only"])
        ack_timeout_s = float(p.get("command_ack_timeout_s", 2.0))

        if self.payload_release_sent_at is None:
            self.status_message = f"Releasing {payload_colour} payload on {target}"
            if simulate_only:
                self.payload_release_sent_at = now
                self.payload_release_accepted = True
                self.payload_started = True
                self.event("PAYLOAD_SIMULATED", target=target, payload_colour=payload_colour, altitude_m=altitude)
            else:
                sent_at = self.vehicle.set_servo(
                    output["servo_channel"],
                    output["release_pwm"],
                )
                self.payload_release_sent_at = now if sent_at is None else float(sent_at)
                # Record the attempt before waiting for ACK so a process
                # restart cannot repeat a release that may have reached the servo.
                self._persist_payload_release(target, payload_colour, accepted=False)
                self.event(
                    "PAYLOAD_RELEASE_COMMAND_SENT",
                    target=target,
                    payload_colour=payload_colour,
                    servo_channel=output["servo_channel"],
                    pwm=output["release_pwm"],
                )
            return

        if not simulate_only and not self.payload_release_accepted:
            ack_reader = getattr(self.vehicle, "command_ack_after", None)
            result = ack_reader(mavutil.mavlink.MAV_CMD_DO_SET_SERVO, self.payload_release_sent_at) if callable(ack_reader) else None
            if result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                self.payload_release_accepted = True
                self.payload_started = True
                self._persist_payload_release(target, payload_colour, accepted=True)
                self.event("PAYLOAD_COMMAND_ACCEPTED", target=target, physical_release_confirmed=False)
            elif result is not None:
                self.payload_failed = True
                self.status_message = f"Payload command rejected: MAV_RESULT={result}; pilot action required"
                self.event("PAYLOAD_COMMAND_REJECTED", target=target, mav_result=result)
                return
            elif now - self.payload_release_sent_at > ack_timeout_s:
                self.payload_failed = True
                self.status_message = "Payload ACK timeout; pilot action required"
                self.event("PAYLOAD_ACK_TIMEOUT", target=target)
                return
            else:
                self.status_message = f"Waiting for payload command ACK on {target}"
                return

        if self.payload_failed:
            self.send_velocity(0.0, 0.0, self.altitude_down(), force=True)
            return

        elapsed = now - self.payload_release_sent_at
        if not simulate_only and self.payload_reset_sent_at is None and elapsed >= float(p["release_hold_s"]):
            sent_at = self.vehicle.set_servo(
                output["servo_channel"],
                output["reset_pwm"],
            )
            self.payload_reset_sent_at = now if sent_at is None else float(sent_at)
            self.event(
                "PAYLOAD_RESET_COMMAND_SENT",
                target=target,
                pwm=output["reset_pwm"],
            )
            return

        if not simulate_only and self.payload_reset_sent_at is not None and not self.payload_reset_accepted:
            ack_reader = getattr(self.vehicle, "command_ack_after", None)
            result = ack_reader(mavutil.mavlink.MAV_CMD_DO_SET_SERVO, self.payload_reset_sent_at) if callable(ack_reader) else None
            if result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                self.payload_reset_accepted = True
                self.payload_reset = True
                self.event("PAYLOAD_RESET_ACCEPTED", target=target)
            elif result is not None or now - self.payload_reset_sent_at > ack_timeout_s:
                self.payload_failed = True
                self.status_message = "Payload reset not confirmed; pilot action required"
                self.event("PAYLOAD_RESET_FAILED", target=target, mav_result=result)
                return
            else:
                self.status_message = f"Waiting for payload reset ACK on {target}"
                return

        action_ready = elapsed >= float(p["total_action_time_s"])
        reset_ready = simulate_only or self.payload_reset_accepted
        if action_ready and reset_ready:
            if self.current_target:
                if self.last_center_error_px is not None:
                    self.completed_center_errors[self.current_target] = self.last_center_error_px
                self.completed_targets.add(self.current_target)
                print(
                    f"[TARGET COMPLETE] {self.current_target}; "
                    f"lock_error={self.last_center_error_px}; done={sorted(self.completed_targets)}"
                )
            self.current_target = None
            self.last_detection = None
            self.tracker.reset()
            next_mode = "AUTO" if self.incomplete_targets() else str(self.config["mission"].get("final_mode", "RTL"))
            self.vehicle.set_mode(next_mode)
            self.last_mode_request_at = now
            next_state = State.WAITING_FOR_AUTO_RESUME if next_mode == "AUTO" else State.WAITING_FOR_RTL
            self.transition(next_state, "payload complete")

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
            if self.last_detection is None or now - self.last_seen_at > lost_timeout_s:
                self.centered_since = None
                self.target_loss_active = True
                self.abandon_active_target(
                    now,
                    f"TARGET_LOST {lost_for:.1f}s",
                )
                return detections, masks
            if not fresh_detection:
                self.centered_since = None
                self.status_message = (
                    f"Looking for {self.current_target} in GUIDED "
                    f"{lost_for:.1f}/{lost_timeout_s:.1f}s"
                )
                self.send_velocity(0.0, 0.0, self.altitude_down())
                return detections, masks
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
        out = frame.copy()
        h, w = out.shape[:2]
        desired = self.desired_drop_point(w, h)
        desired_center = (round(desired[0]), round(desired[1]))
        cv2.drawMarker(out, desired_center, (255, 255, 255), cv2.MARKER_CROSS, 26, 1)
        if self.current_target:
            cv2.circle(out, desired_center, round(self.center_tolerance_px()), (0, 255, 255), 1, cv2.LINE_AA)

        candidate_colours = {
            "colour_candidate": (0, 220, 255),
            "geometric_candidate": (0, 140, 255),
            "confirmed_geometry": (0, 255, 0),
            "temporary_colour_tracking": (0, 220, 255),
            "rejected": (150, 150, 150),
        }
        candidate_labels = {
            "colour_candidate": "COLOUR CANDIDATE",
            "geometric_candidate": "GEOMETRIC CANDIDATE",
            "confirmed_geometry": "STRICT GEOMETRY",
            "temporary_colour_tracking": "TEMP COLOUR TRACK",
            "rejected": "COLOUR CANDIDATE REJECTED",
        }
        for candidate in getattr(self.detector, "last_candidates", []):
            status = str(candidate.get("status", "colour_candidate"))
            bbox = candidate.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            x, y, box_width, box_height = (int(value) for value in bbox)
            colour = candidate_colours.get(status, (0, 220, 255))
            thickness = 1 if status == "rejected" else 2
            cv2.rectangle(
                out,
                (x, y),
                (x + box_width, y + box_height),
                colour,
                thickness,
            )
            if status == "rejected":
                cv2.line(
                    out,
                    (x, y),
                    (x + box_width, y + box_height),
                    colour,
                    1,
                    cv2.LINE_AA,
                )
                cv2.line(
                    out,
                    (x + box_width, y),
                    (x, y + box_height),
                    colour,
                    1,
                    cv2.LINE_AA,
                )
            label = candidate_labels.get(status, status.upper())
            cv2.putText(
                out,
                label,
                (x, max(16, y - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.38,
                colour,
                1,
                cv2.LINE_AA,
            )

        draw_items = list(detections)
        if self.last_detection and all(item.target != self.last_detection.target for item in draw_items):
            draw_items.append(self.last_detection)
        for item in draw_items:
            source = str(getattr(item, "source", ""))
            if source == "colour_track":
                status_label = "TEMP COLOUR TRACK"
                colour = (0, 220, 255)
            elif source == "geometry_track":
                status_label = "STRICT TRACK"
                colour = (0, 255, 0)
            elif source == "geometry_payload":
                status_label = "PAYLOAD GEOMETRY"
                colour = (255, 255, 0)
            elif self.current_target == item.target:
                status_label = "CONFIRMED TARGET"
                colour = (0, 255, 0)
            else:
                status_label = "GEOMETRIC CANDIDATE"
                colour = (0, 140, 255)
            cv2.rectangle(out, (item.bbox_x, item.bbox_y), (item.bbox_x + item.bbox_w, item.bbox_y + item.bbox_h), colour, 2)
            cv2.circle(out, (item.center_x, item.center_y), 5, colour, -1)
            cv2.line(out, desired_center, (item.center_x, item.center_y), colour, 2, cv2.LINE_AA)
            error = math.hypot(item.center_x - desired_center[0], item.center_y - desired_center[1])
            cv2.putText(out, f"{status_label}: {item.target} {item.confidence:.2f} err {error:.0f}px",
                        (item.bbox_x, max(18, item.bbox_y - 7)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, colour, 1, cv2.LINE_AA)
        alt = "unknown" if self.vehicle.relative_alt_m is None else f"{self.vehicle.relative_alt_m:.2f} m"
        centered_for = 0.0 if self.centered_since is None else time.monotonic() - self.centered_since
        center_error = "none" if self.last_center_error_px is None else f"{self.last_center_error_px:.0f}px"
        def fmt(value: Optional[float], unit: str) -> str:
            return "n/a" if value is None else f"{value:.2f}{unit}"

        done = ",".join(sorted(self.completed_targets)) if self.completed_targets else "none"
        drop_locks = ", ".join(
            f"{target.replace('_', ' ')} {error:.0f}px"
            for target, error in sorted(self.completed_center_errors.items())
        ) or "none"
        gate_enabled, gate_reason = self.search_gate_status()
        gate = "enabled" if gate_enabled else f"blocked: {gate_reason}"
        tracking_state = getattr(self.detector, "tracking_state", None)
        if tracking_state is None:
            strict_status = "no lock"
        else:
            strict_age = max(
                0.0,
                time.monotonic() - tracking_state.last_strong_geometry_at,
            )
            strict_status = (
                f"age {strict_age:.2f}s | failed checks "
                f"{tracking_state.failed_geometry_checks}/"
                f"{getattr(self.detector, 'max_failed_geometry_checks', 2)}"
            )
        evidence_source = (
            "none"
            if self.last_detection is None
            else str(self.last_detection.source)
        )
        payload_vision = (
            "VERIFIED"
            if self.last_payload_geometry_detection is not None
            else self.last_payload_geometry_reason
        )
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
            f"Vision: {evidence_source} | Strict {strict_status}",
            f"Payload geometry: {payload_vision}",
            f"Search gate: {gate}",
            f"Done: {done} | Runs {self.mission_done_count} | Guided bounces {self.guided_bounce_count}",
            f"Completed payload locks: {drop_locks}",
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


def manage_payload_state(config: dict[str, Any], reset: bool) -> int:
    state_cfg = config.get("payload_state", {})
    if not bool(state_cfg.get("enabled", False)):
        print("[PAYLOAD STATE] disabled in this profile")
        return 0
    path = Path(str(state_cfg["path"]))
    if reset:
        if path.exists():
            path.unlink()
            print(f"[PAYLOAD STATE] reset: {path}")
        else:
            print(f"[PAYLOAD STATE] already empty: {path}")
        return 0
    if not path.exists():
        print(f"[PAYLOAD STATE] empty: {path}")
        return 0
    print(path.read_text(encoding="utf-8"), end="")
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

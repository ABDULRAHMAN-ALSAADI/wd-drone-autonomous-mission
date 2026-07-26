"""MAVLink connection, telemetry state, commands, and parameter enforcement."""
from __future__ import annotations

import math
import time
from typing import Any, Optional

from pymavlink import mavutil

from config_validation import required_ardupilot_parameters


AUTOPILOT_COMPONENTS = {mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1}


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

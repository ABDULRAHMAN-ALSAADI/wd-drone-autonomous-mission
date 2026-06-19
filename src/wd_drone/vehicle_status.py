from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from pymavlink import mavutil


@dataclass
class VehicleStatus:
    last_heartbeat_monotonic: float | None = None
    system_id: int | None = None
    component_id: int | None = None
    armed: bool | None = None
    mode: str = "UNKNOWN"
    battery_voltage_v: float | None = None
    battery_remaining_pct: int | None = None
    gps_fix_type: int | None = None
    satellites_visible: int | None = None
    latitude_deg: float | None = None
    longitude_deg: float | None = None
    relative_altitude_m: float | None = None
    groundspeed_m_s: float | None = None
    heading_deg: float | None = None

    def update(self, message: Any) -> None:
        message_type = message.get_type()

        if message_type == "HEARTBEAT":
            self.last_heartbeat_monotonic = time.monotonic()
            self.system_id = message.get_srcSystem()
            self.component_id = message.get_srcComponent()
            self.armed = bool(
                message.base_mode
                & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
            )
            self.mode = mavutil.mode_string_v10(message)

        elif message_type == "SYS_STATUS":
            voltage_mv = getattr(message, "voltage_battery", 0)
            self.battery_voltage_v = (
                voltage_mv / 1000.0
                if voltage_mv not in (0, 65535)
                else None
            )
            remaining = getattr(message, "battery_remaining", -1)
            self.battery_remaining_pct = (
                int(remaining) if remaining >= 0 else None
            )

        elif message_type == "GPS_RAW_INT":
            self.gps_fix_type = int(message.fix_type)
            self.satellites_visible = int(message.satellites_visible)

        elif message_type == "GLOBAL_POSITION_INT":
            self.latitude_deg = message.lat / 1e7
            self.longitude_deg = message.lon / 1e7
            self.relative_altitude_m = message.relative_alt / 1000.0
            if message.hdg != 65535:
                self.heading_deg = message.hdg / 100.0

        elif message_type == "VFR_HUD":
            self.groundspeed_m_s = float(message.groundspeed)
            self.heading_deg = float(message.heading)

    def heartbeat_age_s(self) -> float | None:
        if self.last_heartbeat_monotonic is None:
            return None
        return time.monotonic() - self.last_heartbeat_monotonic

    def is_stale(self, stale_after_s: float) -> bool:
        age = self.heartbeat_age_s()
        return age is None or age > stale_after_s

    def format_line(self, stale_after_s: float) -> str:
        state = "STALE" if self.is_stale(stale_after_s) else "OK"
        armed_text = (
            "ARMED" if self.armed is True
            else "DISARMED" if self.armed is False
            else "UNKNOWN"
        )

        def fmt(value: float | int | None, digits: int = 1) -> str:
            if value is None:
                return "-"
            if isinstance(value, int):
                return str(value)
            return f"{value:.{digits}f}"

        return (
            f"link={state} "
            f"sys={self.system_id or '-'} "
            f"mode={self.mode} "
            f"state={armed_text} "
            f"gps_fix={self.gps_fix_type if self.gps_fix_type is not None else '-'} "
            f"sats={self.satellites_visible if self.satellites_visible is not None else '-'} "
            f"alt={fmt(self.relative_altitude_m)}m "
            f"speed={fmt(self.groundspeed_m_s)}m/s "
            f"heading={fmt(self.heading_deg, 0)}deg "
            f"battery={fmt(self.battery_voltage_v)}V/"
            f"{self.battery_remaining_pct if self.battery_remaining_pct is not None else '-'}%"
        )

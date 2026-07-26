"""Centralized guidance and payload release safety checks."""
from __future__ import annotations

import math
import time
from typing import Any, Optional

from payload import TARGET_PAYLOAD_COLOUR


def measurement_age(now: float, timestamp: Optional[float]) -> Optional[float]:
    return None if timestamp is None else now - timestamp


def guidance_health_errors(
    controller: Any,
    expected_mode: str,
    now: Optional[float] = None,
) -> list[str]:
    now = time.monotonic() if now is None else now
    errors: list[str] = []
    if not controller.vehicle.armed:
        errors.append("vehicle is disarmed")
    if controller.vehicle.mode != expected_mode:
        errors.append(f"mode is {controller.vehicle.mode}, expected {expected_mode}")

    heartbeat_age = now - float(getattr(controller.vehicle, "last_heartbeat", 0.0))
    if heartbeat_age > float(controller.safety.get("heartbeat_recent_s", 3.0)):
        errors.append(f"heartbeat stale ({heartbeat_age:.1f}s)")

    position_age = measurement_age(now, getattr(controller.vehicle, "last_position_at", None))
    if controller.vehicle.relative_alt_m is None:
        errors.append("altitude unknown")
    elif position_age is not None and position_age > float(controller.safety.get("position_recent_s", 3.0)):
        errors.append(f"position/altitude stale ({position_age:.1f}s)")
    elif bool(controller.safety.get("require_position", False)) and position_age is None:
        errors.append("position timestamp unavailable")

    min_alt = controller.safety.get("payload_min_altitude_m")
    max_alt = controller.safety.get("payload_max_altitude_m")
    if controller.vehicle.relative_alt_m is not None:
        if min_alt is not None and controller.vehicle.relative_alt_m < float(min_alt):
            errors.append(f"altitude {controller.vehicle.relative_alt_m:.2f}m below {float(min_alt):.2f}m")
        if max_alt is not None and controller.vehicle.relative_alt_m > float(max_alt):
            errors.append(f"altitude {controller.vehicle.relative_alt_m:.2f}m above {float(max_alt):.2f}m")

    if bool(controller.safety.get("require_gps", False)):
        gps_age = measurement_age(now, getattr(controller.vehicle, "last_gps_at", None))
        if gps_age is None or gps_age > float(controller.safety.get("gps_recent_s", 4.0)):
            errors.append("GPS status unavailable or stale")
        fix = getattr(controller.vehicle, "gps_fix_type", None)
        satellites = getattr(controller.vehicle, "gps_satellites", None)
        if fix is None or fix < int(controller.safety.get("min_gps_fix_type", 3)):
            errors.append(f"GPS fix {fix} below required {int(controller.safety.get('min_gps_fix_type', 3))}")
        if satellites is None or satellites < int(controller.safety.get("min_gps_satellites", 6)):
            errors.append(f"GPS satellites {satellites} below required {int(controller.safety.get('min_gps_satellites', 6))}")

    if bool(controller.safety.get("require_ekf_status", False)):
        flags = getattr(controller.vehicle, "ekf_flags", None)
        required = int(controller.safety.get("ekf_required_flags", 51))
        if flags is None:
            errors.append("EKF status unavailable")
        elif flags & required != required:
            errors.append(f"EKF flags 0x{flags:x} missing required 0x{required:x}")

    battery = getattr(controller.vehicle, "battery_voltage_v", None)
    minimum_battery = controller.safety.get("min_battery_voltage_v")
    if bool(controller.safety.get("require_battery", False)) and battery is None:
        errors.append("battery voltage unavailable")
    if minimum_battery is not None and battery is not None and battery < float(minimum_battery):
        errors.append(f"battery {battery:.2f}V below {float(minimum_battery):.2f}V")

    if bool(controller.safety.get("require_attitude", False)):
        attitude_age = measurement_age(now, getattr(controller.vehicle, "last_attitude_at", None))
        if attitude_age is None:
            errors.append("attitude unavailable")
        elif attitude_age > float(controller.safety.get("attitude_recent_s", 1.0)):
            errors.append(f"attitude stale ({attitude_age:.1f}s)")

    enabled, reason = controller.search_gate_status(now=now)
    if not enabled:
        errors.append(f"autonomy gate disabled: {reason}")
    return errors


def payload_safety_error(controller: Any) -> Optional[str]:
    if bool(controller.safety.get("payload_requires_guided", True)) and controller.vehicle.mode != "GUIDED":
        return f"vehicle mode is {controller.vehicle.mode}, not GUIDED"
    altitude = controller.vehicle.relative_alt_m
    min_alt = controller.safety.get("payload_min_altitude_m")
    max_alt = controller.safety.get("payload_max_altitude_m")
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


def payload_release_gate_errors(
    controller: Any,
    now: Optional[float] = None,
) -> list[str]:
    timestamp = time.monotonic() if now is None else float(now)
    errors = controller.guidance_health_errors("GUIDED", timestamp)
    target = controller.current_target
    if target not in TARGET_PAYLOAD_COLOUR:
        errors.append(f"invalid target class {target!r}")
        return errors
    if target in controller.completed_targets:
        errors.append(f"payload already attempted for {target}")
    if controller.last_detection is None or controller.last_detection.target != target:
        errors.append("current target has no matching visual track")

    frame_age = timestamp - controller.last_frame_at
    frame_limit = float(controller.safety.get("camera_frame_timeout_s", 2.0))
    if frame_age > frame_limit:
        errors.append(f"camera frame stale ({frame_age:.2f}s)")
    result_age = timestamp - controller.last_tracking_at if controller.last_tracking_at else float("inf")
    if result_age > float(controller.safety.get("max_vision_result_age_s", 0.75)):
        errors.append(f"tracking result stale ({result_age:.2f}s)")
    geometry_age = (
        timestamp - controller.last_strong_geometry_at
        if controller.last_strong_geometry_at
        else float("inf")
    )
    if geometry_age > float(controller.safety.get("max_strong_geometry_age_s", 0.7)):
        errors.append(f"strong geometry stale ({geometry_age:.2f}s)")
    payload_geometry = controller.last_payload_geometry_detection
    if payload_geometry is None:
        errors.append(
            "fresh full-resolution strict geometry missing "
            f"({controller.last_payload_geometry_reason})"
        )
    else:
        if payload_geometry.target != target:
            errors.append("payload geometry target does not match active target")
        if payload_geometry.source != "geometry_payload":
            errors.append("payload geometry source is not strict")
        if payload_geometry.status != "valid_shape":
            errors.append("payload geometry status is not valid_shape")
        payload_geometry_age = timestamp - controller.last_payload_geometry_at
        if payload_geometry_age > float(
            controller.safety.get("max_payload_geometry_age_s", 0.5)
        ):
            errors.append(
                f"payload geometry stale ({payload_geometry_age:.2f}s)"
            )
        if controller.last_payload_geometry_error_px is None:
            errors.append("full-resolution payload center error unavailable")
    if controller.last_center_error_px is None:
        errors.append("center error unavailable")
    elif controller.last_center_error_px > controller.center_tolerance_px():
        errors.append(
            f"center error {controller.last_center_error_px:.1f}px exceeds "
            f"{controller.center_tolerance_px():.1f}px"
        )
    if not controller.center_lock_completed_at:
        errors.append("continuous center hold not completed")
    variance = controller.center_error_variance_px(timestamp)
    if variance is None:
        errors.append("center variance unavailable")
    elif variance > float(controller.safety.get("max_center_variance_px", 8.0)):
        errors.append(
            f"center variation {variance:.1f}px exceeds "
            f"{float(controller.safety.get('max_center_variance_px', 8.0)):.1f}px"
        )

    horizontal_speed = getattr(controller.vehicle, "horizontal_speed_m_s", None)
    if horizontal_speed is None:
        errors.append("horizontal speed unavailable")
    elif horizontal_speed > float(
        controller.safety.get("max_payload_horizontal_speed_m_s", 0.6)
    ):
        errors.append(f"horizontal speed {horizontal_speed:.2f}m/s too high")
    vertical_down = getattr(controller.vehicle, "velocity_down_m_s", None)
    if vertical_down is None:
        errors.append("vertical speed unavailable")
    elif abs(vertical_down) > float(
        controller.safety.get("max_payload_vertical_speed_m_s", 0.4)
    ):
        errors.append(f"vertical speed {abs(vertical_down):.2f}m/s too high")

    if bool(controller.safety.get("require_attitude", False)):
        roll = getattr(controller.vehicle, "roll_rad", None)
        pitch = getattr(controller.vehicle, "pitch_rad", None)
        if roll is None or pitch is None:
            errors.append("roll/pitch unavailable")
        else:
            roll_deg = abs(math.degrees(roll))
            pitch_deg = abs(math.degrees(pitch))
            if roll_deg > float(controller.safety.get("max_payload_roll_deg", 15.0)):
                errors.append(f"roll {roll_deg:.1f}deg too high")
            if pitch_deg > float(controller.safety.get("max_payload_pitch_deg", 15.0)):
                errors.append(f"pitch {pitch_deg:.1f}deg too high")

    displacement = controller.guided_displacement_m()
    max_displacement = controller.safety.get("max_guided_displacement_m")
    if (
        displacement is not None
        and max_displacement is not None
        and displacement > float(max_displacement)
    ):
        errors.append(
            f"GUIDED displacement {displacement:.1f}m exceeds "
            f"{float(max_displacement):.1f}m"
        )
    if controller.camera_timeout_active:
        errors.append("camera timeout active")
    if controller.target_loss_active:
        errors.append("target loss active")
    if controller.active_abort_reason:
        errors.append(f"abort active: {controller.active_abort_reason}")
    if controller.manual_override_latched:
        errors.append("pilot override active")
    return errors

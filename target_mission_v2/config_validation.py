"""Mission configuration defaults and validation."""
from __future__ import annotations

from typing import Any

from camera_sources import SUPPORTED_CAMERA_SOURCES
from payload import TARGET_PAYLOAD_COLOUR, payload_output_for_target
from vision import SUPPORTED_VISION_BACKENDS


DEFAULT_SAFETY = {
    "max_center_time_s": 120.0,
    "center_warning_time_s": 30.0,
    "max_guided_speed_m_s": 0.45,
    "max_guided_displacement_m": 6.0,
    "center_progress_window_s": 12.0,
    "center_min_progress_px": 8.0,
    "max_lost_detection_s": 4.0,
    "max_single_direction_time_s": 20.0,
    "single_direction_deadband_m_s": 0.05,
    "max_guided_entry_time_s": 8.0,
    "payload_authorization_timeout_s": 8.0,
    "guidance_health_grace_s": 2.0,
    "center_filter_alpha": 0.35,
    "max_command_accel_m_s2": 0.8,
    "guided_auto_bounce_grace_s": None,
    "max_guided_auto_bounces_per_target": None,
    "active_target_abort_mode": "AUTO",
    "mode_retry_interval_s": 0.5,
    "camera_frame_timeout_s": 2.0,
    "max_input_frame_age_s": 0.50,
    "max_vision_result_age_s": 0.75,
    "max_strong_geometry_age_s": 0.7,
    "max_payload_geometry_age_s": 0.5,
    "max_payload_horizontal_speed_m_s": 0.6,
    "max_payload_vertical_speed_m_s": 0.4,
    "max_payload_roll_deg": 15.0,
    "max_payload_pitch_deg": 15.0,
    "max_center_variance_px": 8.0,
    "center_variance_window_s": 1.5,
    "require_attitude": False,
    "attitude_recent_s": 1.0,
    "payload_requires_guided": True,
    "payload_min_altitude_m": None,
    "payload_max_altitude_m": None,
    "heartbeat_recent_s": 3.0,
    "position_recent_s": 3.0,
    "gps_recent_s": 4.0,
    "require_position": False,
    "require_gps": False,
    "min_gps_fix_type": 3,
    "min_gps_satellites": 6,
    "require_ekf_status": False,
    "ekf_required_flags": 51,
    "require_battery": False,
    "min_battery_voltage_v": None,
}

DEFAULT_NAVIGATION = {
    "search_speed_source": "qgc_mission",
    "search_speed_m_s": None,
}


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
    if camera_source in {"picamera2", "rpicam_mjpeg"}:
        if int(config["camera"].get("camera_index", 0)) < 0:
            raise ValueError(f"camera.camera_index must be zero or positive for {camera_source}")
        for key in ("width", "height"):
            if int(config["camera"].get(key, 1)) <= 0:
                raise ValueError(f"camera.{key} must be positive for {camera_source}")
        if float(config["camera"].get("framerate", 1.0)) <= 0:
            raise ValueError(f"camera.framerate must be positive for {camera_source}")
    if camera_source == "picamera2":
        fallback_source = config["camera"].get("fallback_source")
        if fallback_source not in {None, "rpicam_mjpeg"}:
            raise ValueError("camera.fallback_source must be null or rpicam_mjpeg for picamera2")
        if int(config["camera"].get("buffer_count", 4)) < 2:
            raise ValueError("camera.buffer_count must be at least 2 for picamera2")
        if str(config["camera"].get("array_color_order", "BGR")).upper() not in {"BGR", "RGB"}:
            raise ValueError("camera.array_color_order must be BGR or RGB")
        real_target_mission = (
            str(config["mavlink"].get("connection", "")).startswith("/dev/")
            and bool(config["mission"].get("search_enabled", False))
            and str(config["mission"].get("name", ""))
            == "mission2_target_payload"
        )
        if real_target_mission:
            if str(config["camera"].get("autofocus_mode", "")).lower() != "manual":
                raise ValueError(
                    "real Mission 2 requires camera.autofocus_mode=manual"
                )
            if config["camera"].get("lens_position") is None:
                raise ValueError(
                    "real Mission 2 requires a calibrated camera.lens_position"
                )
            if not bool(
                config["camera"].get("lock_auto_controls_after_warmup", False)
            ):
                raise ValueError(
                    "real Mission 2 requires camera.lock_auto_controls_after_warmup=true"
                )
            if int(config["camera"].get("max_exposure_time_us", 0)) <= 0:
                raise ValueError(
                    "real Mission 2 requires a positive camera.max_exposure_time_us"
                )
    if camera_source == "rpicam_mjpeg" or (
        camera_source == "picamera2" and config["camera"].get("fallback_source") == "rpicam_mjpeg"
    ):
        if int(config["camera"].get("quality", 1)) <= 0:
            raise ValueError("camera.quality must be positive for rpicam_mjpeg")
        if float(config["camera"].get("read_timeout_s", 2.0)) <= 0:
            raise ValueError("camera.read_timeout_s must be positive for rpicam_mjpeg")
    if float(config["mission"].get("max_flight_time_s", 600.0)) <= 0:
        raise ValueError("mission.max_flight_time_s must be positive")
    if not str(config["mission"].get("name", "")).strip():
        raise ValueError("mission.name is required")
    if (
        not bool(config["mission"].get("controller_enabled", True))
        and bool(config["mission"].get("search_enabled", False))
    ):
        raise ValueError(
            "mission.search_enabled must be false when "
            "mission.controller_enabled is false"
        )
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
    for key in (
        "confirmation_min_hit_ratio",
        "confirmation_max_missing_ratio",
    ):
        value = float(config["vision"].get(key, 0.0 if "min" in key else 1.0))
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"vision.{key} must be between 0 and 1")
    for key in (
        "confirmation_window_s",
        "confirmation_max_center_std_px",
        "confirmation_max_area_cv",
        "confirmation_max_bbox_cv",
        "confirmation_max_area_jump_ratio",
    ):
        value = config["vision"].get(key)
        if value is not None and float(value) <= 0:
            raise ValueError(f"vision.{key} must be positive")
    if float(config["vision"].get("confirmation_min_duration_s", 0.0)) < 0:
        raise ValueError("vision.confirmation_min_duration_s must be zero or positive")
    if float(config["vision"].get("confirmation_max_area_jump_ratio", 4.0)) < 1.0:
        raise ValueError("vision.confirmation_max_area_jump_ratio must be at least 1")
    strong_verify_interval_s = float(
        config["vision"].get("strong_verify_interval_s", 0.5)
    )
    if not 0.3 <= strong_verify_interval_s <= 0.5:
        raise ValueError(
            "vision.strong_verify_interval_s must be between 0.3 and 0.5 seconds"
        )
    strong_geometry_age_s = float(
        config["vision"].get("max_strong_geometry_age_s", 0.7)
    )
    if not strong_verify_interval_s <= strong_geometry_age_s <= 0.75:
        raise ValueError(
            "vision.max_strong_geometry_age_s must be at least the verification "
            "interval and no more than 0.75 seconds"
        )
    if int(config["vision"].get("max_failed_geometry_checks", 2)) < 1:
        raise ValueError("vision.max_failed_geometry_checks must be at least 1")
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
    if float(config["control"].get("reacquire_interval_s", 0.5)) <= 0:
        raise ValueError("control.reacquire_interval_s must be positive")
    for key in ("desired_drop_x_normalized", "desired_drop_y_normalized"):
        value = config["control"].get(key)
        if value is not None and not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"control.{key} must be between 0 and 1")
    pixel_x = config["control"].get("desired_drop_pixel_x")
    pixel_y = config["control"].get("desired_drop_pixel_y")
    if (pixel_x is None) != (pixel_y is None):
        raise ValueError("control desired drop pixel x/y must both be set or both be null")
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
    for key in (
        "center_warning_time_s",
        "max_guided_displacement_m",
        "center_progress_window_s",
        "max_lost_detection_s",
        "max_single_direction_time_s",
        "max_guided_entry_time_s",
        "payload_authorization_timeout_s",
        "guidance_health_grace_s",
        "max_input_frame_age_s",
        "max_vision_result_age_s",
        "max_strong_geometry_age_s",
        "max_payload_geometry_age_s",
        "center_variance_window_s",
        "attitude_recent_s",
    ):
        value = safety.get(key)
        if value is not None and float(value) <= 0:
            raise ValueError(f"safety.{key} must be positive or null")
    if float(safety.get("single_direction_deadband_m_s", 0.0)) < 0:
        raise ValueError("safety.single_direction_deadband_m_s must be zero or positive")
    if float(safety.get("center_min_progress_px", 0.0)) < 0:
        raise ValueError("safety.center_min_progress_px must be zero or positive")
    alpha = float(safety.get("center_filter_alpha", 0.35))
    if not 0.0 < alpha <= 1.0:
        raise ValueError("safety.center_filter_alpha must be in (0, 1]")
    accel_limit = safety.get("max_command_accel_m_s2")
    if accel_limit is not None and float(accel_limit) <= 0:
        raise ValueError("safety.max_command_accel_m_s2 must be positive or null")
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
    for key in ("heartbeat_recent_s", "position_recent_s", "gps_recent_s"):
        if float(safety.get(key, 1.0)) <= 0:
            raise ValueError(f"safety.{key} must be positive")
    if int(safety.get("min_gps_fix_type", 3)) < 0:
        raise ValueError("safety.min_gps_fix_type must be zero or positive")
    if int(safety.get("min_gps_satellites", 0)) < 0:
        raise ValueError("safety.min_gps_satellites must be zero or positive")
    battery_min = safety.get("min_battery_voltage_v")
    if battery_min is not None and float(battery_min) <= 0:
        raise ValueError("safety.min_battery_voltage_v must be positive or null")
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
    if bool(config["display"].get("mjpeg_stream_enabled", False)):
        if not 1 <= int(config["display"].get("mjpeg_stream_port", 5602)) <= 65535:
            raise ValueError("display.mjpeg_stream_port must be between 1 and 65535")
        if float(config["display"].get("mjpeg_stream_fps", 10.0)) <= 0:
            raise ValueError("display.mjpeg_stream_fps must be positive")
        quality = int(config["display"].get("mjpeg_stream_quality", 65))
        if not 20 <= quality <= 95:
            raise ValueError("display.mjpeg_stream_quality must be between 20 and 95")
        if int(config["display"].get("mjpeg_stream_width", 960)) <= 0:
            raise ValueError("display.mjpeg_stream_width must be positive")
    required_ardupilot_parameters(config)
    payload = config["payload"]
    mechanism = payload.get("mechanism")
    if mechanism not in {None, "separate_servos", "selector_servo"}:
        raise ValueError(
            "payload.mechanism must be separate_servos, selector_servo, or omitted "
            "for legacy simulation"
        )

    def validate_output(output: dict[str, int], name: str) -> None:
        if output["servo_channel"] <= 0:
            raise ValueError(f"{name} servo_channel must be positive")
        for key in ("release_pwm", "reset_pwm"):
            if not 800 <= output[key] <= 2200:
                raise ValueError(f"{name} {key} must be between 800 and 2200")

    for target in TARGET_PAYLOAD_COLOUR:
        validate_output(
            payload_output_for_target(payload, target),
            f"payload output for {target}",
        )

    if not bool(payload.get("simulate_only", True)):
        if mechanism not in {"separate_servos", "selector_servo"}:
            raise ValueError(
                "physical payload requires an explicit separate_servos or "
                "selector_servo mechanism"
            )
        state_cfg = config.get("payload_state", {})
        if not bool(state_cfg.get("enabled", False)) or not str(state_cfg.get("path", "")).strip():
            raise ValueError("physical payload requires enabled payload_state with a path")
    for key in ("release_hold_s", "total_action_time_s"):
        if float(payload.get(key, 0.0)) <= 0:
            raise ValueError(f"payload.{key} must be positive")
    if float(payload.get("command_ack_timeout_s", 2.0)) <= 0:
        raise ValueError("payload.command_ack_timeout_s must be positive")

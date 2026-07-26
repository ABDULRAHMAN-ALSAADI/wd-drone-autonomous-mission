#!/usr/bin/env python3
"""Props-off bench test: strict OpenCV centering triggers one servo action."""
from __future__ import annotations

import argparse
import math
import time
from collections import deque
from pathlib import Path
from typing import Optional

import cv2
from pymavlink import mavutil

from mavlink_bench import (
    connect,
    heartbeat_is_target_vehicle,
    wait_ack,
    wait_vehicle_state,
)
from opencv_common import (
    LatestCameraReader,
    LatestJpegServer,
    draw_panel,
    load_profile,
    resize_width,
)
from opencv_live_test import desired_point, make_tracker, scale_detection
from payload import payload_output_for_target
from vision import Detection, create_detector


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "real_mission/parameter_config/mission2_target_payload.json"
)
SERVO_COMMAND = mavutil.mavlink.MAV_CMD_DO_SET_SERVO
ACCEPTED = "MAV_RESULT_ACCEPTED"


def servo_settings_from_config(
    config: dict,
    target: str,
) -> dict[str, float | int]:
    """Resolve target-specific servo behavior from the mission profile."""
    payload = config["payload"]
    output = payload_output_for_target(payload, target)
    return {
        "servo_channel": output["servo_channel"],
        "release_pwm": output["release_pwm"],
        "reset_pwm": output["reset_pwm"],
        "release_hold_s": float(payload.get("release_hold_s", 1.0)),
        "ack_timeout_s": float(payload.get("command_ack_timeout_s", 2.0)),
    }


def resolve_servo_settings(
    args: argparse.Namespace,
    config: dict,
) -> dict[str, float | int]:
    """Use profile values unless an expert explicitly supplies an override."""
    settings = servo_settings_from_config(config, args.target)
    for key in (
        "servo_channel",
        "release_pwm",
        "reset_pwm",
        "release_hold_s",
        "ack_timeout_s",
    ):
        override = getattr(args, key)
        if override is not None:
            settings[key] = override
    return settings


def send_servo(master, channel: int, pwm: int, timeout_s: float) -> bool:
    print(f"[SERVO COMMAND] channel={channel} pwm={pwm}")
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        SERVO_COMMAND,
        0,
        float(channel),
        float(pwm),
        0,
        0,
        0,
        0,
        0,
    )
    result = wait_ack(master, SERVO_COMMAND, timeout_s=timeout_s)
    print(f"[SERVO ACK] {result or 'timeout'}")
    return result == ACCEPTED


def read_latest_vehicle_heartbeat(master) -> Optional[tuple[str, bool]]:
    latest = None
    while True:
        message = master.recv_match(type="HEARTBEAT", blocking=False)
        if message is None:
            return latest
        if not heartbeat_is_target_vehicle(message, master.target_system):
            continue
        latest = (
            mavutil.mode_string_v10(message),
            bool(
                message.base_mode
                & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
            ),
        )


def select_detection(
    detections: list[Detection],
    target: str,
) -> Optional[Detection]:
    matching = [item for item in detections if item.target == target]
    return (
        max(matching, key=lambda item: item.total_score)
        if matching
        else None
    )


def full_resolution_center_verified(
    detector,
    frame,
    target: str,
    process_detection: Detection,
    desired: tuple[int, int],
    tolerance_px: float,
    process_shape: tuple[int, ...],
    frame_id: int,
    captured_at_s: float,
    received_at_s: float,
    association_fraction: float,
) -> tuple[bool, Optional[Detection], str]:
    expected = (
        process_detection.center_x / max(1.0, float(process_shape[1])),
        process_detection.center_y / max(1.0, float(process_shape[0])),
    )
    detection = detector.verify_target_frame(
        frame,
        target,
        expected_center_normalized=expected,
        max_center_distance_fraction=association_fraction,
        frame_id=frame_id,
        captured_at_s=captured_at_s,
        received_at_s=received_at_s,
    )
    if detection is None:
        reason = str(
            detector.last_payload_verification.get(
                "reason",
                "strict full-resolution geometry failed",
            )
        )
        return False, None, reason

    scale = frame.shape[1] / max(1.0, float(process_shape[1]))
    full_desired = (desired[0] * scale, desired[1] * scale)
    error_px = math.hypot(
        detection.center_x - full_desired[0],
        detection.center_y - full_desired[1],
    )
    full_tolerance_px = tolerance_px * scale
    if error_px > full_tolerance_px:
        return (
            False,
            detection,
            (
                f"full-resolution center error {error_px:.1f}px exceeds "
                f"{full_tolerance_px:.1f}px"
            ),
        )
    return True, detection, f"strict full-resolution error={error_px:.1f}px"


def draw_bench_overlay(
    frame,
    detection: Optional[Detection],
    desired: tuple[int, int],
    source_shape: tuple[int, ...],
    state: str,
    target: str,
    error_px: Optional[float],
    tolerance_px: float,
    centered_for_s: float,
    center_hold_s: float,
    mode: str,
    heartbeat_age_s: float,
    rejection: str,
    fps: float,
):
    output = frame.copy()
    cv2.drawMarker(
        output,
        desired,
        (255, 255, 255),
        cv2.MARKER_CROSS,
        26,
        1,
    )
    if detection is not None:
        shown = scale_detection(detection, source_shape, output.shape)
        colour = (0, 255, 0)
        cv2.rectangle(
            output,
            (shown.bbox_x, shown.bbox_y),
            (
                shown.bbox_x + shown.bbox_w,
                shown.bbox_y + shown.bbox_h,
            ),
            colour,
            2,
        )
        cv2.circle(
            output,
            (shown.center_x, shown.center_y),
            5,
            colour,
            -1,
        )
        cv2.line(
            output,
            desired,
            (shown.center_x, shown.center_y),
            colour,
            2,
            cv2.LINE_AA,
        )

    error_text = "none" if error_px is None else f"{error_px:.1f}px"
    return draw_panel(
        output,
        [
            "OPENCV + SERVO BENCH | NO MODE / MOTOR / VELOCITY COMMANDS",
            f"State {state} | target {target}",
            (
                f"Strict center error {error_text} | tolerance "
                f"{tolerance_px:.1f}px"
            ),
            (
                f"Centered {centered_for_s:.1f}/{center_hold_s:.1f}s "
                "| one release maximum"
            ),
            (
                f"Pixhawk mode {mode} | DISARMED required | "
                f"heartbeat age {heartbeat_age_s:.1f}s"
            ),
            f"Vision FPS {fps:.1f} | last rejection {rejection}",
        ],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--target",
        choices=("red_triangle", "blue_hexagon"),
        required=True,
    )
    parser.add_argument(
        "--connection",
        help="Expert override; normally read from the mission profile.",
    )
    parser.add_argument(
        "--baud",
        type=int,
        help="Expert override; normally read from the mission profile.",
    )
    parser.add_argument(
        "--servo-channel",
        type=int,
        help="Expert override; normally read from the mission profile.",
    )
    parser.add_argument(
        "--release-pwm",
        type=int,
        help="Expert override; normally selected by target from the profile.",
    )
    parser.add_argument(
        "--reset-pwm",
        type=int,
        help="Expert override; normally the profile's neutral PWM.",
    )
    parser.add_argument(
        "--release-hold-s",
        type=float,
        help="Expert override; normally read from the mission profile.",
    )
    parser.add_argument(
        "--ack-timeout-s",
        type=float,
        help="Expert override; normally read from the mission profile.",
    )
    parser.add_argument("--heartbeat-max-age-s", type=float, default=3.0)
    parser.add_argument("--duration-s", type=float, default=180.0)
    parser.add_argument("--stream-bind", default="127.0.0.1")
    parser.add_argument("--stream-port", type=int, default=5602)
    parser.add_argument("--i-understand-props-off", action="store_true")
    parser.add_argument("--i-accept-servo-motion", action="store_true")
    parser.add_argument("--i-confirm-payload-zone-clear", action="store_true")
    return parser.parse_args()


def validate_args(
    args: argparse.Namespace,
    servo_settings: dict[str, float | int],
) -> None:
    required_flags = (
        args.i_understand_props_off
        and args.i_accept_servo_motion
        and args.i_confirm_payload_zone_clear
    )
    if not required_flags:
        raise SystemExit(
            "Refusing physical servo test. Remove every propeller, clear the "
            "payload drop zone, and pass all three acknowledgement flags."
        )
    if not 1 <= int(servo_settings["servo_channel"]) <= 16:
        raise ValueError("servo channel must be between 1 and 16")
    for name in ("release_pwm", "reset_pwm"):
        value = int(servo_settings[name])
        if not 800 <= value <= 2200:
            raise ValueError(f"{name} must be between 800 and 2200")
    if not 0.0 < float(servo_settings["release_hold_s"]) <= 10.0:
        raise ValueError("release-hold-s must be in (0, 10]")
    if float(servo_settings["ack_timeout_s"]) <= 0:
        raise ValueError("ack-timeout-s must be positive")
    if args.heartbeat_max_age_s <= 0 or args.duration_s <= 0:
        raise ValueError("heartbeat-max-age-s and duration-s must be positive")


def main() -> int:
    args = parse_args()
    config = load_profile(args.config)
    servo_settings = resolve_servo_settings(args, config)
    validate_args(args, servo_settings)
    connection = str(
        args.connection or config["mavlink"]["connection"]
    )
    baud = int(
        args.baud
        if args.baud is not None
        else config["mavlink"]["baud"]
    )
    vision = config["vision"]
    control = config["control"]
    detector = create_detector(vision)
    tracker = make_tracker(vision)
    reader = LatestCameraReader.from_config(config["camera"])
    stream = LatestJpegServer(
        bind=args.stream_bind,
        port=args.stream_port,
        fps=float(config["display"].get("mjpeg_stream_fps", 10.0)),
        quality=int(config["display"].get("mjpeg_stream_quality", 65)),
        width=int(config["display"].get("mjpeg_stream_width", 960)),
    )
    master = connect(connection, baud, timeout_s=15.0)
    mode, armed = wait_vehicle_state(master, 3.0)
    if armed:
        raise SystemExit("Refusing test because the vehicle is armed")

    per_target = control.get("center_tolerance_px_by_target", {})
    tolerance_px = float(
        per_target.get(
            args.target,
            control.get("center_tolerance_px", 22.0),
        )
    )
    center_hold_s = float(control.get("center_hold_s", 1.2))
    association_fraction = float(
        vision.get("payload_association_max_fraction", 0.18)
    )

    print("=" * 72)
    print("STRICT OPENCV -> PHYSICAL SERVO BENCH TEST")
    print("PROPELLERS OFF. VEHICLE MUST REMAIN DISARMED.")
    print(
        f"target={args.target} "
        f"channel={int(servo_settings['servo_channel'])} "
        f"release={int(servo_settings['release_pwm'])} "
        f"hold={float(servo_settings['release_hold_s']):.1f}s "
        f"reset={int(servo_settings['reset_pwm'])}"
    )
    print(f"MAVLink {connection} at {baud} baud")
    print("No mode, arm, motor, or velocity command exists in this test.")
    print("=" * 72)

    reader.start()
    stream.start()
    print(
        f"[STREAM] http://{args.stream_bind}:{args.stream_port}/stream.mjpg"
    )

    started_at = time.monotonic()
    last_vehicle_heartbeat_at = started_at
    last_frame_id = 0
    centered_since: Optional[float] = None
    released_at: Optional[float] = None
    state = "SEARCH_STRICT_GEOMETRY"
    process_times: deque[float] = deque(maxlen=120)

    try:
        while time.monotonic() - started_at < args.duration_s:
            now = time.monotonic()
            heartbeat = read_latest_vehicle_heartbeat(master)
            if heartbeat is not None:
                mode, armed = heartbeat
                last_vehicle_heartbeat_at = now
            if armed:
                raise RuntimeError(
                    "Vehicle became armed; servo bench test stopped"
                )

            camera_frame = reader.latest()
            if (
                camera_frame is None
                or camera_frame.frame_id == last_frame_id
            ):
                if reader.failure:
                    raise RuntimeError(
                        f"camera worker failed: {reader.failure}"
                    )
                time.sleep(0.002)
                continue
            last_frame_id = camera_frame.frame_id
            raw = camera_frame.image_bgr
            process_width = int(
                vision.get("process_width", raw.shape[1])
            )
            process_image = resize_width(raw, process_width)
            captured_at_s = (
                float(camera_frame.sensor_timestamp_ns) / 1_000_000_000.0
                if camera_frame.has_sensor_timestamp
                else camera_frame.received_monotonic_s
            )
            processed = detector.preprocess(
                process_image,
                frame_id=camera_frame.frame_id,
                captured_at_s=captured_at_s,
                received_at_s=camera_frame.received_monotonic_s,
            )
            detections = detector.search_processed(
                processed,
                {args.target},
            )
            process_times.append(now)
            confirmed = tracker.update(
                detections,
                {args.target},
                now=now,
                frame_id=camera_frame.frame_id,
            )
            detection = select_detection(detections, args.target)
            desired = desired_point(
                control,
                process_image.shape[1],
                process_image.shape[0],
            )
            error_px = (
                None
                if detection is None
                else math.hypot(
                    detection.center_x - desired[0],
                    detection.center_y - desired[1],
                )
            )

            heartbeat_age_s = now - last_vehicle_heartbeat_at
            strict_confirmed = (
                confirmed is not None
                and detection is not None
                and detection.source == "geometry_search"
                and detection.status == "valid_shape"
            )
            centered = (
                strict_confirmed
                and error_px is not None
                and error_px <= tolerance_px
            )
            if released_at is not None:
                state = "RELEASE_COMPLETE"
            elif heartbeat_age_s > args.heartbeat_max_age_s:
                centered_since = None
                state = "BLOCKED_STALE_HEARTBEAT"
            elif not strict_confirmed:
                centered_since = None
                state = "SEARCH_STRICT_GEOMETRY"
            elif not centered:
                centered_since = None
                state = "STRICT_TARGET_NOT_CENTERED"
            else:
                if centered_since is None:
                    centered_since = now
                state = "CENTER_HOLD"

            centered_for_s = (
                0.0
                if centered_since is None
                else now - centered_since
            )
            ready = (
                released_at is None
                and centered
                and centered_for_s >= center_hold_s
                and heartbeat_age_s <= args.heartbeat_max_age_s
            )
            if ready:
                verified, _full_detection, reason = (
                    full_resolution_center_verified(
                        detector,
                        raw,
                        args.target,
                        detection,
                        desired,
                        tolerance_px,
                        process_image.shape,
                        camera_frame.frame_id,
                        captured_at_s,
                        camera_frame.received_monotonic_s,
                        association_fraction,
                    )
                )
                if not verified:
                    centered_since = None
                    state = f"FULL_RES_REJECTED: {reason}"
                    print(f"[PAYLOAD BLOCKED] {reason}")
                else:
                    mode, armed = wait_vehicle_state(master, 2.0)
                    last_vehicle_heartbeat_at = time.monotonic()
                    if armed:
                        raise RuntimeError(
                            "Vehicle armed before servo command; blocked"
                        )
                    print(f"[FULL RES VERIFIED] {reason}")
                    if not send_servo(
                        master,
                        int(servo_settings["servo_channel"]),
                        int(servo_settings["release_pwm"]),
                        float(servo_settings["ack_timeout_s"]),
                    ):
                        raise RuntimeError(
                            "Release servo command was not accepted"
                        )
                    time.sleep(float(servo_settings["release_hold_s"]))
                    mode, armed = wait_vehicle_state(master, 2.0)
                    last_vehicle_heartbeat_at = time.monotonic()
                    if armed:
                        raise RuntimeError(
                            "Vehicle armed before servo reset; test stopped"
                        )
                    if not send_servo(
                        master,
                        int(servo_settings["servo_channel"]),
                        int(servo_settings["reset_pwm"]),
                        float(servo_settings["ack_timeout_s"]),
                    ):
                        raise RuntimeError(
                            "Servo reset command was not accepted"
                        )
                    released_at = time.monotonic()
                    state = "RELEASE_COMPLETE"
                    print(
                        "[BENCH RELEASE COMPLETE] command accepted and reset "
                        "accepted; physical payload release is not sensed"
                    )

            fps = (
                0.0
                if len(process_times) < 2
                else (len(process_times) - 1)
                / max(
                    1e-6,
                    process_times[-1] - process_times[0],
                )
            )
            rejection = (
                detector.last_rejections[-1]["reason"]
                if detector.last_rejections
                else "none"
            )
            view = draw_bench_overlay(
                raw,
                detection,
                desired_point(
                    control,
                    raw.shape[1],
                    raw.shape[0],
                ),
                process_image.shape,
                state,
                args.target,
                error_px,
                tolerance_px,
                centered_for_s,
                center_hold_s,
                mode,
                heartbeat_age_s,
                rejection,
                fps,
            )
            stream.publish(view)
            if released_at is not None and now - released_at >= 3.0:
                return 0
    finally:
        reader.stop()
        stream.stop()

    print("[TIMEOUT] No payload command was sent")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

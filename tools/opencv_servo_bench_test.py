#!/usr/bin/env python3
"""Props-off bench test: strict OpenCV triggers each target's servo action once."""
from __future__ import annotations

import argparse
import math
import time
from collections import deque
from dataclasses import dataclass
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
MISSION_TARGETS = frozenset({"red_triangle", "blue_hexagon"})
SERVO_IDLE = "IDLE"
SERVO_WAIT_RELEASE_ACK = "WAIT_RELEASE_ACK"
SERVO_HOLD = "HOLD"
SERVO_WAIT_RESET_ACK = "WAIT_RESET_ACK"
SERVO_FAULT = "FAULT"
FAULT_NEUTRAL_RETRY_S = 1.0


@dataclass
class ServoSequence:
    """Non-blocking release/hold/reset state for one target at a time."""

    phase: str = SERVO_IDLE
    target: Optional[str] = None
    settings: Optional[dict[str, float | int]] = None
    command_sent_at: float = 0.0
    hold_until: float = 0.0
    fault_reason: Optional[str] = None
    fault_neutral_confirmed: bool = False
    fault_neutral_last_attempt_at: float = 0.0
    fault_neutral_attempts: int = 0

    def begin_release(
        self,
        target: str,
        settings: dict[str, float | int],
        now: float,
    ) -> None:
        if self.phase != SERVO_IDLE:
            raise RuntimeError("Cannot start a release while another is active")
        self.phase = SERVO_WAIT_RELEASE_ACK
        self.target = target
        self.settings = settings
        self.command_sent_at = now
        self.hold_until = now + float(settings["release_hold_s"])
        self.fault_reason = None
        self.fault_neutral_confirmed = False
        self.fault_neutral_last_attempt_at = 0.0
        self.fault_neutral_attempts = 0

    def handle_ack(self, result: str, now: float) -> Optional[str]:
        if self.phase == SERVO_FAULT:
            if (
                self.fault_neutral_attempts > 0
                and result == ACCEPTED
            ):
                self.fault_neutral_confirmed = True
            return None
        if self.phase not in {
            SERVO_WAIT_RELEASE_ACK,
            SERVO_WAIT_RESET_ACK,
        }:
            return None
        if result == "MAV_RESULT_IN_PROGRESS":
            return None
        if result != ACCEPTED:
            self.fail(
                f"Servo command was rejected during {self.phase}: {result}"
            )
            return None
        if self.phase == SERVO_WAIT_RELEASE_ACK:
            self.phase = SERVO_HOLD
            return None
        completed_target = self.target
        self.phase = SERVO_IDLE
        self.target = None
        self.settings = None
        self.command_sent_at = 0.0
        self.hold_until = 0.0
        self.fault_reason = None
        self.fault_neutral_confirmed = False
        self.fault_neutral_last_attempt_at = 0.0
        self.fault_neutral_attempts = 0
        return completed_target

    def begin_reset(self, now: float) -> None:
        if self.phase != SERVO_HOLD:
            raise RuntimeError("Cannot reset before the release hold")
        self.phase = SERVO_WAIT_RESET_ACK
        self.command_sent_at = now

    def check_ack_timeout(self, now: float) -> Optional[str]:
        if self.phase not in {
            SERVO_WAIT_RELEASE_ACK,
            SERVO_WAIT_RESET_ACK,
        }:
            return None
        assert self.settings is not None
        timeout_s = float(self.settings["ack_timeout_s"])
        if now - self.command_sent_at > timeout_s:
            reason = (
                f"Servo ACK timeout during {self.phase} "
                f"after {timeout_s:.1f}s"
            )
            self.fail(reason)
            return reason
        return None

    def fail(self, reason: str) -> None:
        self.phase = SERVO_FAULT
        self.fault_reason = reason
        self.command_sent_at = 0.0
        self.hold_until = 0.0
        self.fault_neutral_confirmed = False
        self.fault_neutral_last_attempt_at = 0.0
        self.fault_neutral_attempts = 0

    def fault_neutral_due(
        self,
        now: float,
        retry_s: float = FAULT_NEUTRAL_RETRY_S,
    ) -> bool:
        if self.phase != SERVO_FAULT:
            return False
        if self.fault_neutral_confirmed:
            return False
        return (
            self.fault_neutral_attempts == 0
            or now - self.fault_neutral_last_attempt_at >= retry_s
        )

    def note_fault_neutral_attempt(self, now: float) -> None:
        if self.phase != SERVO_FAULT:
            raise RuntimeError("Cannot record fault neutral outside a fault")
        self.fault_neutral_last_attempt_at = now
        self.fault_neutral_attempts += 1

    def hold_remaining_s(self, now: float) -> float:
        if self.phase != SERVO_HOLD:
            return 0.0
        return max(0.0, self.hold_until - now)


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
    target: str,
) -> dict[str, float | int]:
    """Use profile values unless an expert explicitly supplies an override."""
    settings = servo_settings_from_config(config, target)
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


def targets_for_run(target_option: str) -> set[str]:
    """Return the targets that may trigger once during this bench run."""
    if target_option == "both":
        return set(MISSION_TARGETS)
    if target_option in MISSION_TARGETS:
        return {target_option}
    raise ValueError(f"Unknown target option: {target_option}")


def neutral_commands_from_settings(
    settings_by_target: dict[str, dict[str, float | int]],
) -> list[tuple[int, int, float]]:
    """Return one unambiguous neutral command for each configured channel."""
    by_channel: dict[int, tuple[int, float]] = {}
    for target, settings in settings_by_target.items():
        channel = int(settings["servo_channel"])
        reset_pwm = int(settings["reset_pwm"])
        ack_timeout_s = float(settings["ack_timeout_s"])
        previous = by_channel.get(channel)
        if previous is not None and previous[0] != reset_pwm:
            raise ValueError(
                f"Conflicting neutral PWM values on channel {channel}: "
                f"{previous[0]} and {reset_pwm} ({target})"
            )
        by_channel[channel] = (reset_pwm, ack_timeout_s)
    return [
        (channel, reset_pwm, ack_timeout_s)
        for channel, (reset_pwm, ack_timeout_s) in sorted(by_channel.items())
    ]


def issue_servo_command(master, channel: int, pwm: int) -> None:
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


def send_servo(master, channel: int, pwm: int, timeout_s: float) -> bool:
    """Blocking command used only before streaming and during final cleanup."""
    issue_servo_command(master, channel, pwm)
    result = wait_ack(master, SERVO_COMMAND, timeout_s=timeout_s)
    print(f"[SERVO ACK] {result or 'timeout'}")
    return result == ACCEPTED


def mav_result_name(result_code: int) -> str:
    result = mavutil.mavlink.enums["MAV_RESULT"].get(int(result_code))
    return result.name if result else str(result_code)


def read_vehicle_updates(
    master,
) -> tuple[Optional[tuple[str, bool]], list[str]]:
    """Drain MAVLink once so heartbeat and servo ACK handling never blocks video."""
    latest_heartbeat = None
    servo_acks: list[str] = []
    for _ in range(200):
        message = master.recv_match(blocking=False)
        if message is None:
            return latest_heartbeat, servo_acks
        message_type = message.get_type()
        if message_type == "HEARTBEAT":
            if not heartbeat_is_target_vehicle(
                message,
                master.target_system,
            ):
                continue
            latest_heartbeat = (
                mavutil.mode_string_v10(message),
                bool(
                    message.base_mode
                    & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
                ),
            )
            continue
        if (
            message_type == "COMMAND_ACK"
            and message.get_srcSystem() == master.target_system
            and message.get_srcComponent() == master.target_component
            and int(message.command) == int(SERVO_COMMAND)
        ):
            target_system = int(getattr(message, "target_system", 0))
            target_component = int(
                getattr(message, "target_component", 0)
            )
            if target_system not in {0, int(master.source_system)}:
                continue
            if target_component not in {
                0,
                int(master.source_component),
            }:
                continue
            servo_acks.append(mav_result_name(int(message.result)))
    return latest_heartbeat, servo_acks


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
    completed_targets: set[str],
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
                "| one release per target"
            ),
            f"Completed {', '.join(sorted(completed_targets)) or 'none'}",
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
        choices=("both", "red_triangle", "blue_hexagon"),
        default="both",
        help="Defaults to automatic recognition of both targets in any order.",
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
    parser.add_argument(
        "--duration-s",
        type=float,
        default=0.0,
        help="Optional timeout; zero keeps monitoring until Ctrl+C.",
    )
    parser.add_argument("--stream-bind", default="127.0.0.1")
    parser.add_argument("--stream-port", type=int, default=5602)
    parser.add_argument("--i-understand-props-off", action="store_true")
    parser.add_argument("--i-accept-servo-motion", action="store_true")
    parser.add_argument("--i-confirm-payload-zone-clear", action="store_true")
    return parser.parse_args()


def validate_args(
    args: argparse.Namespace,
    settings_by_target: dict[str, dict[str, float | int]],
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
    for target, servo_settings in settings_by_target.items():
        if not 1 <= int(servo_settings["servo_channel"]) <= 16:
            raise ValueError(
                f"{target} servo channel must be between 1 and 16"
            )
        for name in ("release_pwm", "reset_pwm"):
            value = int(servo_settings[name])
            if not 800 <= value <= 2200:
                raise ValueError(
                    f"{target} {name} must be between 800 and 2200"
                )
        if not 0.0 < float(servo_settings["release_hold_s"]) <= 10.0:
            raise ValueError(
                f"{target} release-hold-s must be in (0, 10]"
            )
        if float(servo_settings["ack_timeout_s"]) <= 0:
            raise ValueError(f"{target} ack-timeout-s must be positive")
    if args.heartbeat_max_age_s <= 0:
        raise ValueError("heartbeat-max-age-s must be positive")
    if args.duration_s < 0:
        raise ValueError("duration-s must be zero or positive")


def main() -> int:
    args = parse_args()
    config = load_profile(args.config)
    requested_targets = targets_for_run(args.target)
    settings_by_target = {
        target: resolve_servo_settings(args, config, target)
        for target in requested_targets
    }
    validate_args(args, settings_by_target)
    neutral_commands = neutral_commands_from_settings(settings_by_target)
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
    print("[SERVO INITIALIZE] Commanding configured neutral before vision starts")
    for channel, neutral_pwm, ack_timeout_s in neutral_commands:
        if not send_servo(
            master,
            channel,
            neutral_pwm,
            ack_timeout_s,
        ):
            raise RuntimeError(
                f"Neutral initialization was not accepted on channel {channel}"
            )

    per_target = control.get("center_tolerance_px_by_target", {})
    center_hold_s = float(control.get("center_hold_s", 1.2))
    association_fraction = float(
        vision.get("payload_association_max_fraction", 0.18)
    )

    print("=" * 72)
    print("STRICT OPENCV -> PHYSICAL SERVO BENCH TEST")
    print("PROPELLERS OFF. VEHICLE MUST REMAIN DISARMED.")
    print("Automatic strict recognition; targets may be presented in any order.")
    for target in sorted(requested_targets):
        servo_settings = settings_by_target[target]
        print(
            f"target={target} "
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
    active_target: Optional[str] = None
    completed_targets: set[str] = set()
    all_completed = False
    servo_sequence = ServoSequence()
    state = "SEARCH_STRICT_GEOMETRY"
    process_times: deque[float] = deque(maxlen=120)

    try:
        while (
            args.duration_s <= 0
            or time.monotonic() - started_at < args.duration_s
        ):
            now = time.monotonic()
            heartbeat, servo_acks = read_vehicle_updates(master)
            if heartbeat is not None:
                mode, armed = heartbeat
                last_vehicle_heartbeat_at = now
            if armed:
                raise RuntimeError(
                    "Vehicle became armed; servo bench test stopped"
                )
            heartbeat_age_s = now - last_vehicle_heartbeat_at

            for result in servo_acks:
                neutral_was_pending = (
                    servo_sequence.phase == SERVO_FAULT
                    and servo_sequence.fault_neutral_attempts > 0
                    and not servo_sequence.fault_neutral_confirmed
                )
                completed_target = servo_sequence.handle_ack(result, now)
                print(
                    f"[SERVO ACK] phase={servo_sequence.phase} "
                    f"result={result}"
                )
                if (
                    neutral_was_pending
                    and servo_sequence.fault_neutral_confirmed
                ):
                    print(
                        "[SERVO FAULT NEUTRAL CONFIRMED] "
                        "Cube accepted neutral PWM"
                    )
                if completed_target is None:
                    continue
                completed_targets.add(completed_target)
                active_target = None
                centered_since = None
                tracker.reset()
                reset_tracking = getattr(
                    detector,
                    "reset_tracking",
                    None,
                )
                if callable(reset_tracking):
                    reset_tracking()
                state = f"RELEASE_COMPLETE_{completed_target}"
                print(
                    f"[BENCH RELEASE COMPLETE] target={completed_target}; "
                    "release and neutral reset accepted; physical payload "
                    "release is not sensed"
                )
                all_completed = completed_targets == requested_targets
                if all_completed:
                    state = "BENCH_COMPLETE_MONITORING"
                    print(
                        "[BENCH TEST COMPLETE] all requested targets released "
                        "once; monitoring continues until Ctrl+C"
                    )
                else:
                    print(
                        "[READY FOR NEXT TARGET] remaining="
                        f"{sorted(requested_targets - completed_targets)}"
                    )

            if (
                servo_sequence.phase
                in {
                    SERVO_WAIT_RELEASE_ACK,
                    SERVO_HOLD,
                    SERVO_WAIT_RESET_ACK,
                }
                and heartbeat_age_s > args.heartbeat_max_age_s
            ):
                servo_sequence.fail(
                    "Vehicle heartbeat became stale during servo action"
                )
            servo_sequence.check_ack_timeout(now)

            if (
                servo_sequence.phase == SERVO_FAULT
            ):
                assert servo_sequence.settings is not None
                if servo_sequence.fault_neutral_attempts == 0:
                    print(
                        f"[SERVO FAULT] {servo_sequence.fault_reason}; "
                        "commanding neutral and keeping the video live"
                    )
                if servo_sequence.fault_neutral_due(now):
                    if servo_sequence.fault_neutral_attempts == 0:
                        fault_heartbeat, stale_acks = read_vehicle_updates(
                            master
                        )
                        if fault_heartbeat is not None:
                            mode, armed = fault_heartbeat
                            last_vehicle_heartbeat_at = now
                        if stale_acks:
                            print(
                                "[STALE SERVO ACK DISCARDED BEFORE NEUTRAL] "
                                f"count={len(stale_acks)}"
                            )
                    attempt = servo_sequence.fault_neutral_attempts + 1
                    try:
                        issue_servo_command(
                            master,
                            int(
                                servo_sequence.settings[
                                    "servo_channel"
                                ]
                            ),
                            int(servo_sequence.settings["reset_pwm"]),
                        )
                        print(
                            "[SERVO FAULT NEUTRAL PENDING] "
                            f"attempt={attempt}"
                        )
                    except Exception as exc:
                        print(
                            "[SERVO FAULT NEUTRAL SEND FAILED] "
                            f"attempt={attempt}: {exc}"
                        )
                    finally:
                        servo_sequence.note_fault_neutral_attempt(now)
                centered_since = None
                neutral_state = (
                    "NEUTRAL_CONFIRMED"
                    if servo_sequence.fault_neutral_confirmed
                    else "NEUTRAL_PENDING"
                )
                state = (
                    f"SERVO_FAULT_{neutral_state}_"
                    f"{servo_sequence.target}"
                )
            elif servo_sequence.phase == SERVO_HOLD:
                remaining_s = servo_sequence.hold_remaining_s(now)
                state = (
                    f"SERVO_HOLD_{servo_sequence.target}_"
                    f"{remaining_s:.1f}s"
                )
                if remaining_s <= 0.0:
                    assert servo_sequence.settings is not None
                    try:
                        issue_servo_command(
                            master,
                            int(
                                servo_sequence.settings[
                                    "servo_channel"
                                ]
                            ),
                            int(servo_sequence.settings["reset_pwm"]),
                        )
                    except Exception as exc:
                        servo_sequence.fail(
                            f"Neutral reset command send failed: {exc}"
                        )
                        state = f"SERVO_FAULT_{servo_sequence.target}"
                    else:
                        servo_sequence.begin_reset(now)
                        state = f"WAIT_RESET_ACK_{servo_sequence.target}"
            elif servo_sequence.phase == SERVO_WAIT_RELEASE_ACK:
                state = f"WAIT_RELEASE_ACK_{servo_sequence.target}"
            elif servo_sequence.phase == SERVO_WAIT_RESET_ACK:
                state = f"WAIT_RESET_ACK_{servo_sequence.target}"

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
            pending_targets = requested_targets - completed_targets
            search_targets = (
                {active_target}
                if active_target is not None
                else pending_targets
            )
            detections = detector.search_processed(processed, search_targets)
            process_times.append(now)
            confirmed = tracker.update(
                detections,
                search_targets,
                now=now,
                frame_id=camera_frame.frame_id,
            )
            if confirmed is not None and active_target is None:
                active_target = confirmed.target
                print(f"[TARGET CONFIRMED] {active_target}")
            display_target = (
                active_target
                or (confirmed.target if confirmed is not None else None)
            )
            detection = (
                select_detection(detections, display_target)
                if display_target is not None
                else (
                    max(detections, key=lambda item: item.total_score)
                    if detections
                    else None
                )
            )
            tolerance_target = (
                active_target
                or (detection.target if detection is not None else "")
            )
            tolerance_px = float(
                per_target.get(
                    tolerance_target,
                    control.get("center_tolerance_px", 22.0),
                )
            )
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
                and active_target is not None
                and detection is not None
                and detection.target == active_target
                and detection.source == "geometry_search"
                and detection.status == "valid_shape"
            )
            centered = (
                strict_confirmed
                and error_px is not None
                and error_px <= tolerance_px
            )
            if all_completed:
                centered_since = None
                state = "BENCH_COMPLETE_MONITORING"
            elif servo_sequence.phase != SERVO_IDLE:
                centered_since = None
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
                not all_completed
                and servo_sequence.phase == SERVO_IDLE
                and centered
                and centered_for_s >= center_hold_s
                and heartbeat_age_s <= args.heartbeat_max_age_s
            )
            if ready:
                assert active_target is not None
                servo_settings = settings_by_target[active_target]
                verified, _full_detection, reason = (
                    full_resolution_center_verified(
                        detector,
                        raw,
                        active_target,
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
                    heartbeat, stale_servo_acks = read_vehicle_updates(master)
                    command_now = time.monotonic()
                    if heartbeat is not None:
                        mode, armed = heartbeat
                        last_vehicle_heartbeat_at = command_now
                    if stale_servo_acks:
                        print(
                            "[STALE SERVO ACK DISCARDED] "
                            f"count={len(stale_servo_acks)}"
                        )
                    if armed:
                        raise RuntimeError(
                            "Vehicle armed before servo command; blocked"
                        )
                    if (
                        command_now - last_vehicle_heartbeat_at
                        > args.heartbeat_max_age_s
                    ):
                        centered_since = None
                        state = "BLOCKED_STALE_HEARTBEAT"
                    else:
                        print(f"[FULL RES VERIFIED] {reason}")
                        servo_sequence.begin_release(
                            active_target,
                            servo_settings,
                            time.monotonic(),
                        )
                        centered_since = None
                        try:
                            issue_servo_command(
                                master,
                                int(servo_settings["servo_channel"]),
                                int(servo_settings["release_pwm"]),
                            )
                        except Exception as exc:
                            servo_sequence.fail(
                                f"Release command send failed: {exc}"
                            )
                            state = f"SERVO_FAULT_{active_target}"
                        else:
                            state = f"WAIT_RELEASE_ACK_{active_target}"

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
                active_target or "automatic",
                error_px,
                tolerance_px,
                centered_for_s,
                center_hold_s,
                mode,
                heartbeat_age_s,
                rejection,
                fps,
                completed_targets,
            )
            stream.publish(view)
    finally:
        print("[FINAL NEUTRAL] Returning every selector to neutral")
        pending_neutral_acks = []
        for channel, neutral_pwm, ack_timeout_s in neutral_commands:
            try:
                issue_servo_command(master, channel, neutral_pwm)
                pending_neutral_acks.append((channel, ack_timeout_s))
            except Exception as exc:
                print(
                    "[FINAL NEUTRAL SEND FAILED] "
                    f"channel={channel}: {exc}"
                )
        for channel, ack_timeout_s in pending_neutral_acks:
            try:
                result = wait_ack(
                    master,
                    SERVO_COMMAND,
                    timeout_s=ack_timeout_s,
                )
                print(
                    f"[FINAL NEUTRAL ACK] channel={channel} "
                    f"{result or 'timeout'}"
                )
            except Exception as exc:
                print(
                    "[FINAL NEUTRAL ACK FAILED] "
                    f"channel={channel}: {exc}"
                )
        reader.stop()
        stream.stop()

    if all_completed:
        print("[COMPLETE] Monitoring duration ended after all target releases")
        return 0
    print(
        "[TIMEOUT] completed="
        f"{sorted(completed_targets)} "
        f"remaining={sorted(requested_targets - completed_targets)}"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

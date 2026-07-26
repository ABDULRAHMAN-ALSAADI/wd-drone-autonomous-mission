"""Payload target mapping and release helpers."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Optional

from pymavlink import mavutil


TARGET_PAYLOAD_COLOUR = {
    "blue_hexagon": "red",
    "red_triangle": "blue",
}


def payload_colour_for_target(target: str) -> str:
    try:
        return TARGET_PAYLOAD_COLOUR[target]
    except KeyError as exc:
        raise ValueError(f"Unknown mission target: {target}") from exc


def payload_output_for_target(
    payload_config: dict[str, Any],
    target: str,
) -> dict[str, int]:
    """Resolve the explicitly configured actuator for the target's payload."""
    payload_colour = payload_colour_for_target(target)
    mechanism = payload_config.get("mechanism")
    if mechanism == "separate_servos":
        output = payload_config.get(payload_colour)
        if not isinstance(output, dict):
            raise ValueError(f"payload.{payload_colour} configuration is missing")
        return {
            "servo_channel": int(output["servo_channel"]),
            "release_pwm": int(output["release_pwm"]),
            "reset_pwm": int(output["reset_pwm"]),
        }
    if mechanism == "selector_servo":
        return {
            "servo_channel": int(payload_config["servo_channel"]),
            "release_pwm": int(payload_config[f"{payload_colour}_payload_pwm"]),
            "reset_pwm": int(payload_config["neutral_pwm"]),
        }
    if mechanism is None:
        # Legacy simulation profiles retain one generic output. Physical
        # operation is rejected by validation unless a mechanism is explicit.
        return {
            "servo_channel": int(payload_config["servo_channel"]),
            "release_pwm": int(payload_config["release_pwm"]),
            "reset_pwm": int(payload_config["reset_pwm"]),
        }
    raise ValueError(f"Unsupported payload mechanism: {mechanism!r}")


def load_payload_state(controller: Any) -> None:
    assert controller.payload_state_path is not None
    if not controller.payload_state_path.exists():
        print(f"[PAYLOAD STATE] empty: {controller.payload_state_path}")
        return
    data = json.loads(controller.payload_state_path.read_text(encoding="utf-8"))
    profile = str(controller.config["mission"].get("name", ""))
    if data.get("mission_profile") != profile:
        raise RuntimeError(
            f"Payload state profile mismatch: file={data.get('mission_profile')} config={profile}. "
            "Reset the state explicitly before this attempt."
        )
    targets = data.get("targets", {})
    controller.completed_targets.update(
        target for target, record in targets.items()
        if bool(record.get("payload_release_attempted", False))
    )
    print(f"[PAYLOAD STATE] loaded completed={sorted(controller.completed_targets)} path={controller.payload_state_path}")


def persist_payload_release(
    controller: Any,
    target: str,
    payload_colour: str,
    accepted: bool,
) -> None:
    if controller.payload_state_path is None:
        return
    output = payload_output_for_target(controller.config["payload"], target)
    data: dict[str, Any] = {
        "mission_profile": controller.config["mission"].get("name"),
        "targets": {},
    }
    if controller.payload_state_path.exists():
        data = json.loads(controller.payload_state_path.read_text(encoding="utf-8"))
    data.setdefault("targets", {})[target] = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "waypoint": controller.vehicle.mission_seq,
        "payload_colour": payload_colour,
        "payload_mechanism": controller.config["payload"].get(
            "mechanism",
            "legacy_simulation",
        ),
        "servo_channel": output["servo_channel"],
        "release_pwm": output["release_pwm"],
        "payload_command_accepted": bool(accepted),
        "payload_release_attempted": True,
        "physical_release_confirmed": False,
    }
    controller.payload_state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = controller.payload_state_path.with_suffix(
        controller.payload_state_path.suffix + ".tmp"
    )
    temporary.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(controller.payload_state_path)


def verify_payload_geometry(
    controller: Any,
    full_resolution_frame: Any,
    process_width: int,
    process_height: int,
    now: float,
    frame_id: int = 0,
    captured_at_s: Optional[float] = None,
    received_at_s: Optional[float] = None,
) -> bool:
    """Require a new strict full-resolution shape result before release."""
    target = controller.current_target
    verify = getattr(controller.detector, "verify_target_frame", None)
    previous_reason = controller.last_payload_geometry_reason
    was_verified = controller.last_payload_geometry_detection is not None
    controller.last_payload_geometry_at = 0.0
    controller.last_payload_geometry_detection = None
    controller.last_payload_geometry_error_px = None

    if target not in TARGET_PAYLOAD_COLOUR:
        reason = f"invalid payload target {target!r}"
        detection = None
    elif controller.last_detection is None:
        reason = "no active visual lock"
        detection = None
    elif not callable(verify):
        reason = "detector has no full-resolution payload verifier"
        detection = None
    else:
        expected_center = (
            controller.last_detection.center_x / max(1.0, float(process_width)),
            controller.last_detection.center_y / max(1.0, float(process_height)),
        )
        detection = verify(
            full_resolution_frame,
            target,
            expected_center_normalized=expected_center,
            max_center_distance_fraction=float(
                controller.config["vision"].get(
                    "payload_association_max_fraction",
                    0.18,
                )
            ),
            frame_id=frame_id,
            captured_at_s=captured_at_s,
            received_at_s=received_at_s,
        )
        verification = getattr(
            controller.detector,
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
        process_desired_x, process_desired_y = controller.desired_drop_point(
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
            controller.center_tolerance_px() * tolerance_scale
        )
        if center_error > full_resolution_tolerance:
            detection = None
            reason = (
                f"full-resolution center error {center_error:.1f}px exceeds "
                f"{full_resolution_tolerance:.1f}px"
            )
        else:
            controller.last_payload_geometry_at = now
            controller.last_payload_geometry_detection = detection
            controller.last_payload_geometry_error_px = center_error
            controller.last_payload_geometry_reason = (
                "fresh full-resolution strict geometry"
            )
            controller.last_strong_geometry_at = now
            if not was_verified:
                controller.event(
                    "PAYLOAD_GEOMETRY_VERIFIED",
                    target=target,
                    frame_id=frame_id,
                    error_px=round(center_error, 1),
                    source=source,
                )
            return True

    controller.last_payload_geometry_reason = reason
    if reason != previous_reason:
        controller.event(
            "PAYLOAD_GEOMETRY_REJECTED",
            target=target,
            frame_id=frame_id,
            reason=reason,
        )
    return False


def perform_payload_action(
    controller: Any,
    now: float,
    auto_resume_state: Any,
    rtl_state: Any,
) -> None:
    p = controller.config["payload"]
    if controller.payload_release_sent_at is None:
        health_errors = controller.payload_release_gate_errors(now)
        if health_errors:
            reason = "; ".join(health_errors)
            if controller.payload_block_started_at is None:
                controller.payload_block_started_at = now
            controller.status_message = f"Payload blocked: {reason}"
            controller.send_velocity(0.0, 0.0, controller.altitude_down(), force=True)
            if reason != controller.last_payload_block_reason:
                controller.event("PAYLOAD_BLOCKED", target=controller.current_target, reason=reason)
                controller.last_payload_block_reason = reason
            if now - controller.payload_block_started_at >= float(
                controller.safety.get("payload_authorization_timeout_s", 8.0)
            ):
                controller.abandon_active_target(
                    now,
                    f"PAYLOAD_GATE_TIMEOUT {reason}",
                )
            return
        controller.last_payload_block_reason = ""
        controller.payload_block_started_at = None
    controller.send_velocity(0.0, 0.0, controller.altitude_down())
    altitude = controller.vehicle.relative_alt_m if controller.vehicle.relative_alt_m is not None else float("nan")
    target = controller.current_target or ""
    payload_colour = payload_colour_for_target(target)
    output = payload_output_for_target(p, target)
    simulate_only = bool(p["simulate_only"])
    ack_timeout_s = float(p.get("command_ack_timeout_s", 2.0))

    if controller.payload_release_sent_at is None:
        controller.status_message = f"Releasing {payload_colour} payload on {target}"
        if simulate_only:
            controller.payload_release_sent_at = now
            controller.payload_release_accepted = True
            controller.payload_started = True
            controller.event("PAYLOAD_SIMULATED", target=target, payload_colour=payload_colour, altitude_m=altitude)
        else:
            sent_at = controller.vehicle.set_servo(
                output["servo_channel"],
                output["release_pwm"],
            )
            controller.payload_release_sent_at = now if sent_at is None else float(sent_at)
            # Record the attempt before waiting for ACK so a process restart
            # cannot repeat a release that may have reached the servo.
            controller._persist_payload_release(target, payload_colour, accepted=False)
            controller.event(
                "PAYLOAD_RELEASE_COMMAND_SENT",
                target=target,
                payload_colour=payload_colour,
                servo_channel=output["servo_channel"],
                pwm=output["release_pwm"],
            )
        return

    if not simulate_only and not controller.payload_release_accepted:
        ack_reader = getattr(controller.vehicle, "command_ack_after", None)
        result = ack_reader(mavutil.mavlink.MAV_CMD_DO_SET_SERVO, controller.payload_release_sent_at) if callable(ack_reader) else None
        if result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
            controller.payload_release_accepted = True
            controller.payload_started = True
            controller._persist_payload_release(target, payload_colour, accepted=True)
            controller.event("PAYLOAD_COMMAND_ACCEPTED", target=target, physical_release_confirmed=False)
        elif result is not None:
            controller.payload_failed = True
            controller.status_message = f"Payload command rejected: MAV_RESULT={result}; pilot action required"
            controller.event("PAYLOAD_COMMAND_REJECTED", target=target, mav_result=result)
            return
        elif now - controller.payload_release_sent_at > ack_timeout_s:
            controller.payload_failed = True
            controller.status_message = "Payload ACK timeout; pilot action required"
            controller.event("PAYLOAD_ACK_TIMEOUT", target=target)
            return
        else:
            controller.status_message = f"Waiting for payload command ACK on {target}"
            return

    if controller.payload_failed:
        controller.send_velocity(0.0, 0.0, controller.altitude_down(), force=True)
        return

    elapsed = now - controller.payload_release_sent_at
    if not simulate_only and controller.payload_reset_sent_at is None and elapsed >= float(p["release_hold_s"]):
        sent_at = controller.vehicle.set_servo(
            output["servo_channel"],
            output["reset_pwm"],
        )
        controller.payload_reset_sent_at = now if sent_at is None else float(sent_at)
        controller.event(
            "PAYLOAD_RESET_COMMAND_SENT",
            target=target,
            pwm=output["reset_pwm"],
        )
        return

    if not simulate_only and controller.payload_reset_sent_at is not None and not controller.payload_reset_accepted:
        ack_reader = getattr(controller.vehicle, "command_ack_after", None)
        result = ack_reader(mavutil.mavlink.MAV_CMD_DO_SET_SERVO, controller.payload_reset_sent_at) if callable(ack_reader) else None
        if result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
            controller.payload_reset_accepted = True
            controller.payload_reset = True
            controller.event("PAYLOAD_RESET_ACCEPTED", target=target)
        elif result is not None or now - controller.payload_reset_sent_at > ack_timeout_s:
            controller.payload_failed = True
            controller.status_message = "Payload reset not confirmed; pilot action required"
            controller.event("PAYLOAD_RESET_FAILED", target=target, mav_result=result)
            return
        else:
            controller.status_message = f"Waiting for payload reset ACK on {target}"
            return

    action_ready = elapsed >= float(p["total_action_time_s"])
    reset_ready = simulate_only or controller.payload_reset_accepted
    if action_ready and reset_ready:
        if controller.current_target:
            if controller.last_center_error_px is not None:
                controller.completed_center_errors[controller.current_target] = controller.last_center_error_px
            controller.completed_targets.add(controller.current_target)
            print(
                f"[TARGET COMPLETE] {controller.current_target}; "
                f"lock_error={controller.last_center_error_px}; done={sorted(controller.completed_targets)}"
            )
        controller.current_target = None
        controller.last_detection = None
        controller.tracker.reset()
        next_mode = "AUTO" if controller.incomplete_targets() else str(controller.config["mission"].get("final_mode", "RTL"))
        controller.vehicle.set_mode(next_mode)
        controller.last_mode_request_at = now
        next_state = auto_resume_state if next_mode == "AUTO" else rtl_state
        controller.transition(next_state, "payload complete")


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

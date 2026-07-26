"""Payload target mapping and release helpers."""
from __future__ import annotations

from typing import Any


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

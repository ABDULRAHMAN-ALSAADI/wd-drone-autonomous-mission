#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from typing import Any, Optional

from pymavlink import mavutil


DEFAULT_UART = "/dev/serial0"
DEFAULT_BAUD = 921600
AUTOPILOT_COMPONENTS = {0, 1}


def heartbeat_is_autopilot(msg, target_system: int) -> bool:
    if msg.get_srcSystem() != target_system:
        return False
    if msg.get_srcComponent() not in AUTOPILOT_COMPONENTS:
        return False
    return msg.type not in (
        mavutil.mavlink.MAV_TYPE_GCS,
        mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
    )


def connect(connection: str, baud: Optional[int], timeout_s: float):
    kwargs: dict[str, Any] = {
        "source_system": 245,
        "source_component": 192,
        "autoreconnect": True,
    }
    if baud is not None:
        kwargs["baud"] = baud
    print(f"[CONNECT] {connection} baud={baud or 'default'}")
    master = mavutil.mavlink_connection(connection, **kwargs)
    heartbeat = master.wait_heartbeat(timeout=timeout_s)
    if heartbeat is None:
        raise TimeoutError(f"No heartbeat received within {timeout_s:.1f}s")
    print(
        f"[HEARTBEAT] system={master.target_system} component={master.target_component} "
        f"mode={mavutil.mode_string_v10(heartbeat)} armed={bool(heartbeat.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)}"
    )
    return master


def mode_name(master) -> str:
    return mavutil.mode_string_v10(master.messages.get("HEARTBEAT", None)) if "HEARTBEAT" in master.messages else "UNKNOWN"


def wait_ack(master, command: int, timeout_s: float = 5.0) -> Optional[str]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        msg = master.recv_match(type="COMMAND_ACK", blocking=True, timeout=0.5)
        if msg is None:
            continue
        if int(msg.command) != int(command):
            continue
        result = mavutil.mavlink.enums["MAV_RESULT"].get(msg.result)
        return result.name if result else str(msg.result)
    return None


def command_status(args) -> int:
    master = connect(args.connection, args.baud, args.timeout)
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        msg = master.recv_match(blocking=True, timeout=1.0)
        if msg is None:
            continue
        kind = msg.get_type()
        if kind == "HEARTBEAT":
            src = f"{msg.get_srcSystem()}:{msg.get_srcComponent()}"
            print(f"HEARTBEAT src={src} mode={mavutil.mode_string_v10(msg)} armed={bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)}")
        elif kind == "SYS_STATUS":
            print(f"SYS_STATUS voltage={msg.voltage_battery / 1000.0:.2f}V battery={msg.battery_remaining}%")
        elif kind == "GPS_RAW_INT":
            print(f"GPS fix={msg.fix_type} sats={msg.satellites_visible}")
        elif kind == "GLOBAL_POSITION_INT":
            print(f"ALT rel={msg.relative_alt / 1000.0:.2f}m vx={msg.vx / 100.0:.2f} vy={msg.vy / 100.0:.2f}")
        elif kind == "VFR_HUD":
            print(f"VFR mode={mode_name(master)} alt={msg.alt:.1f}m groundspeed={msg.groundspeed:.2f}m/s heading={msg.heading}")
    return 0


def command_modes(args) -> int:
    master = connect(args.connection, args.baud, args.timeout)
    mapping = master.mode_mapping()
    for name in sorted(mapping):
        print(f"{name}: {mapping[name]}")
    return 0


def set_mode(master, mode: str) -> None:
    mapping = master.mode_mapping()
    if mode not in mapping:
        available = ", ".join(sorted(mapping))
        raise ValueError(f"Mode {mode!r} unavailable. Available: {available}")
    print(f"[MODE REQUEST] {mode}")
    master.mav.set_mode_send(
        master.target_system,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        mapping[mode],
    )


def observe_mode(master, seconds: float) -> tuple[str, bool]:
    end = time.monotonic() + seconds
    actual = mode_name(master)
    armed = False
    target_system = master.target_system
    while time.monotonic() < end:
        msg = master.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
        if msg is None:
            continue
        src = f"{msg.get_srcSystem()}:{msg.get_srcComponent()}"
        if not heartbeat_is_autopilot(msg, target_system):
            print(f"[MODE IGNORED] src={src} mode={mavutil.mode_string_v10(msg)}")
            continue
        actual = mavutil.mode_string_v10(msg)
        armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        print(f"[MODE OBSERVED] src={src} mode={actual} armed={armed}")
    return actual, armed


def command_set_mode(args) -> int:
    master = connect(args.connection, args.baud, args.timeout)
    set_mode(master, args.mode)
    actual, _armed = observe_mode(master, args.observe)
    if actual == args.mode:
        print(f"[CONFIRMED] requested mode={args.mode} actual={actual}")
    else:
        print(
            f"[WARNING] requested mode={args.mode} actual={actual}. "
            "If this snaps to another mode, check RC flight-mode switch, "
            "Mission Planner/QGC mode controls, and Pixhawk mode failsafe conditions."
        )
    return 0


def command_servo(args) -> int:
    master = connect(args.connection, args.baud, args.timeout)
    if not args.i_understand_props_off:
        raise SystemExit("Refusing servo command without --i-understand-props-off")
    print(f"[SERVO] channel={args.channel} pwm={args.pwm}")
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
        0,
        float(args.channel),
        float(args.pwm),
        0,
        0,
        0,
        0,
        0,
    )
    ack = wait_ack(master, mavutil.mavlink.MAV_CMD_DO_SET_SERVO)
    print(f"[ACK] {ack or 'timeout'}")
    if args.reset_pwm is not None:
        time.sleep(args.hold)
        print(f"[SERVO RESET] channel={args.channel} pwm={args.reset_pwm}")
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
            0,
            float(args.channel),
            float(args.reset_pwm),
            0,
            0,
            0,
            0,
            0,
        )
        ack = wait_ack(master, mavutil.mavlink.MAV_CMD_DO_SET_SERVO)
        print(f"[ACK] {ack or 'timeout'}")
    return 0


def command_speed(args) -> int:
    master = connect(args.connection, args.baud, args.timeout)
    print(f"[SPEED] ground speed={args.speed:.2f}m/s")
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
        0,
        1.0,
        float(args.speed),
        -1.0,
        0,
        0,
        0,
        0,
    )
    ack = wait_ack(master, mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED)
    print(f"[ACK] {ack or 'timeout'}")
    return 0


def command_motor_test(args) -> int:
    if not args.i_understand_props_off or not args.i_accept_motor_spin:
        raise SystemExit("Refusing motor test. Remove props and pass both safety flags.")
    if not 1 <= args.motor <= 8:
        raise ValueError("motor must be 1..8")
    if not 0 <= args.throttle_percent <= 15:
        raise ValueError("For bench safety, throttle-percent must be 0..15")
    master = connect(args.connection, args.baud, args.timeout)
    print(
        f"[MOTOR TEST] motor={args.motor} throttle={args.throttle_percent}% "
        f"duration={args.duration}s PROPS-OFF ONLY"
    )
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST,
        0,
        float(args.motor),
        0.0,
        float(args.throttle_percent),
        float(args.duration),
        0,
        0,
        0,
    )
    ack = wait_ack(master, mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST)
    print(f"[ACK] {ack or 'timeout'}")
    return 0


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--connection", default=DEFAULT_UART, help="MAVLink endpoint, e.g. /dev/serial0 or udpin:0.0.0.0:14551")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--timeout", type=float, default=15.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bench-safe MAVLink tests for Raspberry Pi 5 to Pixhawk/Cube.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="connect and print telemetry")
    add_connection_args(status)
    status.add_argument("--seconds", type=float, default=10.0)
    status.set_defaults(func=command_status)

    modes = subparsers.add_parser("modes", help="print available flight modes")
    add_connection_args(modes)
    modes.set_defaults(func=command_modes)

    set_mode_cmd = subparsers.add_parser("set-mode", help="request a flight mode")
    add_connection_args(set_mode_cmd)
    set_mode_cmd.add_argument("mode", choices=["STABILIZE", "ALT_HOLD", "LOITER", "GUIDED", "AUTO", "RTL", "LAND"])
    set_mode_cmd.add_argument("--observe", type=float, default=3.0, help="seconds to watch heartbeat mode after the request")
    set_mode_cmd.set_defaults(func=command_set_mode)

    servo = subparsers.add_parser("servo", help="send DO_SET_SERVO for payload bench testing")
    add_connection_args(servo)
    servo.add_argument("--channel", type=int, required=True)
    servo.add_argument("--pwm", type=int, required=True)
    servo.add_argument("--reset-pwm", type=int)
    servo.add_argument("--hold", type=float, default=1.0)
    servo.add_argument("--i-understand-props-off", action="store_true")
    servo.set_defaults(func=command_servo)

    speed = subparsers.add_parser("speed", help="send DO_CHANGE_SPEED for AUTO mission speed testing")
    add_connection_args(speed)
    speed.add_argument("--speed", type=float, required=True)
    speed.set_defaults(func=command_speed)

    motor = subparsers.add_parser("motor-test", help="guarded ArduPilot motor test. PROPS OFF ONLY.")
    add_connection_args(motor)
    motor.add_argument("--motor", type=int, required=True)
    motor.add_argument("--throttle-percent", type=float, required=True)
    motor.add_argument("--duration", type=float, default=1.0)
    motor.add_argument("--i-understand-props-off", action="store_true")
    motor.add_argument("--i-accept-motor-spin", action="store_true")
    motor.set_defaults(func=command_motor_test)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

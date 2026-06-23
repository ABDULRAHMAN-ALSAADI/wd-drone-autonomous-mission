#!/usr/bin/env python3
"""Bench-safe MAVLink tools for Cube/Pixhawk and Raspberry Pi tests.

This file is for controlled hardware checks before running the mission
controller. Most commands only listen or request a flight mode. Motor and servo
commands are deliberately guarded by explicit safety flags.
"""
from __future__ import annotations

import argparse
import time
from typing import Any, Optional

from pymavlink import mavutil


DEFAULT_UART = "/dev/serial0"
DEFAULT_BAUD = 921600
AUTOPILOT_COMPONENTS = {mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1}


def heartbeat_is_vehicle(msg) -> bool:
    if msg.get_srcSystem() <= 0:
        return False
    if msg.get_srcComponent() not in AUTOPILOT_COMPONENTS:
        return False
    if msg.type in (
        mavutil.mavlink.MAV_TYPE_GCS,
        mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
    ):
        return False
    return msg.autopilot != mavutil.mavlink.MAV_AUTOPILOT_INVALID


def heartbeat_is_target_vehicle(msg, target_system: int) -> bool:
    return msg.get_srcSystem() == target_system and heartbeat_is_vehicle(msg)


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
    heartbeat = None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        msg = master.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
        if msg is None:
            continue
        src = f"{msg.get_srcSystem()}:{msg.get_srcComponent()}"
        if not heartbeat_is_vehicle(msg):
            print(f"[HEARTBEAT IGNORED] src={src} mode={mavutil.mode_string_v10(msg)}")
            continue
        heartbeat = msg
        master.target_system = msg.get_srcSystem()
        master.target_component = msg.get_srcComponent()
        break
    if heartbeat is None:
        raise TimeoutError(f"No vehicle heartbeat received within {timeout_s:.1f}s")
    src = f"{heartbeat.get_srcSystem()}:{heartbeat.get_srcComponent()}"
    print(
        f"[HEARTBEAT] src={src} "
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


def heartbeat_state(msg) -> tuple[str, bool]:
    return mavutil.mode_string_v10(msg), bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)


def format_heartbeat(msg, label: str = "HEARTBEAT") -> str:
    src = f"{msg.get_srcSystem()}:{msg.get_srcComponent()}"
    mode, armed = heartbeat_state(msg)
    return f"{label} src={src} mode={mode} armed={armed}"


def command_status(args) -> int:
    master = connect(args.connection, args.baud, args.timeout)
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        msg = master.recv_match(blocking=True, timeout=1.0)
        if msg is None:
            continue
        kind = msg.get_type()
        if kind == "HEARTBEAT":
            if heartbeat_is_target_vehicle(msg, master.target_system):
                print(format_heartbeat(msg, "VEHICLE_HEARTBEAT"))
            elif args.all_heartbeats:
                print(format_heartbeat(msg, "OTHER_HEARTBEAT"))
        elif kind == "SYS_STATUS":
            print(f"SYS_STATUS voltage={msg.voltage_battery / 1000.0:.2f}V battery={msg.battery_remaining}%")
        elif kind == "GPS_RAW_INT":
            print(f"GPS fix={msg.fix_type} sats={msg.satellites_visible}")
        elif kind == "GLOBAL_POSITION_INT":
            print(f"ALT rel={msg.relative_alt / 1000.0:.2f}m vx={msg.vx / 100.0:.2f} vy={msg.vy / 100.0:.2f}")
        elif kind == "VFR_HUD":
            print(f"VFR mode={mode_name(master)} alt={msg.alt:.1f}m groundspeed={msg.groundspeed:.2f}m/s heading={msg.heading}")
    return 0


def command_health(args) -> int:
    master = connect(args.connection, args.baud, args.timeout)
    summary: dict[str, Any] = {
        "mode": mode_name(master),
        "armed": None,
        "voltage_v": None,
        "battery_pct": None,
        "gps_fix": None,
        "gps_sats": None,
        "relative_alt_m": None,
        "groundspeed_m_s": None,
        "heading_deg": None,
        "ekf_flags": None,
        "vibration": None,
        "power": None,
        "last_vehicle_heartbeat_s": time.monotonic(),
    }

    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        msg = master.recv_match(blocking=True, timeout=0.5)
        if msg is None:
            continue
        kind = msg.get_type()
        if kind == "HEARTBEAT" and heartbeat_is_target_vehicle(msg, master.target_system):
            summary["mode"] = mavutil.mode_string_v10(msg)
            summary["armed"] = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            summary["last_vehicle_heartbeat_s"] = time.monotonic()
        elif kind == "SYS_STATUS":
            voltage_mv = getattr(msg, "voltage_battery", 0)
            if voltage_mv not in (0, 65535):
                summary["voltage_v"] = voltage_mv / 1000.0
            remaining = getattr(msg, "battery_remaining", -1)
            if remaining >= 0:
                summary["battery_pct"] = int(remaining)
        elif kind == "GPS_RAW_INT":
            summary["gps_fix"] = int(msg.fix_type)
            summary["gps_sats"] = int(msg.satellites_visible)
        elif kind == "GLOBAL_POSITION_INT":
            summary["relative_alt_m"] = msg.relative_alt / 1000.0
        elif kind == "VFR_HUD":
            summary["groundspeed_m_s"] = float(msg.groundspeed)
            summary["heading_deg"] = int(msg.heading)
        elif kind == "EKF_STATUS_REPORT":
            summary["ekf_flags"] = int(msg.flags)
        elif kind == "VIBRATION":
            summary["vibration"] = (
                float(msg.vibration_x),
                float(msg.vibration_y),
                float(msg.vibration_z),
            )
        elif kind == "POWER_STATUS":
            summary["power"] = {
                "vcc_v": msg.Vcc / 1000.0,
                "servo_v": msg.Vservo / 1000.0,
                "flags": int(msg.flags),
            }

    heartbeat_age = time.monotonic() - float(summary["last_vehicle_heartbeat_s"])
    print("[HEALTH]")
    print(f"mode={summary['mode']} armed={summary['armed']} heartbeat_age={heartbeat_age:.1f}s")
    print(f"battery={summary['voltage_v'] or '-'}V/{summary['battery_pct'] if summary['battery_pct'] is not None else '-'}%")
    print(f"gps_fix={summary['gps_fix'] if summary['gps_fix'] is not None else '-'} sats={summary['gps_sats'] if summary['gps_sats'] is not None else '-'}")
    print(f"alt={summary['relative_alt_m'] if summary['relative_alt_m'] is not None else '-'}m speed={summary['groundspeed_m_s'] if summary['groundspeed_m_s'] is not None else '-'}m/s heading={summary['heading_deg'] if summary['heading_deg'] is not None else '-'}deg")
    print(f"ekf_flags={summary['ekf_flags'] if summary['ekf_flags'] is not None else '-'}")
    print(f"vibration={summary['vibration'] if summary['vibration'] is not None else '-'}")
    print(f"power={summary['power'] if summary['power'] is not None else '-'}")

    warnings: list[str] = []
    if heartbeat_age > 2.0:
        warnings.append("vehicle heartbeat is stale")
    if summary["gps_fix"] is not None and int(summary["gps_fix"]) < 3:
        warnings.append("GPS fix is below 3D")
    if summary["gps_sats"] is not None and int(summary["gps_sats"]) < args.min_sats:
        warnings.append(f"GPS satellites below {args.min_sats}")
    if summary["voltage_v"] is None:
        warnings.append("battery voltage not reported")

    if warnings:
        for item in warnings:
            print(f"[WARN] {item}")
    else:
        print("[OK] health telemetry received")
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
    actual = "UNKNOWN"
    armed = False
    target_system = master.target_system
    while time.monotonic() < end:
        msg = master.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
        if msg is None:
            continue
        src = f"{msg.get_srcSystem()}:{msg.get_srcComponent()}"
        if not heartbeat_is_target_vehicle(msg, target_system):
            print(f"[MODE IGNORED] src={src} mode={mavutil.mode_string_v10(msg)}")
            continue
        actual = mavutil.mode_string_v10(msg)
        armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        print(f"[MODE OBSERVED] src={src} mode={actual} armed={armed}")
    return actual, armed


def wait_for_mode(master, expected_mode: str, timeout_s: float) -> tuple[bool, str, bool]:
    deadline = time.monotonic() + timeout_s
    actual = "UNKNOWN"
    armed = False
    while time.monotonic() < deadline:
        msg = master.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
        if msg is None:
            continue
        if not heartbeat_is_target_vehicle(msg, master.target_system):
            continue
        actual, armed = heartbeat_state(msg)
        print(f"[MODE OBSERVED] mode={actual} armed={armed}")
        if actual == expected_mode:
            return True, actual, armed
    return False, actual, armed


def wait_for_armed(master, expected_armed: bool, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        msg = master.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
        if msg is None:
            continue
        if not heartbeat_is_target_vehicle(msg, master.target_system):
            continue
        actual, armed = heartbeat_state(msg)
        print(f"[ARM OBSERVED] mode={actual} armed={armed}")
        if armed == expected_armed:
            return True
    return False


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


def send_arm_command(master, arm: bool) -> None:
    action = "ARM" if arm else "DISARM"
    print(f"[{action} REQUEST]")
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0,
        1.0 if arm else 0.0,
        0,
        0,
        0,
        0,
        0,
        0,
    )


def command_arm(args) -> int:
    if not args.i_understand_props_off or not args.i_accept_arming:
        raise SystemExit("Refusing to arm without --i-understand-props-off and --i-accept-arming")
    master = connect(args.connection, args.baud, args.timeout)
    send_arm_command(master, True)
    ack = wait_ack(master, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, timeout_s=args.observe)
    print(f"[ACK] {ack or 'timeout'}")
    if wait_for_armed(master, True, args.observe):
        print("[CONFIRMED] vehicle is armed")
        return 0
    print("[WARNING] arm was not observed. Check pre-arm failures in Mission Planner/QGC.")
    return 1


def command_disarm(args) -> int:
    master = connect(args.connection, args.baud, args.timeout)
    send_arm_command(master, False)
    ack = wait_ack(master, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, timeout_s=args.observe)
    print(f"[ACK] {ack or 'timeout'}")
    if wait_for_armed(master, False, args.observe):
        print("[CONFIRMED] vehicle is disarmed")
        return 0
    print("[WARNING] disarm was not observed")
    return 1


def request_and_confirm_mode(master, mode: str, observe_s: float, stop_on_failure: bool = True) -> bool:
    set_mode(master, mode)
    ok, actual, armed = wait_for_mode(master, mode, observe_s)
    if ok:
        print(f"[CONFIRMED] mode={mode} armed={armed}")
        return True
    print(f"[WARNING] requested mode={mode} actual={actual} armed={armed}")
    if stop_on_failure:
        raise RuntimeError(f"Mode {mode} was not confirmed")
    return False


def command_bench_sequence(args) -> int:
    if args.dry_run:
        print("[DRY RUN] Sequence: STABILIZE -> GUIDED -> ARM -> AUTO -> RTL -> STABILIZE -> DISARM")
        return 0
    if not args.i_understand_props_off or not args.i_accept_arming:
        raise SystemExit("Refusing armed sequence without --i-understand-props-off and --i-accept-arming")
    master = connect(args.connection, args.baud, args.timeout)
    print("[BENCH SEQUENCE] PROPS OFF. This requests modes and arms through MAVLink.")
    failed = False
    try:
        request_and_confirm_mode(master, "STABILIZE", args.observe)
        request_and_confirm_mode(master, "GUIDED", args.observe)
        send_arm_command(master, True)
        ack = wait_ack(master, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, timeout_s=args.observe)
        print(f"[ARM ACK] {ack or 'timeout'}")
        if not wait_for_armed(master, True, args.observe):
            raise RuntimeError("Vehicle did not report armed. Check pre-arm failures.")
        request_and_confirm_mode(master, "AUTO", args.observe, stop_on_failure=not args.continue_on_mode_failure)
        request_and_confirm_mode(master, "RTL", args.observe, stop_on_failure=not args.continue_on_mode_failure)
        request_and_confirm_mode(master, "STABILIZE", args.observe, stop_on_failure=not args.continue_on_mode_failure)
    except RuntimeError as exc:
        failed = True
        print(f"[BENCH SEQUENCE FAILED] {exc}")
    finally:
        if not args.keep_armed_at_end:
            send_arm_command(master, False)
            ack = wait_ack(master, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, timeout_s=args.observe)
            print(f"[DISARM ACK] {ack or 'timeout'}")
            wait_for_armed(master, False, args.observe)
    if failed:
        return 1
    print("[BENCH SEQUENCE DONE]")
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
    status.add_argument(
        "--all-heartbeats",
        action="store_true",
        help="also print GCS and non-autopilot heartbeats for debugging",
    )
    status.set_defaults(func=command_status)

    health = subparsers.add_parser("health", help="read-only Cube/Pixhawk health summary")
    add_connection_args(health)
    health.add_argument("--seconds", type=float, default=8.0)
    health.add_argument("--min-sats", type=int, default=10)
    health.set_defaults(func=command_health)

    modes = subparsers.add_parser("modes", help="print available flight modes")
    add_connection_args(modes)
    modes.set_defaults(func=command_modes)

    set_mode_cmd = subparsers.add_parser("set-mode", help="request a flight mode")
    add_connection_args(set_mode_cmd)
    set_mode_cmd.add_argument("mode", choices=["STABILIZE", "ALT_HOLD", "LOITER", "GUIDED", "AUTO", "RTL", "LAND"])
    set_mode_cmd.add_argument("--observe", type=float, default=3.0, help="seconds to watch heartbeat mode after the request")
    set_mode_cmd.set_defaults(func=command_set_mode)

    arm = subparsers.add_parser("arm", help="guarded arm command. PROPS OFF ONLY.")
    add_connection_args(arm)
    arm.add_argument("--observe", type=float, default=5.0)
    arm.add_argument("--i-understand-props-off", action="store_true")
    arm.add_argument("--i-accept-arming", action="store_true")
    arm.set_defaults(func=command_arm)

    disarm = subparsers.add_parser("disarm", help="disarm command")
    add_connection_args(disarm)
    disarm.add_argument("--observe", type=float, default=5.0)
    disarm.set_defaults(func=command_disarm)

    bench_sequence = subparsers.add_parser(
        "bench-sequence",
        help="guarded mode/arm sequence: STABILIZE -> GUIDED -> ARM -> AUTO -> RTL -> STABILIZE -> DISARM",
    )
    add_connection_args(bench_sequence)
    bench_sequence.add_argument("--observe", type=float, default=5.0)
    bench_sequence.add_argument("--dry-run", action="store_true")
    bench_sequence.add_argument("--continue-on-mode-failure", action="store_true")
    bench_sequence.add_argument("--keep-armed-at-end", action="store_true")
    bench_sequence.add_argument("--i-understand-props-off", action="store_true")
    bench_sequence.add_argument("--i-accept-arming", action="store_true")
    bench_sequence.set_defaults(func=command_bench_sequence)

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

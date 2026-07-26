import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.mavlink_bench import (
    VELOCITY_ONLY_MASK,
    build_parser,
    command_guided_velocity_test,
    command_motor_test,
    command_servo,
    command_set_mode,
    format_rc_channels,
    guided_velocity_steps,
    send_body_velocity_target,
)


class MavlinkBenchTests(unittest.TestCase):
    def test_default_uart_matches_tested_pi_connection(self) -> None:
        args = build_parser().parse_args(["status"])
        self.assertEqual(args.connection, "/dev/ttyAMA0")
        self.assertEqual(args.baud, 921600)

    def test_format_rc_channels_marks_changed_channel(self) -> None:
        text = format_rc_channels({1: 1500, 7: 1801}, changed={7})
        self.assertIn(" CH01=1500", text)
        self.assertIn("*CH07=1801", text)

    def test_guided_velocity_steps_use_body_axis_signs(self) -> None:
        moving = [step for step in guided_velocity_steps(0.2, 1.0) if step[0] != "ZERO"]
        self.assertEqual(
            moving,
            [
                ("FORWARD", 0.2, 0.0, 1.0),
                ("BACKWARD", -0.2, 0.0, 1.0),
                ("RIGHT", 0.0, 0.2, 1.0),
                ("LEFT", 0.0, -0.2, 1.0),
            ],
        )

    def test_send_body_velocity_matches_mission_message_layout(self) -> None:
        master = Mock()
        master.target_system = 1
        master.target_component = 1

        send_body_velocity_target(master, 0.2, -0.1)

        args = master.mav.set_position_target_local_ned_send.call_args.args
        self.assertEqual(args[1:3], (1, 1))
        self.assertEqual(args[4], VELOCITY_ONLY_MASK)
        self.assertEqual(args[8:11], (0.2, -0.1, 0.0))

    @patch("tools.mavlink_bench.connect")
    def test_guided_velocity_dry_run_never_connects(self, connect: Mock) -> None:
        args = SimpleNamespace(
            speed=0.2,
            duration=1.0,
            rate_hz=10.0,
            dry_run=True,
            i_understand_props_off=False,
            payload_disabled=False,
        )

        self.assertEqual(command_guided_velocity_test(args), 0)
        connect.assert_not_called()

    @patch("tools.mavlink_bench.connect")
    def test_servo_requires_both_explicit_safety_flags(self, connect: Mock) -> None:
        args = SimpleNamespace(
            connection="/dev/serial0",
            baud=921600,
            timeout=15.0,
            channel=5,
            pwm=1900,
            reset_pwm=1100,
            hold=1.0,
            i_understand_props_off=True,
            i_accept_servo_motion=False,
        )

        with self.assertRaises(SystemExit):
            command_servo(args)
        connect.assert_not_called()

    @patch("tools.mavlink_bench.wait_vehicle_state", return_value=("STABILIZE", True))
    @patch("tools.mavlink_bench.connect")
    def test_servo_refuses_armed_vehicle(self, connect: Mock, _state: Mock) -> None:
        args = SimpleNamespace(
            connection="/dev/serial0",
            baud=921600,
            timeout=15.0,
            channel=5,
            pwm=1900,
            reset_pwm=1100,
            hold=1.0,
            i_understand_props_off=True,
            i_accept_servo_motion=True,
        )

        with self.assertRaises(SystemExit):
            command_servo(args)
        connect.return_value.mav.command_long_send.assert_not_called()

    @patch("tools.mavlink_bench.wait_vehicle_state", return_value=("STABILIZE", True))
    @patch("tools.mavlink_bench.connect")
    def test_motor_test_refuses_armed_vehicle(self, connect: Mock, _state: Mock) -> None:
        args = SimpleNamespace(
            connection="/dev/serial0",
            baud=921600,
            timeout=15.0,
            motor=1,
            throttle_percent=5.0,
            duration=1.0,
            i_understand_props_off=True,
            i_accept_motor_spin=True,
        )

        with self.assertRaises(SystemExit):
            command_motor_test(args)
        connect.return_value.mav.command_long_send.assert_not_called()

    @patch("tools.mavlink_bench.wait_vehicle_state", return_value=("STABILIZE", True))
    @patch("tools.mavlink_bench.connect")
    def test_standalone_mode_test_refuses_armed_vehicle(self, connect: Mock, _state: Mock) -> None:
        args = SimpleNamespace(
            connection="/dev/serial0",
            baud=921600,
            timeout=15.0,
            mode="GUIDED",
            observe=5.0,
        )

        with self.assertRaises(SystemExit):
            command_set_mode(args)
        connect.return_value.set_mode.assert_not_called()


if __name__ == "__main__":
    unittest.main()

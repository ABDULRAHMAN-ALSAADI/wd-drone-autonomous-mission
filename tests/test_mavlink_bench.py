import unittest
from unittest.mock import Mock

from tools.mavlink_bench import (
    VELOCITY_ONLY_MASK,
    format_rc_channels,
    guided_velocity_steps,
    send_body_velocity_target,
)


class MavlinkBenchTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

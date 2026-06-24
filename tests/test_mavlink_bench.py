import unittest

from tools.mavlink_bench import format_rc_channels


class MavlinkBenchTests(unittest.TestCase):
    def test_format_rc_channels_marks_changed_channel(self) -> None:
        text = format_rc_channels({1: 1500, 7: 1801}, changed={7})
        self.assertIn(" CH01=1500", text)
        self.assertIn("*CH07=1801", text)


if __name__ == "__main__":
    unittest.main()

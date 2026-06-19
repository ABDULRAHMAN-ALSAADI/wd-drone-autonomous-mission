import json
from pathlib import Path
import tempfile
import unittest

from wd_drone.config import load_config


class ConfigTests(unittest.TestCase):
    def test_loads_selected_profile(self) -> None:
        data = {
            "active_profile": "sitl",
            "profiles": {
                "sitl": {
                    "connection": "udpin:0.0.0.0:14551",
                    "baud": None,
                    "heartbeat_timeout_s": 10
                }
            },
            "telemetry": {
                "status_rate_hz": 2,
                "print_rate_hz": 1,
                "stale_after_s": 3
            },
            "mission": {
                "search_altitude_m": 7,
                "search_speed_m_s": 2.5,
                "lane_spacing_m": 5.5,
                "centering_max_speed_m_s": 1,
                "drop_altitude_m": 3.5,
                "search_start_waypoint": 2,
                "mission_complete_waypoint": 20
            }
        }

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "settings.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            config = load_config(path)

        self.assertEqual(config.profile.name, "sitl")
        self.assertEqual(
            config.profile.connection,
            "udpin:0.0.0.0:14551"
        )
        self.assertIsNone(config.profile.baud)
        self.assertEqual(config.mission.search_start_waypoint, 2)
        self.assertEqual(config.mission.mission_complete_waypoint, 20)


if __name__ == "__main__":
    unittest.main()

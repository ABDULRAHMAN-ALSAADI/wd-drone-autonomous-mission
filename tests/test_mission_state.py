from dataclasses import dataclass
import unittest

from wd_drone.mission_state import MissionObserver, MissionState


@dataclass
class FakeStatus:
    armed: bool | None = False
    mode: str = "STABILIZE"
    current_waypoint: int | None = None
    stale: bool = False

    def is_stale(self, _stale_after_s: float) -> bool:
        return self.stale


class MissionObserverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.observer = MissionObserver(
            search_start_waypoint=2,
            mission_complete_waypoint=20,
            stale_after_s=3.0,
        )

    def test_waits_for_arm(self) -> None:
        transition = self.observer.update(FakeStatus())
        self.assertIsNotNone(transition)
        self.assertEqual(
            transition.current,
            MissionState.WAITING_FOR_ARM,
        )

    def test_tracks_auto_transit(self) -> None:
        status = FakeStatus(
            armed=True,
            mode="AUTO",
            current_waypoint=1,
        )
        transition = self.observer.update(status)
        self.assertEqual(
            transition.current,
            MissionState.AUTO_TRANSIT,
        )

    def test_activates_search_at_configured_waypoint(self) -> None:
        status = FakeStatus(
            armed=True,
            mode="AUTO",
            current_waypoint=2,
        )
        transition = self.observer.update(status)
        self.assertEqual(
            transition.current,
            MissionState.SEARCH_ACTIVE,
        )

    def test_detects_link_loss(self) -> None:
        transition = self.observer.update(FakeStatus(stale=True))
        self.assertEqual(
            transition.current,
            MissionState.LINK_LOST,
        )

    def test_marks_complete_after_disarm(self) -> None:
        self.observer.update(
            FakeStatus(
                armed=True,
                mode="AUTO",
                current_waypoint=3,
            )
        )
        transition = self.observer.update(
            FakeStatus(
                armed=False,
                mode="LAND",
                current_waypoint=10,
            )
        )
        self.assertEqual(
            transition.current,
            MissionState.MISSION_COMPLETE,
        )


if __name__ == "__main__":
    unittest.main()

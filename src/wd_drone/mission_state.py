from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol


class StatusView(Protocol):
    armed: bool | None
    mode: str
    current_waypoint: int | None

    def is_stale(self, stale_after_s: float) -> bool:
        ...


class MissionState(Enum):
    STARTUP = auto()
    LINK_LOST = auto()
    WAITING_FOR_ARM = auto()
    WAITING_FOR_AUTO = auto()
    AUTO_TRANSIT = auto()
    SEARCH_ACTIVE = auto()
    MISSION_COMPLETE = auto()


@dataclass(frozen=True)
class Transition:
    previous: MissionState
    current: MissionState
    reason: str


class MissionObserver:
    """Read-only mission state estimator.

    The observer converts vehicle telemetry into deterministic mission states.
    It never sends a flight command.
    """

    def __init__(
        self,
        search_start_waypoint: int,
        mission_complete_waypoint: int,
        stale_after_s: float,
    ) -> None:
        self.search_start_waypoint = search_start_waypoint
        self.mission_complete_waypoint = mission_complete_waypoint
        self.stale_after_s = stale_after_s
        self.state = MissionState.STARTUP
        self.has_armed_once = False

    def update(self, status: StatusView) -> Transition | None:
        if status.armed is True:
            self.has_armed_once = True

        target_state, reason = self._calculate(status)

        if target_state == self.state:
            return None

        transition = Transition(
            previous=self.state,
            current=target_state,
            reason=reason,
        )
        self.state = target_state
        return transition

    def _calculate(self, status: StatusView) -> tuple[MissionState, str]:
        if status.is_stale(self.stale_after_s):
            return MissionState.LINK_LOST, "heartbeat is stale"

        if self.has_armed_once and status.armed is False:
            return MissionState.MISSION_COMPLETE, "vehicle disarmed after mission start"

        if status.armed is not True:
            return MissionState.WAITING_FOR_ARM, "vehicle is disarmed"

        if status.mode.upper() != "AUTO":
            return MissionState.WAITING_FOR_AUTO, (
                f"vehicle is armed in {status.mode} mode"
            )

        waypoint = status.current_waypoint
        if waypoint is None:
            return MissionState.WAITING_FOR_AUTO, (
                "AUTO mode active but current waypoint is unknown"
            )

        if waypoint >= self.mission_complete_waypoint:
            return MissionState.MISSION_COMPLETE, (
                f"waypoint {waypoint} reached mission completion threshold"
            )

        if waypoint >= self.search_start_waypoint:
            return MissionState.SEARCH_ACTIVE, (
                f"waypoint {waypoint} reached search activation threshold"
            )

        return MissionState.AUTO_TRANSIT, (
            f"AUTO mission is travelling to search waypoint "
            f"{self.search_start_waypoint}"
        )

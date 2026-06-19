from __future__ import annotations

import logging
from typing import Iterable

from pymavlink import mavutil

from .config import ConnectionProfile
from .vehicle_status import VehicleStatus


LOGGER = logging.getLogger(__name__)


class MavlinkClient:
    """Read-only MAVLink client used by Phase 1 and Phase 2.

    It reads vehicle telemetry, mission progress and requests message rates.
    It does not arm, change flight mode, move the aircraft, or actuate payload
    outputs.
    """

    def __init__(self, profile: ConnectionProfile) -> None:
        self.profile = profile
        self.connection = None
        self.status = VehicleStatus()

    def connect(self) -> None:
        kwargs = {
            "autoreconnect": True,
            "source_system": 245,
            "source_component": 190,
        }
        if self.profile.baud is not None:
            kwargs["baud"] = self.profile.baud

        LOGGER.info(
            "Opening MAVLink connection '%s' using profile '%s'",
            self.profile.connection,
            self.profile.name,
        )
        self.connection = mavutil.mavlink_connection(
            self.profile.connection,
            **kwargs,
        )

        heartbeat = self.connection.wait_heartbeat(
            timeout=self.profile.heartbeat_timeout_s
        )
        if heartbeat is None:
            raise TimeoutError(
                "No MAVLink heartbeat received within "
                f"{self.profile.heartbeat_timeout_s:.1f} seconds"
            )

        self.status.update(heartbeat)
        LOGGER.info(
            "Heartbeat received from system %s component %s",
            self.connection.target_system,
            self.connection.target_component,
        )

    def request_standard_telemetry(self, rate_hz: float) -> None:
        if self.connection is None:
            raise RuntimeError("MAVLink connection has not been opened")

        message_ids: Iterable[int] = (
            mavutil.mavlink.MAVLINK_MSG_ID_SYS_STATUS,
            mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT,
            mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT,
            mavutil.mavlink.MAVLINK_MSG_ID_VFR_HUD,
            mavutil.mavlink.MAVLINK_MSG_ID_MISSION_CURRENT,
        )
        interval_us = int(1_000_000 / max(rate_hz, 0.1))

        for message_id in message_ids:
            self.connection.mav.command_long_send(
                self.connection.target_system,
                self.connection.target_component,
                mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                0,
                message_id,
                interval_us,
                0,
                0,
                0,
                0,
                0,
            )

    def receive_available(self, max_messages: int = 200) -> list[str]:
        if self.connection is None:
            raise RuntimeError("MAVLink connection has not been opened")

        message_types: list[str] = []
        received = 0

        while received < max_messages:
            message = self.connection.recv_match(blocking=False)
            if message is None:
                break

            message_type = message.get_type()
            if message_type != "BAD_DATA":
                self.status.update(message)
                message_types.append(message_type)
            received += 1

        return message_types

    def close(self) -> None:
        if self.connection is not None:
            close_method = getattr(self.connection, "close", None)
            if callable(close_method):
                close_method()
            self.connection = None

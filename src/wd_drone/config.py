from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ConnectionProfile:
    name: str
    connection: str
    baud: int | None
    heartbeat_timeout_s: float


@dataclass(frozen=True)
class TelemetryConfig:
    status_rate_hz: float
    print_rate_hz: float
    stale_after_s: float


@dataclass(frozen=True)
class MissionConfig:
    search_altitude_m: float
    search_speed_m_s: float
    lane_spacing_m: float
    centering_max_speed_m_s: float
    drop_altitude_m: float
    search_start_waypoint: int
    mission_complete_waypoint: int


@dataclass(frozen=True)
class AppConfig:
    profile: ConnectionProfile
    telemetry: TelemetryConfig
    mission: MissionConfig


def _require(mapping: dict[str, Any], key: str) -> Any:
    if key not in mapping:
        raise ValueError(f"Missing configuration key: {key}")
    return mapping[key]


def load_config(path: str | Path, profile_override: str | None = None) -> AppConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    profile_name = (
        profile_override
        or os.getenv("WD_DRONE_PROFILE")
        or _require(raw, "active_profile")
    )

    profiles = _require(raw, "profiles")
    if profile_name not in profiles:
        available = ", ".join(sorted(profiles))
        raise ValueError(
            f"Unknown profile '{profile_name}'. Available profiles: {available}"
        )

    selected = profiles[profile_name]
    telemetry = _require(raw, "telemetry")
    mission = _require(raw, "mission")

    baud_value = selected.get("baud")
    baud = int(baud_value) if baud_value is not None else None

    search_start_waypoint = int(_require(mission, "search_start_waypoint"))
    mission_complete_waypoint = int(
        _require(mission, "mission_complete_waypoint")
    )

    if search_start_waypoint < 0:
        raise ValueError("search_start_waypoint must be zero or greater")
    if mission_complete_waypoint <= search_start_waypoint:
        raise ValueError(
            "mission_complete_waypoint must be greater than search_start_waypoint"
        )

    return AppConfig(
        profile=ConnectionProfile(
            name=profile_name,
            connection=str(_require(selected, "connection")),
            baud=baud,
            heartbeat_timeout_s=float(
                _require(selected, "heartbeat_timeout_s")
            ),
        ),
        telemetry=TelemetryConfig(
            status_rate_hz=float(_require(telemetry, "status_rate_hz")),
            print_rate_hz=float(_require(telemetry, "print_rate_hz")),
            stale_after_s=float(_require(telemetry, "stale_after_s")),
        ),
        mission=MissionConfig(
            search_altitude_m=float(_require(mission, "search_altitude_m")),
            search_speed_m_s=float(_require(mission, "search_speed_m_s")),
            lane_spacing_m=float(_require(mission, "lane_spacing_m")),
            centering_max_speed_m_s=float(
                _require(mission, "centering_max_speed_m_s")
            ),
            drop_altitude_m=float(_require(mission, "drop_altitude_m")),
            search_start_waypoint=search_start_waypoint,
            mission_complete_waypoint=mission_complete_waypoint,
        ),
    )

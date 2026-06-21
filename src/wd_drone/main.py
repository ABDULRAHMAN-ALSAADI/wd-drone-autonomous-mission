"""Read-only mission observer entry point.

This package is for monitoring telemetry and mission state. It does not send
flight commands, arm, change mode, move servos, or run computer vision.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import signal
import sys
import time

from .config import load_config
from .event_logger import EventLogger
from .mavlink_client import MavlinkClient
from .mission_state import MissionObserver


STOP_REQUESTED = False


def _handle_stop_signal(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="WD DRONE Phase 2 read-only mission observer"
    )
    parser.add_argument(
        "--config",
        default="config/settings.json",
        help="Path to the JSON configuration file",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Connection profile name, for example sitl or cube_pi_uart",
    )
    parser.add_argument(
        "--event-log",
        default="logs/mission_events.jsonl",
        help="Path to the JSON Lines event log",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(Path(args.config), args.profile)
    except (OSError, ValueError) as exc:
        logging.error("Configuration error: %s", exc)
        return 2

    signal.signal(signal.SIGINT, _handle_stop_signal)
    signal.signal(signal.SIGTERM, _handle_stop_signal)

    event_logger = EventLogger(args.event_log)
    observer = MissionObserver(
        search_start_waypoint=config.mission.search_start_waypoint,
        mission_complete_waypoint=config.mission.mission_complete_waypoint,
        stale_after_s=config.telemetry.stale_after_s,
    )
    client = MavlinkClient(config.profile)

    last_waypoint = None
    last_reached_waypoint = None

    try:
        client.connect()
        client.request_standard_telemetry(
            config.telemetry.status_rate_hz
        )

        print(
            f"Connected with profile '{config.profile.name}'. "
            "Phase 2 observes mission progress and sends no flight commands."
        )
        print(
            f"Search activates at waypoint "
            f"{config.mission.search_start_waypoint}."
        )

        event_logger.write(
            "application_started",
            profile=config.profile.name,
            connection=config.profile.connection,
            search_start_waypoint=(
                config.mission.search_start_waypoint
            ),
            mission_complete_waypoint=(
                config.mission.mission_complete_waypoint
            ),
        )

        print_period_s = 1.0 / max(config.telemetry.print_rate_hz, 0.1)
        next_print = time.monotonic()

        while not STOP_REQUESTED:
            client.receive_available()
            status = client.status

            if status.current_waypoint != last_waypoint:
                event_logger.write(
                    "current_waypoint_changed",
                    previous_waypoint=last_waypoint,
                    current_waypoint=status.current_waypoint,
                    mode=status.mode,
                    armed=status.armed,
                )
                last_waypoint = status.current_waypoint

            if status.last_reached_waypoint != last_reached_waypoint:
                if status.last_reached_waypoint is not None:
                    event_logger.write(
                        "waypoint_reached",
                        waypoint=status.last_reached_waypoint,
                        mode=status.mode,
                        armed=status.armed,
                    )
                last_reached_waypoint = status.last_reached_waypoint

            transition = observer.update(status)
            if transition is not None:
                print(
                    "MISSION_STATE "
                    f"{transition.previous.name} -> "
                    f"{transition.current.name}: "
                    f"{transition.reason}",
                    flush=True,
                )
                event_logger.write(
                    "mission_state_transition",
                    previous=transition.previous.name,
                    current=transition.current.name,
                    reason=transition.reason,
                    waypoint=status.current_waypoint,
                    mode=status.mode,
                    armed=status.armed,
                )

            now = time.monotonic()
            if now >= next_print:
                print(
                    f"mission={observer.state.name} "
                    + status.format_line(
                        config.telemetry.stale_after_s
                    ),
                    flush=True,
                )
                next_print = now + print_period_s

            time.sleep(0.02)

    except TimeoutError as exc:
        logging.error("%s", exc)
        event_logger.write("connection_timeout", error=str(exc))
        return 3
    except KeyboardInterrupt:
        pass
    finally:
        event_logger.write(
            "application_stopped",
            final_state=observer.state.name,
            final_waypoint=client.status.current_waypoint,
        )
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

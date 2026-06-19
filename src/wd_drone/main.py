from __future__ import annotations

import argparse
import logging
from pathlib import Path
import signal
import sys
import time

from .config import load_config
from .mavlink_client import MavlinkClient


STOP_REQUESTED = False


def _handle_stop_signal(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="WD DRONE Phase 1 read-only MAVLink monitor"
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

    config_path = Path(args.config)
    try:
        config = load_config(config_path, args.profile)
    except (OSError, ValueError) as exc:
        logging.error("Configuration error: %s", exc)
        return 2

    signal.signal(signal.SIGINT, _handle_stop_signal)
    signal.signal(signal.SIGTERM, _handle_stop_signal)

    client = MavlinkClient(config.profile)
    try:
        client.connect()
        client.request_standard_telemetry(
            config.telemetry.status_rate_hz
        )

        print(
            f"Connected with profile '{config.profile.name}'. "
            "This Phase 1 program is read-only."
        )

        print_period_s = 1.0 / max(config.telemetry.print_rate_hz, 0.1)
        next_print = time.monotonic()

        while not STOP_REQUESTED:
            client.receive_available()

            now = time.monotonic()
            if now >= next_print:
                print(
                    client.status.format_line(
                        config.telemetry.stale_after_s
                    ),
                    flush=True,
                )
                next_print = now + print_period_s

            time.sleep(0.02)

    except TimeoutError as exc:
        logging.error("%s", exc)
        return 3
    except KeyboardInterrupt:
        pass
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

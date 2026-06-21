# Project Structure

This file explains the repository in plain language.

## Active Mission Code

`target_mission_v2/` is the main mission software.

- `mission_controller.py` is the state machine and MAVLink control loop.
- `vision.py` detects red triangles and blue hexagons.
- `control.py` contains small math helpers.
- `parameter_config.json` is the default operator tuning file.
- `configs/sim_gazebo.json` is for SITL/Gazebo.
- `configs/real_pi_camera_module_3.json` is the starting real Cube/Pi profile.
- `run.sh` starts the controller.

This is the folder to open when you want to understand or change the mission.

## Bench And Pi Commands

`scripts/` contains commands humans run:

- `sync_to_pi.sh`: copy laptop source to the Pi.
- `pi_validate.sh`: install/check Pi Python dependencies and tests.
- `pi_test_day_readiness.sh`: check tomorrow hardware test readiness.
- `pi_uart_preflight.sh`: check the Pi UART mapping.
- `mavlink_bench.sh`: safe Cube/Pixhawk bench commands.
- `check_project.sh`: run local tests.

## Bench Tool Internals

`tools/mavlink_bench.py` is used by `scripts/mavlink_bench.sh`.

It can:

- listen to heartbeat/status;
- print a health summary;
- list flight modes;
- request safe mode changes;
- send guarded servo commands;
- send guarded motor-test commands.

Motor tests require explicit safety flags and should only be used with
propellers removed.

## Read-Only Observer

`src/wd_drone/` is a separate read-only monitor package.

It observes telemetry and mission state. It does not arm, change mode, move the
drone, move servos, or upload missions.

Use it for monitoring and logging, not for active target centering.

## Tests

`tests/` covers the read-only observer.

`target_mission_v2/test_mission_controller.py` covers the active mission
controller and strict vision rules.

## Documents

`docs/` holds the operating knowledge. When the team learns a wiring detail,
Mission Planner setting, or field-test result, write it there.

## Generated Or Local-Only Folders

These are not source code:

- `.venv/`: local Python environment.
- `.wheelhouse/`: Pi-side offline Python package cache.
- `__pycache__/`: Python bytecode cache.
- `logs/`: runtime logs.

Do not edit those by hand.

# Project Structure

This file explains the repository in plain language.

## The Two Folders To Use First

### `real_mission/`

This is the real-drone operator folder.

- `run_real_mission.sh`: run this on the Raspberry Pi to start the mission.
- `open_laptop_camera_window.sh`: run this on the Ubuntu laptop to watch the Pi
  Camera Module 3 feed.
- `parameter_config/real_drone.json`: edit this for real-drone tuning.
- `parameter_config/README.md`: explains the important tuning fields.

### `test_components/`

This is the bench-test folder.

- `COMMANDS.md`: step-by-step component test commands.
- `camera/`: Pi camera and laptop live-view tests.
- `mavlink/`: Cube/Pi heartbeat, health, mode, arm, servo, and motor tests.
- `software/`: local code and config checks.

Use this folder before trusting the real mission.

## Mission Engine

`target_mission_v2/` is the tested mission engine used by `real_mission/`.

- `mission_controller.py`: mission state machine and MAVLink control loop.
- `vision.py`: red triangle and blue hexagon detector.
- `camera_sources.py`: SITL camera and Pi Camera Module 3 input.
- `control.py`: small centering and altitude math helpers.
- `test_mission_controller.py`: mission and vision unit tests.
- `parameter_config.json`: SITL/operator tuning file.
- `configs/sim_gazebo.json`: SITL/Gazebo profile.
- `configs/real_pi_camera_module_3.json`: older real profile kept for
  compatibility. Prefer `real_mission/parameter_config/real_drone.json`.

Open this folder when you need to change code behavior, not just tune values.

## Low-Level Commands

`scripts/` contains the lower-level shell toolbox used by the clean wrappers.

Examples:

- `sync_to_pi.sh`: copy laptop source to the Pi.
- `pi_validate.sh`: check Pi Python dependencies and tests.
- `pi_uart_preflight.sh`: check Pi UART mapping.
- `mavlink_bench.sh`: safe Cube/Pixhawk bench command wrapper.
- `check_project.sh`: run local tests.

Normal operators should start from `test_components/COMMANDS.md` instead.

## Tool Internals

`tools/` contains Python helper programs behind the scripts.

- `mavlink_bench.py`: heartbeat, health, modes, guarded arm, servo, speed, and
  motor-test commands.
- `pi_camera_check.py`: Pi-side camera FPS and preview check.
- `pi_camera_live_view.py`: laptop live camera window and detector overlay.

## Read-Only Observer

`src/wd_drone/` is a separate read-only monitor package.

It observes telemetry and mission state. It does not arm, change mode, move the
drone, move servos, or upload missions.

## Tests

- `tests/`: read-only observer tests.
- `target_mission_v2/test_mission_controller.py`: active mission, config, and
  vision tests.

## Documents

`docs/` holds longer operating knowledge: safety, wiring, test day, monitoring,
camera calibration, and model plans.

## Generated Or Local-Only Folders

These are not source code:

- `.venv/`: local Python environment.
- `.wheelhouse/`: Pi-side offline Python package cache.
- `__pycache__/`: Python bytecode cache.
- `logs/`: runtime logs.

Do not edit those by hand.

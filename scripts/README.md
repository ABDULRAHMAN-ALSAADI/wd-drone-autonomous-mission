# Scripts

These are terminal commands for humans.

For normal drone work, start with the cleaner folders:

```text
real_mission/
test_components/
```

This `scripts/` folder is the lower-level toolbox behind those wrappers.

## Daily Development

- `setup.sh`: create/update local Python environment.
- `check_project.sh`: run local tests.
- `sync_to_pi.sh`: copy laptop source to the Raspberry Pi without deleting Pi
  files.
- `clean_workspace.sh`: remove caches/logs when needed.

## Raspberry Pi Preparation

- `pi_validate.sh`: install/check Pi dependencies and run light tests.
- `pi_uart_preflight.sh`: verify Pi UART is mapped to GPIO14/15 and free for
  MAVLink.
- `pi_camera_check.sh`: open the Pi Camera Module 3 stream, print FPS/health,
  and save a live preview image.
- `pi_camera_live.sh`: run on the Ubuntu laptop; opens a live Pi camera window
  and runs the mission OpenCV detector on that stream.
- `pi_test_day_readiness.sh`: full safe readiness report for hardware test day.
- `pi_cache_wheels.sh`: cache Python wheels on the Pi for poor internet.

## MAVLink Bench

- `mavlink_bench.sh`: heartbeat, health, mode, servo, speed, and guarded motor
  tests.
- `pi_mavlink_bench_sequence.sh`: guarded mode/arm bench sequence for Cube/Pi
  testing. It refuses to arm unless explicit safety flags are passed.

Useful read-only health command:

```bash
./scripts/mavlink_bench.sh health --connection /dev/serial0 --baud 921600 --seconds 10
```

Motor tests require explicit safety flags and propellers removed.

Guarded mode/arm bench sequence:

```bash
./test_components/mavlink/bench_sequence.sh --dry-run
./test_components/mavlink/bench_sequence.sh --i-understand-props-off --i-accept-arming
```

## Pi Camera Dependency

The mission and camera checker expect OpenCV from Raspberry Pi OS packages:

```bash
sudo apt install python3-opencv python3-numpy
python3 -m venv --system-site-packages .venv
```

This keeps the project from building a large OpenCV wheel on the Pi.

Live camera window from the laptop:

```bash
./real_mission/open_laptop_camera_window.sh
```

Keys: `q`/Esc quit, `s` saves a snapshot, `m` toggles red/blue masks.

## Monitors

- `run_sitl_observer.sh`: read-only monitor for SITL.
- `run_uart_monitor.sh`: read-only monitor for the real Cube/Pi UART profile.
- `run_sitl_monitor.sh`: SITL monitor helper.

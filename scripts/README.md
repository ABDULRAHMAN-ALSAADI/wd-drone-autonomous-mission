# Scripts

These are terminal commands for humans.

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
- `pi_test_day_readiness.sh`: full safe readiness report for hardware test day.
- `pi_cache_wheels.sh`: cache Python wheels on the Pi for poor internet.

## MAVLink Bench

- `mavlink_bench.sh`: heartbeat, health, mode, servo, speed, and guarded motor
  tests.

Useful read-only health command:

```bash
./scripts/mavlink_bench.sh health --connection /dev/serial0 --baud 921600 --seconds 10
```

Motor tests require explicit safety flags and propellers removed.

## Monitors

- `run_sitl_observer.sh`: read-only monitor for SITL.
- `run_uart_monitor.sh`: read-only monitor for the real Cube/Pi UART profile.
- `run_sitl_monitor.sh`: SITL monitor helper.

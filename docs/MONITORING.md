# Monitoring

Monitoring has two levels:

1. Bench health checks before running the mission.
2. Mission overlay/logging while the target mission is running.

## Bench Health

From the Pi:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/mavlink_bench.sh health --connection /dev/serial0 --baud 921600 --seconds 10
```

This is read-only. It summarizes:

- real Cube heartbeat;
- flight mode;
- armed/disarmed state;
- battery voltage/percentage if available;
- GPS fix type and satellites if GPS is connected;
- altitude/speed/heading if reported;
- EKF flags if reported;
- vibration if reported;
- power status if reported.

Use this before any mode, servo, motor, or mission test.

## Read-Only Mission Observer

The observer in `src/wd_drone/` sends no flight commands.

Cube/Pi UART:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/run_uart_monitor.sh
```

It prints:

- link state;
- armed state;
- mode;
- waypoint;
- GPS fix/satellites;
- altitude;
- speed;
- heading;
- battery.

## Active Mission Overlay

The active mission controller displays:

- mission state;
- mode;
- waypoint;
- target lock and centering status;
- target hit counts;
- done targets;
- mission completion count;
- speed and acceleration.

The active mission controller can command GUIDED centering and payload actions.
Use the read-only observer when you only want status.

## What To Watch Tomorrow

Before motor tests:

- Pi temp below 80 C;
- `throttled=0x0`;
- Cube heartbeat from `src=1:1`;
- `armed=False`;
- GPS status understood, even if GPS is not connected yet;
- battery/power readings are sane;
- Mission Planner agrees with the Pi mode output.

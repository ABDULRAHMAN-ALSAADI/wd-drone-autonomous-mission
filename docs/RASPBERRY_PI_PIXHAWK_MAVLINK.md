# Raspberry Pi 5 to Pixhawk MAVLink Bench Tests

Use this to verify communication before running the autonomous mission.

## Wiring

- Pi GPIO 14 TXD connects to Pixhawk TELEM RX.
- Pi GPIO 15 RXD connects to Pixhawk TELEM TX.
- Ground connects to ground.
- Do not power servos or payload from the Pi UART pins.
- Remove propellers for every command test.

On the Pixhawk/Cube TELEM port, set the serial protocol and baud to match the
Pi connection. This repo defaults to:

```text
/dev/serial0
921600 baud
```

## Enable Pi UART

On Raspberry Pi OS, enable the serial port and disable the serial login shell:

```bash
sudo raspi-config
```

Then check:

```bash
ls -l /dev/serial0
```

## Install

```bash
cd ~/wd-drone-autonomous-mission
./scripts/setup.sh
```

## Heartbeat And Telemetry

First test only the link:

```bash
./scripts/mavlink_bench.sh status --connection /dev/serial0 --baud 921600 --seconds 10
```

Expected result:

```text
[HEARTBEAT] system=1 component=1 mode=...
```

## Modes

List modes:

```bash
./scripts/mavlink_bench.sh modes --connection /dev/serial0 --baud 921600
```

Request GUIDED:

```bash
./scripts/mavlink_bench.sh set-mode GUIDED --connection /dev/serial0 --baud 921600
```

Request AUTO:

```bash
./scripts/mavlink_bench.sh set-mode AUTO --connection /dev/serial0 --baud 921600
```

Use QGC as the authority for whether the mode request was accepted. Some modes
require GPS, EKF readiness, arming state, or a valid mission.

## Payload Servo Bench Test

Props off. Payload disconnected first if you are unsure about the channel.

```bash
./scripts/mavlink_bench.sh servo \
  --connection /dev/serial0 \
  --baud 921600 \
  --channel 9 \
  --pwm 1900 \
  --reset-pwm 1100 \
  --hold 1.0 \
  --i-understand-props-off
```

## AUTO Speed Command Test

This sends `MAV_CMD_DO_CHANGE_SPEED`:

```bash
./scripts/mavlink_bench.sh speed \
  --connection /dev/serial0 \
  --baud 921600 \
  --speed 3.0
```

For the mission itself, prefer setting AUTO speed in QGC unless you intentionally
want companion-owned speed commands.

## Motor Test

Avoid this until heartbeat, modes, and servo tests are already correct.

Props off is mandatory. The script refuses more than 15 percent throttle.

```bash
./scripts/mavlink_bench.sh motor-test \
  --connection /dev/serial0 \
  --baud 921600 \
  --motor 1 \
  --throttle-percent 5 \
  --duration 1 \
  --i-understand-props-off \
  --i-accept-motor-spin
```

Do not run motor tests on a fully assembled aircraft with propellers mounted.

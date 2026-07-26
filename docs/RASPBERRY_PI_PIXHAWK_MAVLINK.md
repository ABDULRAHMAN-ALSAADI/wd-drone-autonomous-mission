# Raspberry Pi 5 to Pixhawk MAVLink Bench Tests

Use this to verify communication before running the autonomous mission.

For the full bench order, including servo and motor-test sequence, see
`test_components/COMMANDS.md`.

## Wiring

- Pi GPIO 14 TXD connects to Pixhawk TELEM RX.
- Pi GPIO 15 RXD connects to Pixhawk TELEM TX.
- Ground connects to ground.
- Do not power servos or payload from the Pi UART pins.
- Remove propellers for every command test.

On the Pixhawk/Cube TELEM port, set the serial protocol and baud to match the
Pi connection. This repo defaults to:

```text
/dev/ttyAMA0
921600 baud
```

This is a measured project-specific choice. On the tested Raspberry Pi 5,
`/dev/serial0` points to `/dev/ttyAMA10`, while the Cube heartbeat arrives on
`/dev/ttyAMA0`. Check the selected mission profile instead of assuming the
`serial0` alias is correct on every Pi.

## Pi UART Preflight

Before connecting Pixhawk, the Pi UART must be dedicated to MAVLink. The ready
state is:

```text
/dev/ttyAMA0 exists and is readable/writable by the `dialout` user
GPIO14 = TXD0
GPIO15 = RXD0
no console=ttyAMA... in /proc/cmdline
no active serial-getty on the MAVLink UART
```

From the Ubuntu laptop:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/pi_uart_preflight.sh
```

If it reports a serial console/getty problem, fix that before wiring Pixhawk.
The companion computer must not share the MAVLink UART with a Linux login
console.

The current Pi 5 setup uses this boot config block:

```text
# WD Drone: MAVLink UART for Pixhawk TELEM on GPIO14/15.
dtparam=uart0_console=off
dtoverlay=uart0-pi5
```

## Install

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/setup.sh
```

## Heartbeat And Telemetry

First test only the link:

```bash
./test_components/mavlink/status.sh
```

Expected result:

```text
[HEARTBEAT] src=1:1 mode=... armed=...
```

The important part is that the Cube vehicle heartbeat arrives and the mode/armed
state are readable. Ignored heartbeats from a GCS or bridge are normal when
Mission Planner/QGC is also connected.

If heartbeat times out, do not try mode, servo, or motor commands yet. Check:

- Pixhawk is powered.
- Pi TX goes to Pixhawk TELEM RX.
- Pi RX goes to Pixhawk TELEM TX.
- Pi ground goes to Pixhawk ground.
- The Pixhawk TELEM port is configured for MAVLink 2 at the same baud.

## Modes

Read-only health summary:

```bash
./test_components/mavlink/health.sh
```

List modes:

```bash
./scripts/mavlink_bench.sh modes --connection /dev/ttyAMA0 --baud 921600
```

Request GUIDED:

```bash
./scripts/mavlink_bench.sh set-mode GUIDED --connection /dev/ttyAMA0 --baud 921600
```

Return to STABILIZE:

```bash
./scripts/mavlink_bench.sh set-mode STABILIZE --connection /dev/ttyAMA0 --baud 921600
```

The script watches heartbeat after the request. If it says the requested mode
was `STABILIZE` but the actual mode became `ALT_HOLD`, the Pi link is working
but another mode authority is winning. Check the RC/transmitter flight-mode
switch first, then Mission Planner/QGC mode controls and Pixhawk failsafe or
mode conditions. AUTO also requires a valid uploaded mission.

## Guarded Mode/Arm Sequence

Use this when you want one command that tests command authority in the same
order we care about for the mission:

```bash
./test_components/mavlink/bench_sequence.sh --dry-run
```

Real run, propellers removed only:

```bash
./test_components/mavlink/bench_sequence.sh --i-understand-props-off --i-accept-arming
```

Sequence:

```text
STABILIZE -> GUIDED -> ARM -> AUTO -> RTL -> STABILIZE -> DISARM
```

The tool refuses to arm unless both safety flags are present. It does not force
arming or bypass ArduPilot pre-arm checks.

## Payload Servo And Vision Bench Test

Props off. Payload disconnected first if you are unsure about the channel.

```bash
./test_components/mavlink/servo_payload_test.sh \
  --target red_triangle \
  --i-understand-props-off \
  --i-accept-servo-motion

./test_components/mavlink/servo_payload_test.sh \
  --target blue_hexagon \
  --i-understand-props-off \
  --i-accept-servo-motion
```

The tested selector mapping is `1300 -> 1500` for a red triangle and
`1700 -> 1500` for a blue hexagon, with a three-second hold. Channel 5 means
MAIN OUT 5.

For automatic recognition plus the physical selector, follow
[Component Test Commands](../test_components/COMMANDS.md#8-payload-servo-and-opencv-integration-test).

## AUTO Speed Ownership

Do not run a companion `MAV_CMD_DO_CHANGE_SPEED` test for this project. Mission
Planner owns AUTO speed, altitude, and waypoint routing.

## Motor Test

Avoid this until heartbeat, modes, and servo tests are already correct.

Props off is mandatory. The script refuses more than 15 percent throttle.

```bash
./scripts/mavlink_bench.sh motor-test \
  --connection /dev/ttyAMA0 \
  --baud 921600 \
  --motor 1 \
  --throttle-percent 5 \
  --duration 1 \
  --i-understand-props-off \
  --i-accept-motor-spin
```

Do not run motor tests on a fully assembled aircraft with propellers mounted.

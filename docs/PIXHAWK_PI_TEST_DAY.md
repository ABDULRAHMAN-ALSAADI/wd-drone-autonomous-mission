# Pixhawk + Raspberry Pi 5 Test Day

Use this when the Cube Orange and Raspberry Pi 5 are connected for bench tests
before the full aircraft is assembled.

## Today: Finish Before Leaving

Run from the Ubuntu laptop:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/pi_validate.sh
./scripts/pi_test_day_readiness.sh
./scripts/pi_cache_wheels.sh
./scripts/sync_to_pi.sh
```

`.wheelhouse/` is only a Pi-side offline package cache. It is ignored by Git.

Run from the Pi when the Cube is connected:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/mavlink_bench.sh status --connection /dev/serial0 --baud 921600 --seconds 10
./scripts/mavlink_bench.sh health --connection /dev/serial0 --baud 921600 --seconds 10
./scripts/mavlink_bench.sh set-mode GUIDED --connection /dev/serial0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode AUTO --connection /dev/serial0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode STABILIZE --connection /dev/serial0 --baud 921600 --observe 5
```

Good result:

```text
[HEARTBEAT] src=1:1 ...
[CONFIRMED] requested mode=... actual=...
```

Ignored heartbeats from `255:190` or `1:0` are normal when another MAVLink
endpoint is present. The real Cube vehicle heartbeat is `src=1:1`.

## Tomorrow Hardware Order

1. Remove propellers.
2. Mount Cube, power module, Pi, and wiring securely.
3. Connect Cube TELEM TX to Pi GPIO15 RXD.
4. Connect Cube TELEM RX to Pi GPIO14 TXD.
5. Connect Cube ground to Pi ground.
6. Power the Pi from its own stable supply.
7. Power the Cube.
8. Confirm Pi temperature and throttling:

```bash
vcgencmd measure_temp
vcgencmd get_throttled
```

9. Confirm MAVLink:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/mavlink_bench.sh status --connection /dev/serial0 --baud 921600 --seconds 10
./scripts/mavlink_bench.sh health --connection /dev/serial0 --baud 921600 --seconds 10
```

10. Confirm modes:

```bash
./scripts/mavlink_bench.sh set-mode STABILIZE --connection /dev/serial0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode GUIDED --connection /dev/serial0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode AUTO --connection /dev/serial0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode STABILIZE --connection /dev/serial0 --baud 921600 --observe 5
```

## Servo Or Payload Output Test

Do this before any motor test. Keep the payload mechanism disconnected first if
the channel is uncertain.

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

If the wrong output moves, stop and fix the channel mapping before continuing.

## Motor Test

Only run this when all of these are true:

- Propellers are removed.
- Battery and ESC wiring have been inspected.
- The aircraft is physically restrained on the bench.
- Everyone near the aircraft knows a motor test is about to run.
- MAVLink heartbeat and mode tests already passed.

Start with one motor, low throttle, short duration:

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

Then test motors `2`, `3`, and `4` one at a time. Do not exceed 5 percent for
the first round. The script refuses values above 15 percent.

## Mission Software Boundary

For the first full-system test, keep:

```text
target_mission_v2/configs/real_pi_camera_module_3.json
payload.simulate_only = true
control.altitude_control = "off"
navigation.search_speed_source = "qgc_mission"
```

That means QGC/ArduPilot owns AUTO altitude and AUTO speed, and the companion
only tests mission supervision, vision, and guided centering logic.

Enable physical payload only after mode, servo, camera, and simulated mission
tests are clean.

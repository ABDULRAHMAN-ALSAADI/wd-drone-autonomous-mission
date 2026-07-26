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
./test_components/mavlink/status.sh
./test_components/mavlink/health.sh
./scripts/mavlink_bench.sh set-mode GUIDED --connection /dev/ttyAMA0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode AUTO --connection /dev/ttyAMA0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode STABILIZE --connection /dev/ttyAMA0 --baud 921600 --observe 5
./test_components/mavlink/bench_sequence.sh --dry-run
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
./test_components/mavlink/status.sh
./test_components/mavlink/health.sh
```

10. Confirm modes:

```bash
./scripts/mavlink_bench.sh set-mode STABILIZE --connection /dev/ttyAMA0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode GUIDED --connection /dev/ttyAMA0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode AUTO --connection /dev/ttyAMA0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode STABILIZE --connection /dev/ttyAMA0 --baud 921600 --observe 5
```

11. Confirm the guarded full command sequence in dry-run mode:

```bash
./test_components/mavlink/bench_sequence.sh --dry-run
```

12. Only with propellers removed and the aircraft secured, run the guarded
mode/arm sequence:

```bash
./test_components/mavlink/bench_sequence.sh --i-understand-props-off --i-accept-arming
```

The sequence requests:

```text
STABILIZE -> GUIDED -> ARM -> AUTO -> RTL -> STABILIZE -> DISARM
```

It does not force arming. If pre-arm checks, GPS, EKF, RC mode switch, or AUTO
mission requirements are not satisfied, ArduPilot should reject the step. Treat
that as useful bench information, not as a reason to bypass safety.

## Camera Live Vision

The laptop viewer does not start the Pi camera server. Start a camera-only test,
the integrated OpenCV/servo test, or the real mission on the Pi first. For a
camera-only stream, run on the Pi:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/camera/opencv_test.sh live \
  --mode full \
  --headless \
  --stream \
  --stream-bind 127.0.0.1
```

Wait for `[STREAM]`, then run this from the Ubuntu laptop:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/open_laptop_camera_window.sh
```

The laptop creates an SSH tunnel and displays the annotated stream produced on
the Pi. Press `q` or Esc to quit and `s` to save a snapshot. A connection
refusal means the Pi producer is not running or exited with an earlier error.

## Servo And OpenCV Payload Test

Do this before any motor test. Keep the payload mechanism disconnected first if
the channel is uncertain.

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

These read the tested mapping from the Mission 2 profile: red triangle uses
`1300 -> 1500`, and blue hexagon uses `1700 -> 1500`, with a three-second hold.
Channel 5 means MAIN OUT 5. If the wrong output moves, stop and fix the channel
mapping before continuing.

Then follow the complete camera-recognition-servo procedure in
[`test_components/COMMANDS.md`](../test_components/COMMANDS.md#8-payload-servo-and-opencv-integration-test).

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
  --connection /dev/ttyAMA0 \
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
real_mission/parameter_config/mission2_target_payload.json
payload.simulate_only = true
control.altitude_control = "off"
navigation.search_speed_source = "qgc_mission"
```

That means QGC/ArduPilot owns AUTO altitude and AUTO speed, and the companion
only tests mission supervision, vision, and guided centering logic.

Enable physical payload only after mode, servo, camera, and simulated mission
tests are clean.

# Component Test Commands

Run these before trusting the real mission on the aircraft.

## 1. Software Checks

Run on the Ubuntu laptop:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/software/run_all_checks.sh
```

This tests Python syntax, configs, vision rejection rules, centering state logic,
and the read-only observer tests.

## 2. Sync Laptop Code To Pi

Run on the Ubuntu laptop:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
DRY_RUN=1 ./scripts/sync_to_pi.sh
./scripts/sync_to_pi.sh
```

The dry run shows what will copy. The real sync copies code to the Pi without
deleting Pi files.

## 3. Pi Camera Health Without Window

Run on the Raspberry Pi:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/camera/check_on_pi.sh --seconds 10
```

This checks Camera Module 3 FPS and saves a preview image. It does not open a
window because Pi OS Lite has no desktop.

Good result:

```text
[CAMERA OK] frames=... avg_fps=...
```

## 4. Laptop Live Camera Window

Run on the Ubuntu laptop, not inside `ssh pi5`:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/camera/live_from_laptop.sh
```

This opens the real live OpenCV window and runs the mission detector overlay.
The laptop and Pi must be on the same Wi-Fi/hotspot network. RFD900x does not
carry video.

Keys:

- `q` or Esc: quit;
- `s`: save snapshot;
- `m`: toggle red/blue mask windows.

## 5. MAVLink Heartbeat And Health

Run on the Raspberry Pi after Cube TELEM is wired:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/mavlink/status.sh
./test_components/mavlink/health.sh
```

This proves the Pi can read the Cube through `/dev/serial0` at `921600`.

Good result:

```text
VEHICLE_HEARTBEAT src=1:1 mode=... armed=...
[HEALTH]
```

The Cube/Pixhawk autopilot heartbeat is `src=1:1`. If you need to debug every
MAVLink participant on the wire, run:

```bash
./scripts/mavlink_bench.sh status --connection /dev/serial0 --baud 921600 --seconds 10 --all-heartbeats
```

Extra heartbeats such as `src=255:190` are usually Mission Planner/GCS, and
`src=1:0` is not the autopilot component used for mode/arm confirmation.

## 6. Mode Authority Checks

Run on the Raspberry Pi:

```bash
./scripts/mavlink_bench.sh set-mode STABILIZE --connection /dev/serial0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode GUIDED --connection /dev/serial0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode AUTO --connection /dev/serial0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode RTL --connection /dev/serial0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode STABILIZE --connection /dev/serial0 --baud 921600 --observe 5
```

If a requested mode immediately snaps to another mode, the Pi link works but
another authority is winning. Check RC flight-mode switch, Mission Planner/QGC,
failsafe conditions, and whether AUTO has a valid mission.

## 7. Avionics Bench Sequence

This is the requested sequence:

```text
STABILIZE -> GUIDED -> ARM -> AUTO -> RTL -> STABILIZE -> DISARM
```

Dry run first:

```bash
./test_components/mavlink/bench_sequence.sh --dry-run
```

Real run only with propellers removed and the airframe secured:

```bash
./test_components/mavlink/bench_sequence.sh --i-understand-props-off --i-accept-arming
```

This tests whether the Pi can request modes and arm/disarm through MAVLink. It
does not bypass ArduPilot pre-arm checks.

## 8. Payload Servo Test

Run only with propellers removed. Disconnect the payload mechanism first if the
channel is uncertain.

```bash
./test_components/mavlink/servo_payload_test.sh
```

This sends `release_pwm`, waits, then sends `reset_pwm` on the configured servo
channel. Your real config currently targets Pixhawk MAIN OUT / signal 5. If the
wrong output moves, stop and fix Mission Planner servo mapping.

## 9. Motor Test

Run only with propellers removed, the airframe restrained, and everyone warned.

If a command spins the wrong physical motor, stop and read:

```text
test_components/mavlink/MOTOR_MAPPING.md
```

```bash
./test_components/mavlink/motor_test.sh --motor 1 --throttle-percent 5 --duration 1
```

Test one motor at a time. Do not increase throttle until direction and mapping
are correct.

## 10. Real Mission Smoke Test

Keep payload simulated first:

```json
"payload": {
  "simulate_only": true
}
```

Then run on the Raspberry Pi:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/run_real_mission.sh
```

The script waits for AUTO and `mission.search_start_wp`. It should not start
searching while the Cube is disarmed or before the configured waypoint.

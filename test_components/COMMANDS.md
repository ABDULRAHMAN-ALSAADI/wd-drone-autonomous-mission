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

For the full read-only Pi preflight check:

```bash
./test_components/preflight/full_check.sh
```

Use skips when hardware is not connected:

```bash
SKIP_CAMERA=1 SKIP_MAVLINK=1 ./test_components/preflight/full_check.sh
```

## 2. Sync Laptop Code To Pi

Run on the Ubuntu laptop:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
DRY_RUN=1 ./scripts/sync_to_pi.sh
./scripts/sync_to_pi.sh
```

The dry run shows what will copy. The real sync copies code to the Pi without
deleting Pi files.

## 3. OpenCV Camera Module 3 Tests

The OpenCV-only mission uses direct Picamera2 arrays. Run on the Raspberry Pi:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/camera/opencv_test.sh list
./test_components/camera/opencv_test.sh diagnostic --seconds 20
./test_components/camera/opencv_test.sh live --mode full --headless --stream --stream-bind 127.0.0.1
```

Open the annotated stream from a second terminal on the Ubuntu laptop:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/open_laptop_camera_window.sh
```

These camera-only commands never connect to MAVLink. The complete focus,
benchmark, replay, and image-capture workflow is in:

```text
docs/OPENCV_PI5_TESTING.md
```

## 3A. Legacy Camera And Hailo Hardware Status

Run on the Ubuntu laptop:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/camera/pi_camera_hailo_status.sh
```

This is read-only. It checks Pi temperature, throttling, camera boot overlay,
detected `rpicam` cameras, video devices, Hailo PCIe visibility, and HailoRT
installation state.

## 4. Legacy MJPEG Camera Health Without Window

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

## 5. Legacy Laptop-Side OpenCV Window

Run on the Ubuntu laptop, not inside `ssh pi5`:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/camera/live_from_laptop.sh
```

This compatibility tool streams raw MJPEG from the Pi and runs OpenCV on the
laptop. Prefer the direct Picamera2 test in section 3, where OpenCV runs on the
Pi and only annotated monitoring frames cross the network.
The laptop and Pi must be on the same Wi-Fi/hotspot network. RFD900x does not
carry video.

Keys:

- `q` or Esc: quit;
- `s`: save snapshot;
- `m`: toggle red/blue mask windows.

## 6. MAVLink Heartbeat And Health

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

## 7. Mode Authority Checks

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

## 6A. RC Switch / Mission Enable Mapping

Run on the Raspberry Pi with the transmitter on:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/mavlink/rc_channels.sh --seconds 30 --channels 5 6 7 8
```

Flip one AT9S Pro switch at a time. The channel marked with `*` is the channel
that changed. Use this to choose the optional Mission 2 search-enable switch.

Do not guess the channel. If you choose the wrong channel, Mission 2 search may
stay blocked or may become enabled at the wrong time.

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
./test_components/mavlink/servo_payload_test.sh \
  --i-understand-props-off \
  --i-accept-servo-motion
```

This sends `release_pwm`, waits, then sends `reset_pwm` on the configured servo
channel. Your real config currently targets Pixhawk MAIN OUT / signal 5. If the
wrong output moves, stop and fix Mission Planner servo mapping. The tool refuses
to run while the Cube reports armed.

## 8A. GUIDED Body-Velocity Command Test

Run the dry run anywhere first:

```bash
./test_components/mavlink/guided_velocity_props_off.sh --dry-run
```

The real bench test requires propellers removed, the payload mechanism disabled,
and the vehicle disarmed:

```bash
./test_components/mavlink/guided_velocity_props_off.sh \
  --i-understand-props-off \
  --payload-disabled
```

It requests GUIDED while disarmed, streams `0.2 m/s` body-frame commands in the
order forward, backward, right, and left, sends zero between directions, and
restores the original mode. It refuses to run if the Cube reports armed and
aborts if the mode changes or heartbeat becomes stale. This proves that the Pi
sends the same MAVLink message layout as mission centering. It cannot prove the
aircraft's physical direction or motor mixing; those require SITL and a later
controlled open-field test with a safety pilot.

## 9. Motor Test

Run only with propellers removed, the airframe restrained, and everyone warned.

If a command spins the wrong physical motor, stop and read:

```text
test_components/mavlink/MOTOR_MAPPING.md
```

```bash
./test_components/mavlink/motor_test.sh \
  --motor 1 \
  --throttle-percent 5 \
  --duration 1 \
  --i-understand-props-off \
  --i-accept-motor-spin
```

Test one motor at a time. Do not increase throttle until direction and mapping
are correct. The tool refuses to run while the Cube reports armed.

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
./real_mission/run_mission2_target_payload.sh
```

The script waits for AUTO, `mission.search_enabled=true`, and
`mission.search_start_wp`. It should not start searching while the Cube is
disarmed, while the Mission 1 no-search profile is running, or before the
configured waypoint.

During an active target, the real config keeps the target lock through
`GUIDED -> AUTO` bounces and keeps requesting GUIDED. It should not RTL just
because AUTO appears briefly during centering.

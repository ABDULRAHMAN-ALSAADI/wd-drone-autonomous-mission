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

This proves the Pi can read the Cube through the tested Raspberry Pi 5 UART,
`/dev/ttyAMA0`, at `921600`. On the project Pi, `/dev/serial0` points to
`/dev/ttyAMA10` and is not the Cube connection. The wrappers allow
`MAVLINK_CONNECTION` and `MAVLINK_BAUD` overrides for a differently configured
Pi.

Good result:

```text
VEHICLE_HEARTBEAT src=1:1 mode=... armed=...
[HEALTH]
```

The Cube/Pixhawk autopilot heartbeat is `src=1:1`. If you need to debug every
MAVLink participant on the wire, run:

```bash
./scripts/mavlink_bench.sh status --connection /dev/ttyAMA0 --baud 921600 --seconds 10 --all-heartbeats
```

Extra heartbeats such as `src=255:190` are usually Mission Planner/GCS, and
`src=1:0` is not the autopilot component used for mode/arm confirmation.

## 7. Mode Authority Checks

Run on the Raspberry Pi:

```bash
./scripts/mavlink_bench.sh set-mode STABILIZE --connection /dev/ttyAMA0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode GUIDED --connection /dev/ttyAMA0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode AUTO --connection /dev/ttyAMA0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode RTL --connection /dev/ttyAMA0 --baud 921600 --observe 5
./scripts/mavlink_bench.sh set-mode STABILIZE --connection /dev/ttyAMA0 --baud 921600 --observe 5
```

If a requested mode immediately snaps to another mode, the Pi link works but
another authority is winning. Check RC flight-mode switch, Mission Planner/QGC,
failsafe conditions, and whether AUTO has a valid mission.

## 7A. RC Switch / Mission Enable Mapping

Run on the Raspberry Pi with the transmitter on:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/mavlink/rc_channels.sh --seconds 30 --channels 5 6 7 8
```

Flip one AT9S Pro switch at a time. The channel marked with `*` is the channel
that changed. Use this to choose the optional Mission 2 search-enable switch.

Do not guess the channel. If you choose the wrong channel, Mission 2 search may
stay blocked or may become enabled at the wrong time.

## 7B. Avionics Bench Sequence

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

## 8. Payload Servo And OpenCV Integration Test

Run only with propellers removed. Disconnect the payload mechanism first if the
channel is uncertain.

Red-triangle / blue-payload selector test (`1300 -> 1500`):

```bash
./test_components/mavlink/servo_payload_test.sh \
  --target red_triangle \
  --i-understand-props-off \
  --i-accept-servo-motion
```

Blue-hexagon / red-payload selector test (`1700 -> 1500`):

```bash
./test_components/mavlink/servo_payload_test.sh \
  --target blue_hexagon \
  --i-understand-props-off \
  --i-accept-servo-motion
```

Each test holds the selected position for the configured three seconds and
returns the servo to neutral. Your real config targets Pixhawk MAIN OUT /
signal 5. If the wrong output moves, stop and fix Mission Planner servo
mapping. The tool refuses to run while the Cube reports armed.

### Complete Two-Terminal Test

This is the tested team procedure for strict OpenCV plus the physical selector
servo. Before starting:

- remove every propeller;
- secure the airframe and keep the vehicle disarmed;
- clear the payload/servo movement area;
- power the Cube and wait for it to finish booting;
- power the Pi and put the Pi and laptop on the same Wi-Fi, hotspot, or
  Ethernet network;
- configure the laptop SSH alias `pi5` as shown in
  [Beginner Guide section 6](../docs/BEGINNER_GUIDE.md#6-prepare-the-raspberry-pi-5);
- close any other program using the Pi camera or Cube UART.

On the Ubuntu laptop, confirm that the alias works before starting:

```bash
ssh pi5 true
```

If your team deliberately uses another alias, pass it to the laptop viewer as
`./real_mission/open_laptop_camera_window.sh --ssh-alias YOUR_ALIAS`.

First, on the Raspberry Pi, verify the powered Cube:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
source .venv/bin/activate
SECONDS_TO_RUN=3 ./test_components/mavlink/status.sh
```

Do not continue unless the output contains a vehicle heartbeat such as:

```text
[HEARTBEAT] src=1:1 mode=STABILIZE armed=False
```

Start the integrated test in that same Pi terminal:

```bash
./test_components/mavlink/opencv_servo_payload_test.sh \
  --i-understand-props-off \
  --i-accept-servo-motion \
  --i-confirm-payload-zone-clear
```

Do not add a target name or PWM values for the normal test. They come from the
reviewed Mission 2 profile. Wait until the Pi prints:

```text
[STREAM] http://127.0.0.1:5602/stream.mjpg
```

Then open a second terminal on the Ubuntu laptop:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/open_laptop_camera_window.sh
```

Present the targets one at a time in either order and move each into the
on-screen center marker:

| Detected target | Selector action |
| --- | --- |
| Red triangle | `1300` PWM for 3 seconds, then neutral `1500` |
| Blue hexagon | `1700` PWM for 3 seconds, then neutral `1500` |

The detector automatically accepts the red triangle and blue hexagon in either
order during the same run. Each target can trigger only once. Connection, baud,
servo channel, target PWM, neutral PWM, and hold time come from the real Mission
2 profile. Before starting vision, the tool commands and confirms neutral
`1500` PWM. It also sends neutral on normal exit, timeout, `Ctrl+C`, and error
paths. Servo ACK waits and the configured three-second hold run without blocking
camera processing, so the laptop feed remains live while the selector moves.
After both targets trigger once, the video remains open in monitoring mode until
you press `Ctrl+C`; shutdown commands neutral `1500` before closing the stream.
If an ACK is lost or rejected, the tool commands neutral, shows a latched fault,
keeps retrying neutral until the Cube accepts it, and leaves the monitoring
stream open instead of attempting another release.
The bench tool sends no arm, mode, motor, or velocity commands.

Press `q` or Esc to close only the laptop viewer. Press `Ctrl+C` in the Pi
terminal to end the hardware test; shutdown commands neutral before closing the
stream.

### Troubleshooting

`No vehicle heartbeat received`:

- confirm the Cube is powered and fully booted;
- confirm the vehicle is disarmed;
- confirm Cube TELEM TX/RX/GND are connected to Pi RX/TX/GND;
- confirm the Cube port and Pi use `921600` baud;
- run `ls -l /dev/serial0`; on the tested Pi the correct Cube device is
  `/dev/ttyAMA0`, not the `/dev/serial0 -> ttyAMA10` alias;
- make sure no other mission or MAVLink tool owns the UART.

Laptop `channel 1: open failed` or `Connection refused`:

- read the Pi terminal first—the Pi test exited before opening the stream;
- fix its camera, dependency, or heartbeat error;
- start the laptop viewer only after `[STREAM]` appears.

`ModuleNotFoundError: pymavlink`:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/setup.sh
source .venv/bin/activate
```

Wrong physical output or selector direction:

- stop immediately;
- keep the props off;
- verify MAIN OUT 5, `SERVO5_FUNCTION`, and the mechanical selector mapping;
- do not compensate with random PWM values on the command line.

The Cube's `COMMAND_ACK` confirms command acceptance, not that a physical
payload left the aircraft. Record every hardware run in
[`docs/HARDWARE_TEST_LOG.md`](../docs/HARDWARE_TEST_LOG.md).

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
disarmed or before the configured waypoint.

During an active target, the real config keeps the target lock through
`GUIDED -> AUTO` bounces and keeps requesting GUIDED. It should not RTL just
because AUTO appears briefly during centering.

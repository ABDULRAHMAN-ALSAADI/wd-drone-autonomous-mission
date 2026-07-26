# WD Drone Autonomous Mission

[![Tests](https://github.com/ABDULRAHMAN-ALSAADI/wd-drone-autonomous-mission/actions/workflows/tests.yml/badge.svg)](https://github.com/ABDULRAHMAN-ALSAADI/wd-drone-autonomous-mission/actions/workflows/tests.yml)
[![Python 3](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![ArduPilot](https://img.shields.io/badge/Autopilot-ArduPilot-orange.svg)](https://ardupilot.org/)
[![Raspberry Pi 5](https://img.shields.io/badge/Companion-Raspberry%20Pi%205-c51a4a.svg)](https://www.raspberrypi.com/products/raspberry-pi-5/)

Open, testable companion-computer software for an autonomous rotary-wing UAV.
The project combines ArduPilot, a Raspberry Pi 5, MAVLink, and computer vision
to find ground targets, center above them, and operate a payload mechanism.

This repository was built for the 2026 UAV competition, but its simulation,
vision, safety, and hardware-test tools are designed so future teams can study
and improve them.

> [!CAUTION]
> This software can command a real aircraft. Start with unit tests and
> simulation. Remove propellers for all hardware bench tests. A successful
> software test does not make an aircraft safe to fly.

## Start Here

Choose the path that matches what you want to do:

| I want to… | Read or run this |
| --- | --- |
| Understand the project in five minutes | Continue with **How It Works** below |
| Set up a new computer from zero | [Beginner Guide](docs/BEGINNER_GUIDE.md) |
| Run a quick local software check | [Quick Start](#quick-start-software-only) |
| Run the Gazebo + ArduPilot simulation | [Simulation Guide](simulation/README.md) |
| Prepare Raspberry Pi and flight hardware | [Real Mission Guide](real_mission/README.md) |
| Test one camera, MAVLink, servo, or motor component | [Component Test Commands](test_components/COMMANDS.md) |
| Repeat the tested OpenCV + servo bench procedure | [Two-Terminal Hardware Test](test_components/COMMANDS.md#complete-two-terminal-test) |
| Run the first real companion test | [First Real Companion Test](#first-real-test-for-the-companion-on-the-drone) |
| Decide what remains before flight | [Real Drone And First-Flight Checklist](docs/REAL_DRONE_CHECKLIST.md) |
| See what was physically verified | [Hardware Test Log](docs/HARDWARE_TEST_LOG.md) |
| Understand the code | [Architecture](docs/ARCHITECTURE.md) and [Project Structure](docs/PROJECT_STRUCTURE.md) |
| Contribute a change | [Contributing Guide](CONTRIBUTING.md) |

## How It Works

ArduPilot remains responsible for the normal AUTO route, altitude, speed, and
flight failsafes. The Raspberry Pi companion process starts searching only when
the configured mission conditions are met.

```text
ArduPilot AUTO survey
        ↓
Pi detects and confirms a target
        ↓
Pi requests GUIDED and sends bounded horizontal centering commands
        ↓
Fresh full-resolution geometry and safety checks pass
        ↓
Payload is simulated or released
        ↓
AUTO resumes for target two, then RTL is requested
```

The OpenCV mission looks for:

- a **blue hexagon**, which receives the red payload;
- a **red triangle**, which receives the blue payload.

Read [Real Mission Flow](docs/REAL_MISSION_FLOW.md) for the complete state
machine and operator responsibilities.

## Quick Start: Software Only

The supported beginner environment is Ubuntu. These commands do not connect to
an aircraft:

```bash
git clone https://github.com/ABDULRAHMAN-ALSAADI/wd-drone-autonomous-mission.git
cd wd-drone-autonomous-mission
./scripts/setup.sh
source .venv/bin/activate
./scripts/check_project.sh
```

A successful check ends with all unit tests passing and all Python files
compiling. Camera, Gazebo, Raspberry Pi, and flight-controller tests are
separate because they need additional hardware or software.

## Project Map

| Path | What belongs here |
| --- | --- |
| [`real_mission/`](real_mission/) | Real-aircraft launch commands and parameter profiles |
| [`target_mission_v2/`](target_mission_v2/) | Mission state machine, camera sources, OpenCV detector, configuration validation, and payload mapping |
| [`simulation/`](simulation/) | Gazebo and ArduPilot SITL setup and launch scripts |
| [`test_components/`](test_components/) | Guarded camera, MAVLink, servo, motor, and preflight checks |
| [`tools/`](tools/) | Lower-level diagnostic and replay utilities |
| [`tests/`](tests/) | Tests for the observer and shared tools |
| [`docs/`](docs/) | Architecture, safety, operations, calibration, and roadmap documents |

The [`main`](https://github.com/ABDULRAHMAN-ALSAADI/wd-drone-autonomous-mission/tree/main)
branch is the OpenCV reference implementation. The
[`yolov8-mission-pi5`](https://github.com/ABDULRAHMAN-ALSAADI/wd-drone-autonomous-mission/tree/yolov8-mission-pi5)
branch contains the YOLOv8/Hailo candidate-detection implementation.

## Real Hardware

Do not jump from cloning the repository to flight. Use this progression:

1. Run `./scripts/check_project.sh`.
2. Test in Gazebo and ArduPilot SITL.
3. Review [Safety and Failsafes](docs/SAFETY_AND_FAILSAFES.md).
4. Run read-only Raspberry Pi and MAVLink checks.
5. Run guarded propeller-off bench tests.
6. Close every gate in the
   [Real Drone And First-Flight Checklist](docs/REAL_DRONE_CHECKLIST.md).
7. Use a team-reviewed flight-test plan with a competent safety pilot.

Physical payload output is disabled by default:

```json
"payload": {
  "simulate_only": true
}
```

The project does not replace airframe inspection, correct ArduPilot setup,
range checks, GPS/compass checks, tested RC recovery modes, legal compliance,
or pilot judgment.

## First Real Test For The Companion On The Drone

This is the first **companion-controlled** test, not the aircraft's first
flight. First complete a stable hover, Loiter, RTL, and small AUTO route without
companion control, then close every item in the
[first-flight checklist](docs/REAL_DRONE_CHECKLIST.md).

> [!CAUTION]
> Commands in step 1 are **PROPS OFF**. Run them one at a time with the
> aircraft secured and disarmed. For the flight, `payload.simulate_only=true`
> means the servo will not move, but the aircraft and GUIDED centering are real.

### 1. Props-Off Checks — Run On The Raspberry Pi

Power the Cube, Pi, and camera. Close every other camera or MAVLink program.
Before the mode/arm test, upload the small reviewed AUTO route described in
step 2:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
source .venv/bin/activate

# Software, camera, Cube heartbeat, health, and config
SECONDS_TO_RUN=10 ./test_components/preflight/full_check.sh

# Identify the tested RC recovery switch
./test_components/mavlink/rc_channels.sh --seconds 30 --channels 5 6 7 8

# Test mode/arm authority; this must finish STABILIZE and DISARMED
./test_components/mavlink/bench_sequence.sh --dry-run
./test_components/mavlink/bench_sequence.sh \
  --i-understand-props-off \
  --i-accept-arming

# Physically disable the payload, then test body-velocity MAVLink while disarmed
./test_components/mavlink/guided_velocity_props_off.sh \
  --i-understand-props-off \
  --payload-disabled

# Reconnect the payload and repeat the tested vision/selector sequence
./test_components/mavlink/opencv_servo_payload_test.sh \
  --i-understand-props-off \
  --i-accept-servo-motion \
  --i-confirm-payload-zone-clear
```

Present both targets, press `Ctrl+C` after both pass, and confirm the selector
returns to neutral `1500`.

Test motors separately. Repeat this command with `--motor 1`, `2`, `3`, and
`4`; confirm the documented physical motor and rotation direction each time:

```bash
./test_components/mavlink/motor_test.sh \
  --motor 1 \
  --throttle-percent 5 \
  --duration 1 \
  --i-understand-props-off \
  --i-accept-motor-spin
```

`[CHECKS COMPLETE]` is not flight clearance. Stop for any failure, warning,
missing health value (`-`), pre-arm error, wrong motor, wrong mode, or wrong
servo movement.

### 2. Prepare The Flight

- Power down before installing the correct propellers.
- In Mission Planner/QGC, review the uploaded small real-field AUTO mission.
  This repository contains no ready-to-fly real-field route.
- Confirm home, fence, RTL altitude, waypoint altitude/speed, target visibility
  at the real height, and that the first search-area item is MAVLink mission
  sequence `2`.
- Keep `payload.simulate_only=true`.
- Verify battery, 3D GPS, EKF, compass, position, RC failsafes, and the pilot's
  non-AUTO/non-GUIDED recovery mode.

The current profile has no RC search-enable gate and does not require battery
or EKF messages; centering also has no automatic time limit. Therefore the
ground team must verify those items and the safety pilot must be ready to select
Loiter, Stabilize, RTL, or Land. Selecting one of those modes makes the
companion stand down until disarm.

### 3. Test In Two Stages

For the first companion flight, use the no-search profile:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/run_mission1_no_search.sh
```

Confirm `MISSION PROFILE: mission1_figure8_no_search` and
`SEARCH GATE: blocked`. Fly only the already-tested small AUTO route, then land
and review the logs.

Next, place **one target** in the field and run Mission 2. The Pi waits for the
aircraft to be armed, in AUTO, and at mission sequence `2`. It then confirms
the target, requests GUIDED, centers horizontally at no more than `0.35 m/s`,
logs `PAYLOAD_SIMULATED`, and resumes AUTO. Altitude and AUTO speed remain
owned by ArduPilot. After the first target, use the tested recovery mode, land,
and review logs before repeating with both targets. After both targets pass,
the companion requests RTL.

Prepare the optional laptop viewer in a second terminal; run it only after the
Pi prints `[VIDEO STREAM]`:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/open_laptop_camera_window.sh
```

Finally, start Mission 2 on the Raspberry Pi while disarmed in `STABILIZE`.
Do not arm until it prints the Cube connection, Picamera2 camera, video-stream
address, and `MISSION PROFILE: mission2_target_payload`:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/run_mission2_target_payload.sh
```

## Testing

Run the same project check used by continuous integration:

```bash
./scripts/check_project.sh
```

When mission control, MAVLink, camera, or vision behavior changes, also run the
relevant SITL scenario. Hardware behavior must be reported separately in a pull
request; unit tests cannot prove real-flight readiness.

## Contributing

Beginner contributions are welcome. Documentation corrections, reproducible bug
reports, simulation scenarios, tests, and small focused improvements are good
places to start.

Please read [CONTRIBUTING.md](CONTRIBUTING.md), open an issue for large or
safety-critical changes, and use a feature branch. Never include secrets, logs,
private flight data, camera dumps, or large model files in a pull request.

See the [Roadmap](docs/ROADMAP.md) for future work.

## License

An open-source license has not been selected yet. Public visibility alone does
not grant permission to copy, modify, or redistribute the project. A `LICENSE`
file should be added before inviting broad reuse.

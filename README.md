# WD Drone Autonomous Mission

[![Tests](https://github.com/ABDULRAHMAN-ALSAADI/wd-drone-autonomous-mission/actions/workflows/tests.yml/badge.svg)](https://github.com/ABDULRAHMAN-ALSAADI/wd-drone-autonomous-mission/actions/workflows/tests.yml)
[![Python 3](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![ArduPilot](https://img.shields.io/badge/Autopilot-ArduPilot-orange.svg)](https://ardupilot.org/)
[![Raspberry Pi 5](https://img.shields.io/badge/Companion-Raspberry%20Pi%205-c51a4a.svg)](https://www.raspberrypi.com/products/raspberry-pi-5/)

Open, testable companion-computer software for an autonomous rotary-wing UAV.
The project combines ArduPilot, a Raspberry Pi 5, MAVLink, and computer vision
to find ground targets, center above them, and operate a payload mechanism.
This repository and the Raspberry Pi controller are for **Mission 2 only**.

This repository was built for the 2026 UAV competition, but its simulation,
vision, safety, and hardware-test tools are designed so future teams can study
and improve them.

This is the **OpenCV-only Mission 2 implementation**. It contains no trained
model, model compiler, or neural-network inference runtime.

> [!CAUTION]
> This software can command a real aircraft. Start with unit tests and
> simulation. Remove propellers for all hardware bench tests. A successful
> software test does not make an aircraft safe to fly.

## Mission Demonstration

[![Watch the WD Drone Mission 2 demonstration](https://img.youtube.com/vi/QQ8b78xBOo0/maxresdefault.jpg)](https://youtu.be/QQ8b78xBOo0)

[Watch the Mission 2 system demonstration on YouTube](https://youtu.be/QQ8b78xBOo0).
The video shows the project architecture and simulated autonomous mission flow.

## Start Here

Choose the path that matches what you want to do:

| I want to… | Read or run this |
| --- | --- |
| Prepare a new Ubuntu laptop | [New Ubuntu Laptop Setup](UBUNTU_SETUP.txt) |
| Understand the project in five minutes | Continue with **How It Works** below |
| Set up a new computer from zero | [Beginner Guide](docs/BEGINNER_GUIDE.md) |
| Run a quick local software check | [Quick Start](#quick-start-software-only) |
| Run the Gazebo + ArduPilot simulation | [Simulation Guide](simulation/README.md) |
| Prepare Raspberry Pi and flight hardware | [Real Mission Guide](real_mission/README.md) |
| Keep every Mission 2 test command in one file | [Mission 2 Command Sheet](MISSION2_COMMANDS.txt) |
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
sudo apt update
sudo apt install -y git
mkdir -p ~/FOR_COMP
cd ~/FOR_COMP
git clone https://github.com/ABDULRAHMAN-ALSAADI/wd-drone-autonomous-mission.git
cd wd-drone-autonomous-mission
./scripts/setup_ubuntu.sh
```

The setup installs the Ubuntu packages, creates `.venv`, and runs all software
tests. A successful run ends with `[READY] Ubuntu development setup passed`.
Camera, Gazebo, Raspberry Pi, and flight-controller tests are separate because
they need additional hardware or software. See
[UBUNTU_SETUP.txt](UBUNTU_SETUP.txt) for daily Git commands and the safe
contribution workflow.

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
branch is the only supported Mission 2 implementation. Target authority comes
from strict OpenCV colour and geometric-shape verification across multiple
frames.

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

The reusable starting profile keeps physical payload output disabled:

```json
"payload": {
  "simulate_only": true
}
```

The operator launcher uses
`real_mission/parameter_config/mission2_target_payload.json`. That tested team
profile currently has physical output enabled for MAIN OUT 5 after the recorded
propeller-off servo checks. New users must change it back to
`"simulate_only": true` until their own mechanism and PWM mapping have passed
the documented bench procedure.

The project does not replace airframe inspection, correct ArduPilot setup,
range checks, GPS/compass checks, tested RC recovery modes, legal compliance,
or pilot judgment.

## First Real Test For The Companion On The Drone

This is the first **Mission 2 companion-controlled** test, not the aircraft's
first flight. Complete the [first-flight checklist](docs/REAL_DRONE_CHECKLIST.md)
and every props-off test in [MISSION2_COMMANDS.txt](MISSION2_COMMANDS.txt)
before installing propellers.

Upload and review a small real-field AUTO route in Mission Planner/QGC. Confirm
home, fence, RTL, altitude, speed, RC recovery, and that the first search-area
item is MAVLink mission sequence `2`. Use payload simulation for an initial
aircraft test; the checked-in operator profile must not be used unchanged by a
new aircraft or payload mechanism.

Start with **one target**. The Pi waits for the aircraft to be armed, in AUTO,
and at mission sequence `2`. It then confirms
the target, requests GUIDED, centers horizontally at no more than `0.35 m/s`,
logs `PAYLOAD_SIMULATED`, and resumes AUTO. Altitude and AUTO speed remain
owned by ArduPilot. After the first target, use the tested recovery mode, land,
and review logs before repeating with both targets. After both targets pass,
the companion requests RTL. For any wrong behavior, the pilot must leave
AUTO/GUIDED using the tested RC recovery mode, land, and disarm.

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
private flight data, or camera dumps in a pull request.

See the [Roadmap](docs/ROADMAP.md) for future work.

## License

An open-source license has not been selected yet. Public visibility alone does
not grant permission to copy, modify, or redistribute the project. A `LICENSE`
file should be added before inviting broad reuse.

# Beginner Guide: From GitHub To A Working Test

This is the complete onboarding path for a new team member. Follow it in
order. Do not jump directly from cloning the code to a real flight.

## 1. Understand The Two Implementations

The same GitHub repository has two important branches:

| Branch | Local folder | Vision backend |
| --- | --- | --- |
| `main` | `wd-drone-autonomous-mission` | Strict OpenCV color and geometry |
| `yolov8-mission-pi5` | `YoloV8-autonmous-mission` | YOLOv8/Hailo proposals verified by OpenCV |

Both versions use the same mission idea:

```text
Mission Planner / ArduPilot
    owns AUTO route, takeoff, speed, altitude and normal navigation

Raspberry Pi mission process
    waits for the configured search waypoint
    detects and confirms a target
    requests GUIDED
    sends bounded horizontal velocity for centering
    attempts the configured payload command
    requests AUTO after target one
    requests RTL after target two
```

Mission 1 is different: it is an ArduPilot AUTO mission with search and
payload logic disabled.

## 2. Safety Boundary

Code that runs in simulation is not automatically safe for an aircraft.

- Remove propellers for every UART, mode, arm, servo and motor bench test.
- Keep `payload.simulate_only` set to `true` until the payload output has been
  checked with the mechanism secured.
- Never use the motor-test command as a flight command.
- The pilot must retain a tested RC recovery mode.
- Mission Planner must contain the correct mission and verified failsafes.
- Move from software tests to SITL, then propeller-off bench tests, then a
  controlled flight-test plan approved by the team.

The repository does not replace an airframe inspection, ArduPilot setup,
range test, compass/GPS checks, failsafe tests or a competent safety pilot.

## 3. Clone The Workspace

Install the basic Ubuntu tools:

```bash
sudo apt update
sudo apt install git openssh-client rsync python3-venv python3-pip python3-opencv python3-numpy gstreamer1.0-tools
```

Create the workspace and clone both branches:

```bash
mkdir -p ~/FOR_COMP
cd ~/FOR_COMP

git clone --branch main \
  https://github.com/ABDULRAHMAN-ALSAADI/wd-drone-autonomous-mission.git \
  wd-drone-autonomous-mission

git clone --branch yolov8-mission-pi5 \
  https://github.com/ABDULRAHMAN-ALSAADI/wd-drone-autonomous-mission.git \
  YoloV8-autonmous-mission
```

Private-repository users must authenticate with GitHub first. Team members
with SSH access can replace the HTTPS URL with:

```text
git@github.com:ABDULRAHMAN-ALSAADI/wd-drone-autonomous-mission.git
```

## 4. Prepare The OpenCV Version On Ubuntu

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/setup.sh
source .venv/bin/activate
./scripts/check_project.sh
```

The virtual environment contains architecture-specific packages. Never copy
this Ubuntu `.venv` to the Raspberry Pi.

Important entry points:

| Path | Purpose |
| --- | --- |
| `real_mission/` | Operator commands and real-aircraft profiles |
| `target_mission_v2/` | Mission controller, MAVLink control and OpenCV detector |
| `test_components/` | Camera, MAVLink, servo, motor and preflight tests |
| `simulation/` | Gazebo and ArduPilot SITL launchers |
| `docs/` | Architecture, operations and safety documents |

## 5. Prepare Gazebo And ArduPilot SITL

The repository contains the mission code and its project patch. ArduPilot and
`ardupilot_gazebo` remain separate upstream projects.

The current tested source revisions are:

```text
ArduPilot:         c1170033f4146db01056ef4d20ad5b4eca072b62
ardupilot_gazebo: 082a0fe231f6e63bc8d1598f1cba461d9e2ea7f5
```

Use the official ArduPilot Linux SITL setup instructions:

```text
https://ardupilot.org/dev/docs/setting-up-sitl-on-linux.html
```

Clone the tested source revisions:

```bash
git clone https://github.com/ArduPilot/ardupilot.git ~/ardupilot
cd ~/ardupilot
git checkout c1170033f4146db01056ef4d20ad5b4eca072b62
git submodule update --init --recursive
Tools/environment_install/install-prereqs-ubuntu.sh -y

git clone https://github.com/ArduPilot/ardupilot_gazebo.git ~/ardupilot_gazebo
cd ~/ardupilot_gazebo
git checkout 082a0fe231f6e63bc8d1598f1cba461d9e2ea7f5
```

Install Gazebo Harmonic and build `ardupilot_gazebo` by following its official
README:

```text
https://github.com/ArduPilot/ardupilot_gazebo
```

Typical plugin build after its dependencies are installed:

```bash
cd ~/ardupilot_gazebo
cmake -S . -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build -j4
```

Apply this project's target field and downward-camera patch:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./simulation/setup_external_assets.sh --check
./simulation/setup_external_assets.sh --apply
```

The script is idempotent: it reports `already applied` instead of applying the
patch twice.

Start the simulation in four Ubuntu terminals:

```bash
# Terminal 1
cd ~/FOR_COMP/wd-drone-autonomous-mission
./simulation/start_gazebo.sh

# Terminal 2
cd ~/FOR_COMP/wd-drone-autonomous-mission
./simulation/start_sitl.sh

# Terminal 3
cd ~/FOR_COMP/wd-drone-autonomous-mission
./simulation/enable_gazebo_camera.sh

# Terminal 4
cd ~/FOR_COMP/wd-drone-autonomous-mission
./simulation/run_target_mission.sh
```

Upload a SITL-only mission through Mission Planner/QGC or MAVProxy. Never use
an example simulation route on the real aircraft without rebuilding and
reviewing it for the actual field.

## 6. Prepare The Raspberry Pi 5

Use 64-bit Raspberry Pi OS. Install a cooler before sustained camera or Hailo
work. The Pi and laptop must share Ethernet, Wi-Fi or a hotspot for SSH and the
laptop video window.

Install the base packages on the Pi:

```bash
sudo apt update
sudo apt install git rsync python3-venv python3-pip python3-opencv python3-numpy rpicam-apps
```

Official camera documentation:

```text
https://www.raspberrypi.com/documentation/computers/camera_software.html
```

Configure an SSH alias on each team laptop in `~/.ssh/config`:

```text
Host pi5
    HostName raspberrypi5.local
    User pi5
    IdentityFile ~/.ssh/id_ed25519
```

Verify access:

```bash
ssh pi5 '
hostname
hostname -I
uname -m
cat /etc/os-release
vcgencmd measure_temp
vcgencmd get_throttled
df -h /
'
```

Expected architecture is `aarch64`. A clean throttling result is
`throttled=0x0`.

## 7. Deploy And Validate The OpenCV Version

The Ubuntu clone is the source of truth. Preview every synchronization:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
DRY_RUN=1 ./scripts/sync_to_pi.sh
./scripts/sync_to_pi.sh
./scripts/pi_validate.sh
```

The sync excludes virtual environments, logs, datasets, model weights and
secrets. It does not delete Pi files.

Read-only hardware report from the laptop:

```bash
./test_components/camera/pi_camera_hailo_status.sh
```

Camera test on the Pi:

```bash
ssh pi5
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/camera/check_on_pi.sh --seconds 10
```

Standalone OpenCV camera window on the laptop:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/camera/live_from_laptop.sh
```

Run the laptop command outside an SSH session. Raspberry Pi OS Lite has no
desktop, and direct X forwarding is not the supported camera path.

## 8. Prepare YOLOv8 And Hailo AI HAT+

Read the dedicated guide after cloning the YOLO branch:

```text
~/FOR_COMP/YoloV8-autonmous-mission/BEGINNER_GUIDE.md
```

For an AI HAT+, Raspberry Pi's official installation currently uses:

```bash
sudo apt install dkms
sudo apt install hailo-all
sudo reboot
```

After reboot:

```bash
hailortcli fw-control identify
rpicam-hello --list-cameras
```

Official AI HAT documentation:

```text
https://www.raspberrypi.com/documentation/accessories/ai-hat-plus.html
https://www.raspberrypi.com/documentation/computers/ai.html
```

The model artifacts are intentionally not stored in GitHub yet. Obtain the
team-approved files and put them at the YOLO project root:

```text
best.pt    Ubuntu Gazebo / Ultralytics test
best.onnx  Hailo compilation input
best.hef   Raspberry Pi Hailo runtime
```

Do not rename one format to another. ONNX-to-HEF is a real compilation and
quantization process documented in `hailo_compile/README.md`.

## 9. Test MAVLink Before A Mission

The configured real link is:

```text
device: /dev/serial0
baud:   921600
```

With the Cube connected and propellers removed, begin with read-only checks:

```bash
ssh pi5
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/mavlink/status.sh
./test_components/mavlink/health.sh
```

Do not continue to mode, servo, arm or motor tests until heartbeat and health
are understood. The complete guarded sequence is in:

```text
test_components/COMMANDS.md
```

## 10. Run Mission 1 And Mission 2

Mission Planner owns the uploaded AUTO route. Upload only one reviewed mission
at a time.

Mission 1, Figure 8:

```text
Upload Mission 1 from Mission Planner.
Do not run the target controller, or run the no-search profile for monitoring.
```

Optional no-search process on the Pi:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/run_mission1_no_search.sh
```

Mission 2, target payload:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/run_mission2_target_payload.sh
```

Before starting Mission 2:

1. Upload the reviewed Mission 2 AUTO route.
2. Set `mission.search_start_wp` in the selected profile.
3. Keep payload simulation enabled for non-release tests.
4. Verify camera, heartbeat, GPS/EKF, RC modes and failsafes.
5. Start the Pi process before the pilot arms and selects AUTO.

The Pi process does not arm, take off or choose the AUTO mission. It waits for
the required ArduPilot state.

## 11. Configuration Ownership

Set in Mission Planner / ArduPilot:

- AUTO waypoints and route;
- AUTO speed and altitude;
- takeoff and final mission items;
- flight modes, RC mapping, geofence and failsafes;
- motor order, direction and airframe parameters.

Set in the mission JSON profile:

- search start waypoint;
- target confirmation and center tolerance;
- bounded GUIDED centering behavior;
- camera source and dimensions;
- payload simulation, servo channel and PWM values;
- return behavior after completed targets.

Never tune the real configuration by guessing. Record one controlled test,
change one value and document the result.

## 12. Normal Git Workflow

Create a branch for each change:

```bash
git switch -c feature/short-description
```

Before committing:

```bash
git status --short
./scripts/check_project.sh
```

Commit only source, configuration templates and documentation:

```bash
git add path/to/files
git commit -m "Explain the change"
git push -u origin feature/short-description
```

Never commit:

- `.venv/`, caches or logs;
- private keys, passwords, hotspot details or `.env` secrets;
- unreviewed camera dumps;
- model weights until the team approves their release;
- external ArduPilot or Gazebo build directories.

See `CONTRIBUTING.md` for review expectations.

## 13. Troubleshooting

### `ssh pi5` fails on the Pi itself

The alias is for the laptop. If the terminal prompt already begins with
`pi5@raspberrypi5`, run the command directly without `ssh pi5`.

### Camera connector is CAM/DISP 1 but code uses index 0

The connector number and logical libcamera index are different. If only one
camera is detected, it is usually logical index `0`. Always verify with:

```bash
rpicam-hello --list-cameras
```

### Laptop camera window does not open through `ssh -Y`

Use the repository's laptop viewer. The camera runs on the Pi and encoded
frames are transported to the laptop; direct X forwarding is not supported by
the normal `rpicam` preview path.

### MAVLink shows several heartbeat sources

The ArduPilot vehicle heartbeat is normally system `1`, component `1`.
Mission Planner and router components may also send heartbeats. The provided
tools filter the vehicle heartbeat for mode confirmation.

### YOLO clone starts but cannot find a model

Check that `best.hef` exists on the Pi and `best.pt` exists for the Ubuntu
simulation. These files are not downloaded from GitHub by design.

## 14. Recommended Reading Order

1. `docs/ARCHITECTURE.md`
2. `docs/REAL_MISSION_FLOW.md`
3. `real_mission/parameter_config/README.md`
4. `test_components/COMMANDS.md`
5. `simulation/README.md`
6. `docs/SAFETY_AND_FAILSAFES.md`
7. `docs/REAL_DRONE_CHECKLIST.md`
8. YOLO branch `BEGINNER_GUIDE.md`

When a command and an old screenshot disagree, trust the current Git branch,
configuration file and test output. Ask the test lead before using a command
that can arm, move a servo or spin a motor.

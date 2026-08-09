# WD DRONE Target Mission V2

This is the tested mission engine. For normal real-drone operation, start in:

```text
real_mission/
```

If you are changing mission logic, this folder is where the implementation
lives. If you only remember one thing:

```text
mission_controller.py = mission state machine and control loop
vehicle.py            = MAVLink telemetry, commands, and parameters
vision.py             = target detection and tracking
camera_worker.py      = non-blocking latest-frame capture
video_stream.py       = non-blocking annotated MJPEG stream
payload.py            = target mapping and payload action state
safety.py             = guidance and payload release gates
overlay.py            = operator diagnostics drawn on video
control.py            = small tested math helpers
parameter_config.json = normal SITL/operator tuning
real_mission/parameter_config/mission2_target_payload.json = real Mission 2 profile
```

## Correct mission behavior

This controller targets the 2026 International UAV Competition Rotary Wing
Category second mission:

- find two unknown targets by image processing after the AUTO survey reaches the
  configured search waypoint;
- release the red payload on the blue regular hexagon;
- release the blue payload on the red equilateral triangle;
- release one payload per target, in whichever target order is detected first;
- keep the mission inside the 10 minute second-mission flight limit.

```text
AUTO survey controlled by QGC/ArduPilot
→ strict target confirmation
→ GUIDED
→ horizontal centering only
→ correct-colour payload event
→ AUTO resume
→ second target
→ RTL
```

QGC/ArduPilot owns AUTO altitude and speed. By default the companion
controller does not enforce RTL parameters and does not send vertical velocity
during GUIDED centering.

## MAVLink port

The program uses only:

```text
UDP 14551
```

Mission Planner must be disconnected before this program starts because both
cannot reliably bind the same UDP listening port.

The real Raspberry Pi/Cube profile uses:

```text
/dev/ttyAMA0
921600 baud
```

Camera input is selected with `camera.source`. SITL uses `udp_h264` from
`enable_camera`. The new Raspberry Pi Camera Module 3 profiles use
`picamera2`, which delivers image arrays directly to OpenCV with capture
metadata and a single-slot latest-frame buffer. The older `rpicam_mjpeg`
source remains available only as an explicit compatibility/debug fallback.

Use the camera-only profiles and commands in
`docs/OPENCV_PI5_TESTING.md` before selecting a real mission profile.

## Parameter policy

By default, parameter enforcement is disabled:

```json
"parameters": {
  "enforce": false
},
"control": {
  "altitude_control": "off"
}
```

Use QGC or ArduPilot parameters for waypoint speed, mission altitude, RTL
altitude, and acceleration limits.

## Operator configuration

`./run.sh` uses `parameter_config.json` by default. Edit this file for normal
SITL testing and bench tuning. It contains `_help` notes beside the important
tuning values.

Common values:

```json
"navigation": {
  "search_speed_source": "qgc_mission",
  "search_speed_m_s": 3.0
},
"control": {
  "center_max_speed_m_s": 0.35,
  "center_tolerance_px": 16.0,
  "center_tolerance_px_by_target": {
    "red_triangle": 26.0,
    "blue_hexagon": 16.0
  },
  "target_lost_timeout_s": 4.0,
  "reacquire_after_lost_s": 0.25
},
"safety": {
  "max_center_time_s": 120.0,
  "mode_retry_interval_s": 0.2
}
```

`search_speed_source` has two modes:

- `qgc_mission`: QGC/ArduPilot owns AUTO search speed. This is the default.
- `companion_do_change_speed`: the companion sends `MAV_CMD_DO_CHANGE_SPEED`
  with `search_speed_m_s` when SEARCH starts or resumes.

For real flights, prefer `qgc_mission` unless companion-owned AUTO speed is
intentional.

If the target is briefly lost during centering, the controller stays in GUIDED,
stops horizontal movement, searches the full frame for the same target, and keeps
trying to reacquire the same active target.

The named OpenCV profiles bound centering time, target loss, stale frames,
stagnation, and GUIDED displacement. Recovery behavior is profile-controlled.
These guards must be verified in SITL before any flight test; they do not
replace pilot control or Pixhawk failsafes.

The overlay shows `Guided bounces`. A normal AUTO resume after completing one
target does not increase this counter; only an unexpected AUTO report during
active centering does.

## Vision correction

The OpenCV detector uses one HSV/mask preprocessing pass, a low-resolution
colour-candidate stage, and high-resolution ROI geometry verification. Triangle
and hexagon decisions use weighted geometry scores rather than one exact polygon
vertex count. Initial confirmation records both hits and misses over time;
colour-only fallback is limited to tracking an already confirmed target.

The active flight backend is selected explicitly:

```json
"vision": {
  "backend": "strict_shape"
}
```

`strict_shape` is the production and supported vision backend in this
repository.

## Install

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission/target_mission_v2
chmod +x setup.sh run.sh
./setup.sh
```

Run the current complete test suite from the repository root:

```bash
./scripts/check_project.sh
```

## Run

Close Mission Planner, old detectors, and the GStreamer camera viewer.

At the SITL/MAVProxy prompt:

```text
enable_camera
```

Start the controller:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission/target_mission_v2
./run.sh
```

Default config:

```text
parameter_config.json
```

Equivalent explicit profile:

```bash
./run.sh configs/sim_gazebo.json
./run.sh operator_config.json
```

Starting profile for Raspberry Pi Camera Module 3:

```bash
./run.sh ../real_mission/parameter_config/mission2_target_payload.json
```

That real profile is for the Pi-to-Cube UART path. Use the SITL profile when
running only Gazebo on the Ubuntu laptop.

Before a real bench test, check the camera alone without MAVLink:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/camera/opencv_test.sh list
./test_components/camera/opencv_test.sh diagnostic --seconds 20
```

For the exact camera-only test sequence and the live annotated laptop window,
read:

```text
docs/OPENCV_PI5_TESTING.md
```

Start the mission from MAVProxy:

```text
mode guided
arm throttle
mode auto
```

## Expected output

```text
[PARAMETER] Enforcement disabled; QGC/ArduPilot parameters are left unchanged
[STATE] WAITING_FOR_AUTO -> SEARCH
[TARGET CONFIRMED] red_triangle hits=3/3
[MODE REQUEST] AUTO -> GUIDED
[STATE] WAITING_FOR_GUIDED -> CENTER
[STATE] CENTER -> PAYLOAD
[PAYLOAD] SIMULATED blue DROP over red_triangle ...
[MODE REQUEST] GUIDED -> AUTO
```

After both targets:

```text
[MODE REQUEST] GUIDED -> RTL
[STATE] WAITING_FOR_RTL -> COMPLETE
```

## Direction correction

Stop immediately if movement is reversed.

Forward/backward reverse:

```json
"image_y_to_forward_sign": 1.0
```

Left/right reverse:

```json
"image_x_to_right_sign": -1.0
```

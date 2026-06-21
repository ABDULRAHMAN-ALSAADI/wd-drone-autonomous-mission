# WD DRONE Target Mission V2

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
/dev/serial0
921600 baud
```

Camera input is selected with `camera.source`. SITL currently uses `udp_h264`
from `enable_camera`; custom GStreamer or direct camera-device sources are
reserved for real-camera setup after cooling is installed.

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
  "center_tolerance_px": 12.0,
  "target_lost_timeout_s": 4.0,
  "reacquire_after_lost_s": 0.25
},
"safety": {
  "guided_auto_bounce_grace_s": 8.0,
  "mode_retry_interval_s": 0.2
}
```

`search_speed_source` has two modes:

- `qgc_mission`: QGC/ArduPilot owns AUTO search speed. This is the default.
- `companion_do_change_speed`: the companion sends `MAV_CMD_DO_CHANGE_SPEED`
  with `search_speed_m_s` when SEARCH starts or resumes.

For real flights, prefer `qgc_mission` unless companion-owned AUTO speed is
intentional.

`guided_auto_bounce_grace_s` prevents one temporary AUTO heartbeat from causing
the controller to drop a target after GUIDED was requested. During this grace
period it keeps the target lock and retries GUIDED.

If the target is briefly lost during centering, the controller stays in GUIDED,
stops horizontal movement, searches the full frame for the same target, and only
returns to AUTO after `target_lost_timeout_s`.

The overlay shows `Guided bounces`. A normal AUTO resume after completing one
target does not increase this counter; only an unexpected AUTO report during
active centering does.

## Vision correction

Red triangle acceptance requires:

- red colour mask;
- at least two independent three-corner approximations;
- fewer than two four-corner approximations;
- contour extent at or below 0.72;
- triangle-compatible circularity and solidity;
- three spatially consistent detections inside 1.5 seconds.

Blue hexagon acceptance requires:

- blue colour mask;
- at least two 5–8-corner approximations;
- no square/rectangle extent;
- hexagon-compatible circularity and solidity;
- three spatially consistent detections inside 1.5 seconds.

The display shows explicit counts instead of an unclear accumulated score:

```text
red_triangle hits: 0/3
blue_hexagon hits: 0/3
```

The active flight backend is selected explicitly:

```json
"vision": {
  "backend": "strict_shape"
}
```

Future YOLO or AI HAT work should be added as a separate backend after the model
passes the safety gates in `docs/VISION_MODEL_PLAN.md`.

## Install

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission/target_mission_v2
chmod +x setup.sh run.sh
./setup.sh
```

Expected test result:

```text
Ran 49 tests
OK
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
./run.sh configs/real_pi_camera_module_3.json
```

That real profile is for the Pi-to-Cube UART path. Use the SITL profile when
running only Gazebo on the Ubuntu laptop.

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

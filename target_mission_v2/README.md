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

## Install

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission/target_mission_v2
chmod +x setup.sh run.sh
./setup.sh
```

Expected test result:

```text
Ran 32 tests
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

Equivalent explicit profile:

```bash
./run.sh configs/sim_gazebo.json
```

Starting profile for Raspberry Pi Camera Module 3:

```bash
./run.sh configs/real_pi_camera_module_3.json
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

## Replay vision frames

Vision replay and tuning are development tools in the root-level `vision_lab/`
folder. They are intentionally outside this mission runtime folder so the Pi can
stay lean.

Record a short session from the repo root:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
python3 vision_lab/vision_lab.py --config target_mission_v2/configs/sim_gazebo.json \
  record --label blue_hexagon --altitude-m 7 --frames 300
```

Use `--show` only when you need preview; recording without it is lighter.

Replay it:

```bash
python3 vision_lab/vision_lab.py list
python3 vision_lab/vision_lab.py --config target_mission_v2/configs/sim_gazebo.json replay \
  --input vision_lab/data/REAL_SESSION_FOLDER \
  --output-jsonl vision_lab/reports/session_report.jsonl
```

Use this before changing thresholds. The tests also include synthetic runway
rectangles so the detector keeps rejecting blue non-target shapes.

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

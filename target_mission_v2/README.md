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
AUTO survey at 5 m
→ strict target confirmation
→ GUIDED
→ centre while holding 5 m
→ correct-colour payload event at 5 m
→ AUTO resume
→ second target
→ RTL at 5 m
```

There is no descent phase and no post-payload climb phase.

## MAVLink port

The program uses only:

```text
UDP 14551
```

Mission Planner must be disconnected before this program starts because both
cannot reliably bind the same UDP listening port.

## RTL altitude correction

At startup the controller reads, sets when necessary, and verifies:

```text
RTL_ALT = 500 cm
RTL_CLIMB_MIN = 0 cm
MIS_RESTART = 0
```

This prevents ArduCopter's normal default RTL climb to 15 m.

The config stores these as centimetres because ArduPilot's parameters are
centimetre-based:

```json
"rtl_alt_cm": 500.0,
"rtl_climb_min_cm": 0.0
```

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
cd ~/FOR_COMP
unzip -o ~/Downloads/wd_drone_target_mission_v2.zip -d .

cd ~/FOR_COMP/wd_drone_target_mission_v2
chmod +x setup.sh run.sh
./setup.sh
```

Expected test result:

```text
Ran 21 tests
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
cd ~/FOR_COMP/wd_drone_target_mission_v2
./run.sh
```

Start the mission from MAVProxy:

```text
mode guided
arm throttle
mode auto
```

## Expected output

```text
[PARAMETER VERIFIED] RTL_ALT=500
[PARAMETER VERIFIED] RTL_CLIMB_MIN=0
[PARAMETER VERIFIED] MIS_RESTART=0
[STATE] WAITING_FOR_AUTO -> SEARCH
[TARGET CONFIRMED] red_triangle hits=3/3
[MODE REQUEST] AUTO -> GUIDED
[STATE] WAITING_FOR_GUIDED -> CENTER
[STATE] CENTER -> PAYLOAD
[PAYLOAD] SIMULATED blue DROP over red_triangle at 5.00 m
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

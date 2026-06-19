# Target Mission Operations

This is the active operating guide for `target_mission_v2`.

## Control Ownership

QGC and ArduPilot own AUTO mission altitude, waypoint speed, acceleration limits,
RTL behavior, arming, failsafes, and mission item order.

The companion controller owns only:

- target detection;
- target confirmation;
- switching to GUIDED after a confirmed target;
- low-speed horizontal centering in GUIDED;
- payload decision or simulated payload event;
- returning to AUTO after one target, or RTL after both targets.

The default config keeps vertical velocity disabled:

```json
"control": {
  "altitude_control": "off"
}
```

## Run Profiles

Default SITL profile:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission/target_mission_v2
./run.sh
```

Explicit Gazebo profile:

```bash
./run.sh configs/sim_gazebo.json
```

Real Raspberry Pi Camera Module 3 starting profile:

```bash
./run.sh configs/real_pi_camera_module_3.json
```

The real profile is a starting point, not a final calibration. Validate it with
recorded frames at the exact camera mount angle, lens, exposure, target size, and
flight altitude.

## Repeat Tests

After both targets are complete and RTL is confirmed, the process stays open.
If you start AUTO again from the search waypoint or later, the controller clears
the target-completion set and starts a new run.

The overlay keeps `MISSIONS DONE` in memory while the process remains open. If
you close the camera window and restart the program, the count starts from zero.

## Camera Window

The main window intentionally shows mission state, mode, current waypoint, target
lock, hit counts, completed targets, speed, vertical speed, total speed, and
acceleration.

Mask windows are disabled by default:

```json
"display": {
  "show_main_window": true,
  "show_masks": false
}
```

Turn masks on only when debugging HSV thresholds.

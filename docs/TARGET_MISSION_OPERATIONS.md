# Target Mission Operations

This is the active operating guide for the real mission wrapper in
`real_mission/` and the tested engine in `target_mission_v2/`.

## Control Ownership

QGC and ArduPilot own AUTO mission altitude, waypoint speed, acceleration limits,
RTL behavior, arming, failsafes, and mission item order.

The companion controller owns only:

- target detection;
- target confirmation;
- switching to GUIDED after a confirmed target;
- low-speed horizontal centering in GUIDED;
- payload decision or simulated payload event;
- returning to AUTO only after a successful first target payload;
- requesting RTL after both targets are complete.

The default config keeps vertical velocity disabled:

```json
"control": {
  "altitude_control": "off"
}
```

## Run Profiles

Real Raspberry Pi Camera Module 3 profile:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/run_mission2_target_payload.sh
```

SITL profile:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission/target_mission_v2
./run.sh
```

Explicit Gazebo profile:

```bash
./run.sh configs/sim_gazebo.json
```

Real profile through the engine directly:

```bash
./run.sh ../real_mission/parameter_config/mission2_target_payload.json
```

The real profile uses `/dev/serial0` at `921600` baud for the Cube UART link.
Use `parameter_config.json`, `operator_config.json`, or `configs/sim_gazebo.json`
for Ubuntu SITL.

The real profile is a starting point, not a final calibration. Validate it with
recorded frames at the exact camera mount angle, lens, exposure, target size, and
flight altitude.

The active vision backend is:

```json
"vision": {
  "backend": "strict_shape"
}
```

Keep this backend for SITL, MAVLink bench tests, and first real-camera checks.
Add YOLO or AI HAT inference only as a separate backend after the trained model
passes the safety gates in `docs/VISION_MODEL_PLAN.md`.

## Operator Config

Mission 2 uses:

```text
real_mission/parameter_config/mission2_target_payload.json
```

Mission 1 no-search monitoring uses:

```text
real_mission/parameter_config/mission1_no_search.json
```

`target_mission_v2/parameter_config.json` remains the normal SITL tuning file.

AUTO search speed is controlled by:

```json
"navigation": {
  "search_speed_source": "qgc_mission",
  "search_speed_m_s": 3.0
}
```

With `qgc_mission`, QGC/ArduPilot owns AUTO speed. With
`companion_do_change_speed`, the companion sends `MAV_CMD_DO_CHANGE_SPEED` when
SEARCH starts or resumes.

If you see search speed around 1.5-1.6 m/s while `qgc_mission` is selected, it
is coming from the QGC mission or ArduPilot waypoint navigation settings, not
from this controller.

GUIDED bounce protection is controlled by:

```json
"guided_auto_bounce_grace_s": null,
"max_guided_auto_bounces_per_target": null,
"active_target_abort_mode": "AUTO",
"mode_retry_interval_s": 0.2
```

If ArduPilot briefly reports AUTO after GUIDED was requested, the controller
keeps the target lock and immediately forces GUIDED again. With
`guided_auto_bounce_grace_s` and `max_guided_auto_bounces_per_target` set to
`null`, repeated AUTO bounces do not abort the target; the controller keeps
requesting GUIDED until centering and payload are finished.

The overlay shows `Guided bounces`. This counter increases only when the
controller is already centering a target and ArduPilot reports AUTO. It does not
count the normal AUTO resume after one target is complete.

Temporary target loss during centering is controlled by:

```json
"target_lost_timeout_s": 4.0,
"reacquire_after_lost_s": 0.25
```

During this window the controller stays in GUIDED, sends zero horizontal
velocity, and searches for the same target again.

## Repeat Tests

After both targets are complete and RTL is confirmed, the process stays open.
If you start AUTO again from the search waypoint or later, the controller clears
the target-completion set and starts a new run.

The overlay keeps `MISSIONS DONE` in memory while the process remains open. If
you close the camera window and restart the program, the count starts from zero.

## Camera Window

The main window intentionally shows mission state, mode, current waypoint, target
lock, hit counts, completed targets, speed owner, speed, vertical speed, and
acceleration in a compact panel.

Mask windows are disabled by default:

```json
"display": {
  "show_main_window": true,
  "show_masks": false
}
```

Turn masks on only when debugging HSV thresholds.

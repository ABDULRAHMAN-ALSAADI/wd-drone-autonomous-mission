# Real Mission

This folder is the operator-facing place for the real drone mission.

The tested Python engine still lives in `target_mission_v2/` so the old SITL
tests and imports stay stable. Use this folder when preparing the Raspberry Pi
for the aircraft.

## What Runs On The Drone

For Mission 2, run this on the Raspberry Pi 5:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/run_mission2_target_payload.sh
```

The Pi connects to the Cube on `/dev/serial0`, reads the Pi Camera Module 3,
waits for the AUTO mission to reach the configured search waypoint, searches for
the blue hexagon and red triangle, centers in GUIDED, triggers the payload servo
when enabled, resumes AUTO after the first target, and requests RTL after both
targets are complete.

## Mission 1 vs Mission 2

This is the important safety rule:

```text
Mission 1 = ArduPilot AUTO only, no Pi search/payload logic.
Mission 2 = ArduPilot AUTO + Pi vision/search/payload logic.
```

For Mission 1, normally do not run the target payload controller at all. Upload
the Figure 8 mission from Mission Planner/QGC and start AUTO from the RC.

If you want the Pi process open during Mission 1 for bench monitoring practice,
use the no-search profile:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/run_mission1_no_search.sh
```

That profile has:

```json
"mission": {
  "name": "mission1_figure8_no_search",
  "search_enabled": false
}
```

Even if the AUTO mission reaches waypoint `5`, `7`, or any search-like number,
the Pi will not enter SEARCH.

For Mission 2, use:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/run_mission2_target_payload.sh
```

That profile has:

```json
"mission": {
  "name": "mission2_target_payload",
  "search_enabled": true
}
```

The RC should control ArduPilot modes or mission start. The companion computer
does not upload or choose between the two missions in the air.

Real sequence:

1. Upload the correct AUTO mission from Mission Planner/QGC.
2. Start the matching Pi profile, or no Pi target controller for Mission 1.
3. Use RC/Mission Planner to arm and start AUTO.
4. The Pi waits quietly until:
   - the vehicle is armed;
   - mode is `AUTO`;
   - mission item is at or after `mission.search_start_wp`.
   - search is enabled by the selected mission profile.
5. Then, only for Mission 2, the Pi starts vision/search/centering/payload.

The Cube normally has one uploaded AUTO mission at a time. If you want one RC
button for mission one and another RC button for mission two, that is an
ArduPilot/Mission Planner/Lua mission-management design. The clean first
version is: upload the mission you want on the ground, start the matching Pi
profile, then use RC to start AUTO.

## Optional Mission 2 RC Enable Switch

You can add one extra safety lock for Mission 2: an RC channel that must be high
before search can start.

Example for RC channel 7:

```json
"mission": {
  "search_enable_rc_channel": 7,
  "search_enable_pwm_min": 1700
}
```

Then the Pi will require all of this before search:

```text
armed == true
mode == AUTO
waypoint >= search_start_wp
RC7 >= 1700
```

If the switch is low, the overlay/logs say search is blocked. This is a safety
enable, not a mission selector.

Find the real AT9S Pro channel before enabling this:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/mavlink/rc_channels.sh --seconds 30 --channels 5 6 7 8
```

Flip one switch at a time. The changed channel is marked with `*`.

## Active Target Abort Safety

If Mission 2 has already confirmed a target and the vehicle repeatedly leaves
GUIDED before the payload step is finished, the Pi should not quietly let AUTO
continue the route.

The real config now uses:

```json
"safety": {
  "max_guided_auto_bounces_per_target": null,
  "camera_frame_timeout_s": 2.0,
  "active_target_abort_mode": "AUTO"
}
```

So `GUIDED -> AUTO -> GUIDED` bounces are retried without dropping the target
lock. The Pi keeps requesting GUIDED until the target is centered and payload is
finished.

If the pilot or failsafe changes the vehicle to LOITER, STABILIZE, RTL, LAND, or
another non-mission mode, the Pi stands down, sends zero velocity, clears the
target lock, and waits for AUTO. That keeps the companion from overriding a real
safety decision.

## Laptop Camera Window

The camera window is not opened by the RC and it is not sent through RFD900x.
RFD900x is for MAVLink telemetry, not video.

To see what the drone camera sees, the laptop and Pi must be on the same Wi-Fi
or hotspot network. Start this from the Ubuntu laptop before takeoff:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/open_laptop_camera_window.sh
```

This opens a laptop OpenCV window. The Pi only streams frames. The mission does
not depend on the laptop window, so if the Wi-Fi video drops, the Pi can still
continue the mission.

## Tunable Parameters

Edit:

```text
real_mission/parameter_config/mission2_target_payload.json
```

Important values:

- `mission.name`: tells the operator which mission profile is running.
- `mission.search_enabled`: false means the Pi will never enter target search.
- `mission.search_start_wp`: first AUTO waypoint where search starts.
- `mission.search_enable_rc_channel`: optional extra RC switch gate for Mission
  2 search.
- `payload.simulate_only`: keep `true` until servo tests pass; set `false` only
  when the payload mechanism is physically ready.
- `payload.servo_channel`, `payload.release_pwm`, `payload.reset_pwm`: payload
  output settings.
- `control.center_max_speed_m_s`: max horizontal speed during GUIDED centering.
- `control.center_tolerance_px`: pixel error accepted as centered.
- `vision.search_min_area_px`: target size threshold for the real camera.
- `camera.width`, `camera.height`, `camera.framerate`: Camera Module 3 stream
  settings.

Read `parameter_config/README.md` before changing values.

## Safety Defaults

The default real-drone config keeps:

```json
"payload": {
  "simulate_only": true
},
"display": {
  "show_main_window": false
}
```

That means the Pi can run headless and will not physically drop payload until
you intentionally enable it after bench tests.

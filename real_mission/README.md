# Real Mission

This folder is the operator-facing place for the real drone mission.

The tested Python engine still lives in `target_mission_v2/` so the old SITL
tests and imports stay stable. Use this folder when preparing the Raspberry Pi
for the aircraft.

## What Runs On The Drone

Run this on the Raspberry Pi 5:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/run_real_mission.sh
```

The Pi connects to the Cube on `/dev/serial0`, reads the Pi Camera Module 3,
waits for the AUTO mission to reach the configured search waypoint, searches for
the blue hexagon and red triangle, centers in GUIDED, triggers the payload servo
when enabled, resumes AUTO after the first target, and requests RTL after both
targets are complete.

## RC Trigger Reality

The RC should control ArduPilot modes or mission start. The companion computer
does not need a special RC button to begin searching.

Real sequence:

1. Upload the correct AUTO mission from Mission Planner/QGC.
2. Start `./real_mission/run_real_mission.sh` on the Pi before takeoff.
3. Use RC/Mission Planner to arm and start AUTO.
4. The Pi waits quietly until:
   - the vehicle is armed;
   - mode is `AUTO`;
   - mission item is at or after `mission.search_start_wp`.
5. Then the Pi starts the second-mission vision/search/centering/payload logic.

The Cube normally has one uploaded AUTO mission at a time. If you want one RC
button for mission one and another RC button for mission two, that is an
ArduPilot/Mission Planner/Lua mission-management design, not just a Python
vision setting. The clean first version is: upload the mission you want, then
use RC to start AUTO.

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
real_mission/parameter_config/real_drone.json
```

Important values:

- `mission.search_start_wp`: first AUTO waypoint where search starts.
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

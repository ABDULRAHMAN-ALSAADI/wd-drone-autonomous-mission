# Real Drone Checklist

Use this before moving from SITL to Cube Orange Plus and Raspberry Pi 5.

## Bench

- QGC mission altitude and speed are set correctly.
- `operator_config.json` uses `navigation.search_speed_source: "qgc_mission"`
  unless companion-owned AUTO speed is intentionally required.
- ArduPilot failsafes, RTL altitude, geofence, and battery failsafe are verified.
- Companion parameter enforcement remains disabled unless intentionally enabled.
- Payload config remains `simulate_only: true`.
- Camera stream opens without old viewers holding the port.
- Telemetry link through RFD900x is stable.

## Tether Or Props-Off

- Verify mode transitions: AUTO, GUIDED, AUTO, RTL.
- Verify servo channel and PWM values with payload disconnected first.
- Verify the camera overlay shows waypoint, speed, acceleration, target state,
  and mission count.
- Verify closing and reopening the program resets the in-memory mission count.

## First Flight

- Use conservative QGC speed.
- Keep payload simulation enabled.
- Use only one target first.
- Abort if the centering direction is wrong.
- Abort if the detector locks on anything except the actual target shape.

## Payload Enable

Enable physical payload only after:

- SITL completes both targets repeatedly;
- automated vision tests reject non-target rectangles;
- real-camera recordings pass offline;
- low-speed flight centering is correct;
- servo release and reset are verified on the bench.

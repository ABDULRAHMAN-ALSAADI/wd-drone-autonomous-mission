# WD Drone Autonomous Mission

Autonomous rotary-wing UAV mission software for the 2026 UAV competition.

Start here:

```text
START_HERE.md
```

## Simple Folder Map

| Folder | Purpose |
| --- | --- |
| `real_mission/` | Real Pi 5 + Cube Orange mission and real-drone parameter config. |
| `test_components/` | Bench commands for camera, MAVLink, servo, motor, and software checks. |
| `target_mission_v2/` | Internal tested mission engine used by `real_mission/`. |
| `scripts/` and `tools/` | Internal helpers used by the test wrappers. |
| `docs/` | Safety notes, wiring notes, and longer explanations. |

## Run The Real Mission

On the Raspberry Pi:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/run_real_mission.sh
```

Edit the real-drone tuning file:

```text
real_mission/parameter_config/real_drone.json
```

## Test Before Flight

Read:

```text
test_components/COMMANDS.md
```

Run all local software checks:

```bash
./test_components/software/run_all_checks.sh
```

Run the live laptop camera window:

```bash
./real_mission/open_laptop_camera_window.sh
```

Run MAVLink health on the Pi:

```bash
./test_components/mavlink/status.sh
./test_components/mavlink/health.sh
```

Run the guarded avionics sequence dry-run:

```bash
./test_components/mavlink/bench_sequence.sh --dry-run
```

Real armed bench sequence, propellers removed only:

```bash
./test_components/mavlink/bench_sequence.sh --i-understand-props-off --i-accept-arming
```

## Mission Behavior

The Pi waits for ArduPilot to be armed, in `AUTO`, and at or after the configured
search waypoint. Then it:

1. searches for the blue hexagon and red triangle;
2. requests `GUIDED`;
3. centers over the confirmed target;
4. drops the correct payload when physical payload is enabled;
5. resumes `AUTO` for the next target;
6. requests `RTL` after both targets are complete.

By default, QGC/Mission Planner and ArduPilot own AUTO altitude and AUTO speed.
The Pi only controls low-speed horizontal centering in GUIDED after a confirmed
target.

## Camera Monitoring

The laptop camera window needs Wi-Fi or hotspot access to the Pi:

```bash
./real_mission/open_laptop_camera_window.sh
```

RFD900x is MAVLink telemetry, not video. The mission can continue without the
laptop window.

## Safety

Keep this default until bench tests pass:

```json
"payload": {
  "simulate_only": true
}
```

Do not run arm, servo, or motor tests with propellers installed.

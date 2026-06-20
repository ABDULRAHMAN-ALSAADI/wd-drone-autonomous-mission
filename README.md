# WD DRONE Autonomous Mission

Autonomous rotary-wing UAV mission software for the 2026 TÜBİTAK/TEKNOFEST UAV competition.

The flight stack is divided into two systems:

- **Cube Orange Plus / ArduPilot:** flight control, navigation, failsafes and payload outputs.
- **Raspberry Pi 5:** mission supervision, computer vision, target centering and payload decisions.

## Active target mission

The active SITL/Gazebo controller for the rotary-wing second mission is in:

```text
target_mission_v2/
```

It performs target detection, GUIDED centering, simulated payload release, AUTO
resume, repeat-run reset, and RTL after both targets. QGC/ArduPilot owns AUTO
altitude and AUTO speed by default; the companion controller only controls
horizontal centering velocity after target lock.

Run it with:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission/target_mission_v2
./run.sh
```

The default operator-facing config is:

```text
target_mission_v2/operator_config.json
```

Use an explicit profile when needed:

```bash
./run.sh configs/sim_gazebo.json
./run.sh configs/real_pi_camera_module_3.json
```

Useful mission docs:

- `docs/TARGET_MISSION_OPERATIONS.md`
- `docs/CAMERA_CALIBRATION.md`
- `docs/SITL_TEST_PLAN.md`
- `docs/REAL_DRONE_CHECKLIST.md`
- `docs/RASPBERRY_PI_PIXHAWK_MAVLINK.md`
- `docs/TEAM_PI_WORKFLOW.md`
- `docs/VISION_MODEL_PLAN.md`

## Phase 2 observer

Phase 2 is read-only. It:

- Connects to SITL or the future Cube UART profile
- Reads vehicle telemetry
- Reads the current AUTO mission waypoint
- Detects when the configured search waypoint is reached
- Runs a deterministic mission-state observer
- Logs state transitions and waypoint events to JSON Lines
- Sends no flight commands

## Install

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
chmod +x scripts/*.sh
./scripts/setup.sh
```

## Raspberry Pi Team Workflow

Sync the Ubuntu laptop source tree to the Pi without deleting Pi files:

```bash
DRY_RUN=1 ./scripts/sync_to_pi.sh
./scripts/sync_to_pi.sh
```

Prepare and validate the Pi copy without running hardware commands:

```bash
./scripts/pi_validate.sh
```

See `docs/TEAM_PI_WORKFLOW.md` for teammate access, editing, sync, and safe
MAVLink bench-test rules.

## Configure the search waypoint

Edit:

```text
config/settings.json
```

The important fields are:

```json
{
  "search_start_waypoint": 2,
  "mission_complete_waypoint": 999
}
```

`search_start_waypoint` is the first mission item at which computer-vision search is considered active.

`mission_complete_waypoint` is a temporary upper threshold. It remains `999` until the final mission item numbering is fixed.

## Start the simulation

Terminal 1:

```bash
drone
```

Terminal 2:

```bash
sitl
```

Terminal 3:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/run_sitl_observer.sh
```

Expected initial output:

```text
MISSION_STATE STARTUP -> WAITING_FOR_ARM: vehicle is disarmed
mission=WAITING_FOR_ARM link=OK sys=1 mode=STABILIZE state=DISARMED wp=0 ...
```

During an AUTO mission, the observer changes through states such as:

```text
WAITING_FOR_ARM
WAITING_FOR_AUTO
AUTO_TRANSIT
SEARCH_ACTIVE
MISSION_COMPLETE
```

## Event log

Mission events are stored at:

```text
logs/mission_events.jsonl
```

View the latest events:

```bash
tail -n 20 logs/mission_events.jsonl
```

Each line is valid JSON and contains a UTC timestamp, event type, mission state, mode, arm status and waypoint data.

## Tests

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/check_project.sh
```

GitHub Actions runs these tests on every push.

Clean local ignored caches and optional runtime logs:

```bash
./scripts/clean_workspace.sh
./scripts/clean_workspace.sh --logs
```

## Safety boundary

The Phase 2 observer does not arm, disarm, change flight mode, move the
aircraft, alter the mission or actuate payload outputs.

The active target mission controller can request GUIDED/AUTO/RTL and can send
low-speed horizontal centering velocity only after target confirmation. AUTO
altitude and AUTO speed stay under QGC/ArduPilot control by default.

# Start Here

This project has three jobs:

1. Run the rotary-wing second mission in simulation and later on the real drone.
2. Test the Raspberry Pi 5 to Cube Orange MAVLink link safely on the bench.
3. Give the team clear checklists for wiring, safety, monitoring, and tuning.

## The Important Folders

| Folder | Purpose | Edit Often? |
| --- | --- | --- |
| `target_mission_v2/` | The active mission controller: camera, vision, GUIDED centering, payload decision, AUTO resume, RTL. | Yes, for mission behavior and tuning. |
| `target_mission_v2/configs/` | Real and simulation mission profiles. | Yes, carefully. |
| `scripts/` | Commands you run from the terminal: setup, sync, Pi checks, bench tests. | Sometimes. |
| `tools/` | Developer/bench helper programs used by scripts. | Rarely. |
| `src/wd_drone/` | Read-only telemetry observer/monitor package. It sends no flight commands. | Rarely. |
| `tests/` | Unit tests for the package monitor/config logic. | Only when behavior changes. |
| `docs/` | Operating guides, safety checklists, mission notes, and test-day plans. | Yes, whenever the team learns something. |
| `config/` | Settings for the read-only observer, not the active target mission. | Rarely. |

## What To Run

Simulation mission:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission/target_mission_v2
./run.sh
```

Pi/Cube bench readiness:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/pi_test_day_readiness.sh
```

Pi/Cube health monitor:

```bash
ssh pi5
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/mavlink_bench.sh health --connection /dev/serial0 --baud 921600 --seconds 10
```

Full local tests:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/check_project.sh
```

## What To Edit For Normal Mission Tuning

Use this first:

```text
target_mission_v2/parameter_config.json
```

Real Pi/Cube starting profile:

```text
target_mission_v2/configs/real_pi_camera_module_3.json
```

Do not change source code just to adjust waypoint start, centering speed,
target hit count, display font size, or payload simulation. Those belong in the
JSON config files.

## What Not To Touch Before Asking

- `.venv/`
- `.wheelhouse/`
- `__pycache__/`
- `.git/`
- ArduPilot parameters on the real Cube
- Motor or servo commands with propellers installed
- `payload.simulate_only: false`

## Best Reading Order

1. `docs/PROJECT_STRUCTURE.md`
2. `docs/COMPETITION_REQUIREMENTS.md`
3. `docs/SAFETY_AND_FAILSAFES.md`
4. `docs/PIXHAWK_PI_TEST_DAY.md`
5. `target_mission_v2/README.md`

# Start Here

This repo has two folders you should care about first:

| Folder | Use it for |
| --- | --- |
| `real_mission/` | The real Raspberry Pi 5 + Cube Orange mission. Run this on the aircraft. |
| `test_components/` | Camera, MAVLink, servo, motor, Pi, and software checks before flight. |

Everything else is support code, tests, or documentation behind those two
folders.

## Real Mission

Run on the Raspberry Pi:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/run_real_mission.sh
```

Tune the real drone here:

```text
real_mission/parameter_config/real_drone.json
```

Read the tuning notes here:

```text
real_mission/parameter_config/README.md
```

The Pi mission waits until ArduPilot is armed, in `AUTO`, and at or after
`mission.search_start_wp`. Then it starts target search, switches to `GUIDED`
for centering, triggers payload when enabled, resumes `AUTO`, and requests `RTL`
after both targets are complete.

## Test Components

Start here:

```text
test_components/COMMANDS.md
```

Useful examples:

```bash
./test_components/software/run_all_checks.sh
./test_components/camera/check_on_pi.sh --seconds 10
./test_components/mavlink/status.sh
./test_components/mavlink/health.sh
./test_components/mavlink/bench_sequence.sh --dry-run
```

The avionics bench sequence requested for testing is:

```text
STABILIZE -> GUIDED -> ARM -> AUTO -> RTL -> STABILIZE -> DISARM
```

Run it only with propellers removed:

```bash
./test_components/mavlink/bench_sequence.sh --i-understand-props-off --i-accept-arming
```

## Laptop Camera Window

The live camera window opens on the Ubuntu laptop, not on the Pi:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/open_laptop_camera_window.sh
```

The laptop and Pi must be on the same Wi-Fi/hotspot network. RFD900x carries
MAVLink telemetry, not video.

The mission does not depend on this window. It is only for you to watch what the
camera sees.

## What The Other Folders Are

| Folder | Why it exists |
| --- | --- |
| `target_mission_v2/` | Tested mission engine used by `real_mission/run_real_mission.sh`. |
| `scripts/` | Low-level shell helpers used by the clean test wrappers. |
| `tools/` | Python bench tools behind the shell commands. |
| `src/wd_drone/` | Read-only telemetry observer package. It sends no flight commands. |
| `tests/` | Unit tests for monitor/config code. |
| `docs/` | Longer explanations and checklists. |
| `config/` | Config for the read-only observer, not the active mission. |

Do not edit `.venv/`, `.git/`, `__pycache__/`, or logs.

## Best Reading Order

1. `real_mission/README.md`
2. `real_mission/parameter_config/README.md`
3. `test_components/COMMANDS.md`
4. `docs/SAFETY_AND_FAILSAFES.md`
5. `docs/PIXHAWK_PI_TEST_DAY.md`

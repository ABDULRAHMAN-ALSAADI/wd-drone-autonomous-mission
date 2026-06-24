#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

SECONDS_TO_RUN="${SECONDS_TO_RUN:-8}"

echo "[PREFLIGHT] project=$ROOT"
echo "[PREFLIGHT] commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo

echo "[1/5] Software checks"
./test_components/software/run_all_checks.sh
echo

echo "[2/5] System health"
if command -v vcgencmd >/dev/null 2>&1; then
    vcgencmd measure_temp || true
    vcgencmd get_throttled || true
else
    echo "[INFO] vcgencmd not available on this machine"
fi
df -h /
echo

echo "[3/5] Camera check"
if [[ "${SKIP_CAMERA:-0}" == "1" ]]; then
    echo "[SKIP] camera check skipped by SKIP_CAMERA=1"
else
    ./test_components/camera/check_on_pi.sh --seconds "$SECONDS_TO_RUN"
fi
echo

echo "[4/5] MAVLink read-only status"
if [[ "${SKIP_MAVLINK:-0}" == "1" ]]; then
    echo "[SKIP] MAVLink status skipped by SKIP_MAVLINK=1"
else
    SECONDS_TO_RUN="$SECONDS_TO_RUN" ./test_components/mavlink/status.sh
    SECONDS_TO_RUN="$SECONDS_TO_RUN" ./test_components/mavlink/health.sh
fi
echo

echo "[5/5] Real mission safety config"
python3 - <<'PY'
import json
from pathlib import Path

path = Path("real_mission/parameter_config/real_drone.json")
cfg = json.loads(path.read_text(encoding="utf-8"))

checks = [
    ("mavlink.connection", cfg["mavlink"]["connection"] == "/dev/serial0"),
    ("mavlink.baud", int(cfg["mavlink"]["baud"]) == 921600),
    ("payload.servo_channel", int(cfg["payload"]["servo_channel"]) == 5),
    ("payload.simulate_only", cfg["payload"]["simulate_only"] is True),
    ("control.altitude_control", cfg["control"]["altitude_control"] == "off"),
    ("navigation.search_speed_source", cfg["navigation"]["search_speed_source"] == "qgc_mission"),
]

failed = False
for name, ok in checks:
    print(f"{'[OK]' if ok else '[FAIL]'} {name}")
    failed = failed or not ok

if failed:
    raise SystemExit("Real mission safety config is not in first-flight-safe state")
PY

echo
echo "[PREFLIGHT OK] read-only checks completed"


#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$ROOT/real_mission/parameter_config/real_drone.json"
cd "$ROOT"

python_bin="$ROOT/.venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
    python_bin="python3"
fi

channel="$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["payload"]["servo_channel"])' "$CONFIG")"
release_pwm="$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["payload"]["release_pwm"])' "$CONFIG")"
reset_pwm="$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["payload"]["reset_pwm"])' "$CONFIG")"
hold_s="$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["payload"]["release_hold_s"])' "$CONFIG")"

exec ./scripts/mavlink_bench.sh servo \
    --connection /dev/serial0 \
    --baud 921600 \
    --channel "$channel" \
    --pwm "$release_pwm" \
    --reset-pwm "$reset_pwm" \
    --hold "$hold_s" \
    --i-understand-props-off

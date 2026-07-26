#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

python_bin="$ROOT/.venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
    python_bin="python3"
fi

exec env PYTHONPATH="$ROOT/target_mission_v2" \
    "$python_bin" tools/opencv_servo_bench_test.py "$@"

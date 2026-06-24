#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${1:-$ROOT/real_mission/parameter_config/mission2_target_payload.json}"

pick_python() {
    for candidate in "$ROOT/.venv/bin/python" python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" - <<'PY' >/dev/null 2>&1
import cv2
import numpy
import pymavlink
PY
        then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(pick_python)" || {
    echo "No Python with cv2, numpy, and pymavlink found. Run ./scripts/setup.sh or ./scripts/pi_validate.sh first." >&2
    exit 1
}

cd "$ROOT"
exec "$PYTHON_BIN" target_mission_v2/mission_controller.py --config "$CONFIG_PATH"

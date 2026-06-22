#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

pick_python() {
    for candidate in "${PYTHON_BIN:-}" .venv/bin/python python3; do
        [[ -n "$candidate" ]] || continue
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" - <<'PY' >/dev/null 2>&1
import cv2
import numpy
PY
        then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(pick_python)" || {
    echo "No Python with cv2/numpy found. Run target_mission_v2/setup.sh on Ubuntu, or install python3-opencv/python3-numpy on the Pi." >&2
    exit 1
}

exec "$PYTHON_BIN" tools/pi_camera_check.py "$@"

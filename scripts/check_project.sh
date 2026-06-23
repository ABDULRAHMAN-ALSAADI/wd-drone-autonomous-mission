#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

pick_python() {
    for candidate in .venv/bin/python python3; do
        path="$(command -v "$candidate" 2>/dev/null || true)"
        if [[ -n "$path" ]] && "$path" - <<'PY' >/dev/null 2>&1
import cv2
import numpy
import pymavlink
PY
        then
            case "$path" in
                /*) printf '%s\n' "$path" ;;
                *) printf '%s/%s\n' "$PWD" "$path" ;;
            esac
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(pick_python)" || {
    echo "No Python environment has cv2, numpy, and pymavlink. Run ./scripts/setup.sh or target_mission_v2/setup.sh first." >&2
    exit 1
}

PYTHONPATH=src "$PYTHON_BIN" -m unittest discover -s tests -v
(
    cd target_mission_v2
    "$PYTHON_BIN" -m unittest -v test_mission_controller.py
)
"$PYTHON_BIN" -m py_compile target_mission_v2/*.py src/wd_drone/*.py tools/*.py

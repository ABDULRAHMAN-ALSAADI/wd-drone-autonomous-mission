#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

pick_python() {
  for candidate in ../.venv/bin/python python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" - <<'PY' >/dev/null 2>&1
import cv2
import numpy
import pymavlink
PY
    then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(pick_python)" || {
  echo "No Python environment has cv2, numpy, and pymavlink. Run ./setup.sh first." >&2
  exit 1
}

CONFIG_PATH="${1:-mission_config.json}"
exec "$PYTHON_BIN" mission_controller.py --config "$CONFIG_PATH"

#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

host="$(hostname 2>/dev/null || true)"
if [[ "${ALLOW_PI_LOCAL_CAMERA_WINDOW:-0}" != "1" ]] && { [[ "$host" == "raspberrypi5" ]] || [[ "${USER:-}" == "pi5" ]]; }; then
    cat >&2 <<'EOF'
This live camera window must be started from the Ubuntu laptop, not from the Raspberry Pi terminal.

Why:
  Raspberry Pi OS Lite has no desktop window.
  The Pi only streams Camera Module 3 frames.
  The Ubuntu laptop opens the OpenCV window and runs the detector overlay.

Do this:
  1. Leave the Pi shell:
       exit

  2. On the Ubuntu laptop terminal:
       cd ~/FOR_COMP/wd-drone-autonomous-mission
       ./real_mission/open_laptop_camera_window.sh

For a no-window Pi-side camera health check only:
       ./scripts/pi_camera_check.sh --seconds 10
EOF
    exit 2
fi

if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
    cat >&2 <<'EOF'
No desktop display was found.

Run this from the Ubuntu laptop's graphical terminal, not from SSH into the Pi.
EOF
    exit 2
fi

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
    echo "No laptop Python with cv2/numpy found. Run ./scripts/setup.sh first." >&2
    exit 1
}

exec "$PYTHON_BIN" tools/pi_camera_live_view.py "$@"
